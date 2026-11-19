"""④ 判定・集計 (F-06, F-07)。ここは LLM を使わず、Python のルールだけで数字を決める。

1回答ごとの判定:
- 対象が出たか: 回答文 (正規化) の中に、会社名・別名があるかを Python が探す。LLM の抽出結果は使わない。
- 出た会社の並び: LLM が抜き出した名前のうち、回答文に本当に書いてあるものだけを残し、
  回答文の中で最初に出た位置の順に並べ直す。書いていない名前 (LLMの作り話) は捨てて記録する。
- 順位: 対象が最初に出た位置より前に出た、ほかの会社の数 + 1。
- 自社サイトの引用: 出典のドメインが、対象のドメイン (サブドメインを含む) と一致するか。
- 要確認: 別の語の一部として見つかった (「ミドリムシ」など)、ambiguous_aliases だけで見つかった、
  または AI の抽出結果に対象が入っていない (文字列としては出たが、会社として挙げられていない)。
  要確認でも「出た」には数える。レポートで人が確かめる。
"""
from __future__ import annotations

from collections import Counter

from .normalize import domain_matches, find_first, nfkc, norm_name, norm_text
from .surfaces import has_search


def target_keys(target: dict) -> list[str]:
    keys = [norm_name(x) for x in [target["name"], *target["aliases"]]]
    return sorted({k for k in keys if k}, key=len, reverse=True)


def relation(name: str, target: dict) -> str:
    """抜き出した名前と対象の関係。

    "target"  : 対象そのもの (正規化して会社名・別名と一致)
    "related" : 対象の名前を含む別の名前 (「ミドリ電機」「〇〇 AIO Insight」など)。別の会社か、
                対象のサービス名か、プログラムでは決めきれないので、競合にも対象にも数えず「要確認」にする
    "other"   : それ以外 (競合)
    """
    n = norm_name(name)
    keys = target_keys(target)
    if not n:
        return "other"
    if n in keys:
        return "target"
    if any(k in n for k in keys):
        return "related"
    return "other"


def is_target_name(name: str, target: dict) -> bool:
    return relation(name, target) == "target"


def judge_one(answer: dict, extracted: dict | None, target: dict) -> dict:
    text_n, idx = norm_text(answer["answer"])
    keys = target_keys(target)
    amb = {norm_name(x) for x in target.get("ambiguous_aliases", [])} - {norm_name(target["name"])}
    src = nfkc(answer["answer"])

    names, dropped = [], []
    seen = set()
    for raw in (extracted or {}).get("names", []):
        k = norm_name(raw)
        if not k or k in seen:
            continue
        p = text_n.find(k)
        if p < 0:
            dropped.append(raw)  # 回答文に無い名前
            continue
        seen.add(k)
        names.append((p, raw, k))
    names.sort()
    rel = {raw: relation(raw, target) for _, raw, _ in names}
    related = [raw for _, raw, _ in names if rel[raw] == "related"]

    # 対象の名前を探す。対象の名前を含む別の名前 (related) の中は、いったん隠して探す
    masked = text_n
    for _, raw, k in names:
        if rel[raw] == "related":
            masked = masked.replace(k, "\x00" * len(k))
    hit = find_first(masked, keys, src, idx)
    review = []
    if hit is None and related:
        hit = find_first(text_n, keys, src, idx)
        if hit:
            review.append(f"対象の名前は「{'」「'.join(related)}」の一部としてだけ出ています（別の会社・サービスの可能性）")
    elif related:
        review.append(f"対象の名前を含む別の名前があります: {'、'.join(related)}")
    if hit:
        pos, matched, glued = hit
        if glued:
            review.append(f"「{matched}」が別の語の一部として出ています")
        if matched in amb:
            review.append(f"紛らわしい別名「{matched}」でだけ見つかりました")

    others = [(p, raw, k) for p, raw, k in names if rel[raw] == "other"]
    rank = None
    if hit:
        rank = 1 + sum(1 for p, _, _ in others if p < hit[0])
        # 2つの方法の食い違い: 文字列は見つかったが、AIの抽出では会社として挙がっていない
        if extracted and extracted.get("parse_ok", True) and not any(v == "target" for v in rel.values()) \
                and not related:
            review.append("AIの抽出では、対象は会社として挙がっていません（地名・一般名詞の可能性）")

    sources = answer.get("sources") or []
    cited = any(domain_matches(s.get("domain") or s.get("url", ""), target["domains"])
                for s in sources) if target["domains"] else False

    return {
        "question_id": answer["question_id"], "mode": answer["mode"], "repeat": answer.get("repeat", 1),
        "engine": answer.get("engine"),
        "mentioned": bool(hit), "rank": rank,
        "matched": hit[1] if hit else None,
        "needs_review": bool(review), "review_reasons": review,
        "companies": [raw for _, raw, _ in names],
        "competitors": [raw for _, raw, _ in others],
        "dropped_by_check": dropped,
        "extract_ok": None if extracted is None else extracted.get("parse_ok", True),
        "cited_own_site": cited,
        "source_domains": sorted({s.get("domain") for s in sources if s.get("domain")}),
    }


def summarize(mentions: list[dict], answers: list[dict], target: dict, top: int = 10,
              order: list[str] | None = None) -> dict:
    by_mode: dict[str, dict] = {}
    present = {m["mode"] for m in mentions}
    modes = [m for m in (order or []) if m in present] + sorted(present - set(order or []))
    no_ans = {(a["question_id"], a["mode"], a.get("repeat", 1)) for a in answers if a.get("no_answer")}
    for mode in modes:
        ms = [m for m in mentions if m["mode"] == mode]
        n = len(ms)
        hit = [m for m in ms if m["mentioned"]]
        ranks = [m["rank"] for m in hit if m["rank"]]
        top3 = [m for m in hit if m["rank"] and m["rank"] <= 3]
        first = [m for m in hit if m["rank"] == 1]
        comp = Counter()
        label = {}
        for m in ms:
            for c in {norm_name(c): c for c in m["competitors"]}.items():
                comp[c[0]] += 1
                label.setdefault(c[0], c[1])
        dom = Counter()
        cited = 0
        grounded = has_search(mode)
        for m in ms:
            for d in m["source_domains"]:
                dom[d] += 1
            cited += m["cited_own_site"]
        by_mode[mode] = {
            "answers": n,
            "mentioned": len(hit),
            "mention_rate": pct(len(hit), n),
            "avg_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
            "first": len(first),
            "first_rate": pct(len(first), n),   # 第一想起率: 1番目に名前が挙がった割合
            "top3": len(top3),
            "top3_rate": pct(len(top3), n),
            "needs_review": sum(m["needs_review"] for m in ms),
            "no_ai_answer": sum(1 for m in ms if (m["question_id"], mode, m.get("repeat", 1)) in no_ans),
            "extract_failed": sum(1 for m in ms if m["extract_ok"] is False),
            "dropped_names": sum(len(m["dropped_by_check"]) for m in ms),
            "competitors": [{"name": label[k], "count": c} for k, c in comp.most_common(top)],
            "sources": [{"domain": d, "count": c} for d, c in dom.most_common(top)] if grounded else [],
            "own_site_cited": cited if grounded else None,
            "own_site_cite_rate": pct(cited, n) if grounded and target["domains"] else None,
        }
    return {"by_mode": by_mode}


def pct(a: int, b: int):
    return round(100 * a / b, 1) if b else None
