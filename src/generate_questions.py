"""① 質問の生成 (F-02, F-03)。

LLM が作った質問は、Python 側で次を確かめてから使う:
重複していない / 対象の会社名・別名を含まない / 短すぎず長すぎない。足りなければ1回だけ作り直す。
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .normalize import norm_name

SCHEMA = {
    "type": "object",
    "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
    "required": ["questions"],
}

PROMPT = """あなたは、AIチャット（Gemini や ChatGPT）に相談する一般の利用者の質問を考える係です。
次の条件で、利用者が実際に入力しそうな質問を{n}個、日本語で作ってください。

- 業界：{industry}
- 地域：{region}
- 補足：{notes}

条件：
- 「おすすめの会社は？」「比較して」「〜に強いところは？」など、会社や事務所の名前が答えに挙がる質問にする
- 特定の会社名・サービス名は質問文に入れない
- 目的・規模・予算・地域など、条件の付け方を質問ごとに変える
- 1つの質問は15〜80文字くらい
{avoid}
JSON で {{"questions": ["...", ...]}} の形だけを返してください。"""


def build_prompt(t: dict, n: int, avoid: list[str] | None = None) -> str:
    av = ""
    if avoid:
        av = "- 次の質問とは別の質問にする：" + " / ".join(avoid[:30])
    return PROMPT.format(n=n, industry=t["industry"], region=t["region"] or "指定なし（日本）",
                         notes=t["notes"] or "なし", avoid=av)


def validate(raw: list, target: dict) -> tuple[list[str], list[str]]:
    """戻り値は (使う質問, 落とした理由)。"""
    banned = [norm_name(x) for x in [target["name"], *target["aliases"]] if norm_name(x)]
    seen, ok, dropped = set(), [], []
    for q in raw:
        if not isinstance(q, str):
            dropped.append(f"文字列ではない: {q!r}")
            continue
        q = " ".join(q.split())
        k = norm_name(q)
        if not (8 <= len(q) <= 150):
            dropped.append(f"長さ: {q}")
        elif any(b in k for b in banned):
            dropped.append(f"会社名入り: {q}")
        elif k in seen:
            dropped.append(f"重複: {q}")
        else:
            seen.add(k)
            ok.append(q)
    return ok, dropped


def generate(cfg: dict, engine, limiter, log=print) -> list[dict]:
    t, n = cfg["target"], int(cfg["questions"]["count"])
    model = cfg["models"]["questions"]
    got: list[str] = []
    for round_ in range(2):
        need = n - len(got)
        prompt = build_prompt(t, need + 2, got)
        _, res = limiter.call_models(
            model, lambda m: (lambda: engine.generate(m, prompt, json_schema=SCHEMA)))
        try:
            raw = json.loads(res.text)["questions"]
        except (json.JSONDecodeError, KeyError, TypeError):
            log(f"  質問の生成: JSONとして読めませんでした ({round_ + 1}回目)")
            raw = []
        ok, dropped = validate(got + list(raw), t)
        for d in dropped:
            log(f"  質問を除外 -> {d}")
        got = ok
        if len(got) >= n:
            break
    if not got:
        raise RuntimeError("質問を1つも作れませんでした")
    if len(got) < n:
        log(f"  注意: 質問は {len(got)} 個しか作れませんでした (指定 {n} 個)")
    return [{"id": f"q{i:02d}", "text": q} for i, q in enumerate(got[:n], 1)]


def save(path: Path, questions: list[dict], cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (f"# 質問セット ({cfg['target']['name']} / {cfg['target']['industry']})\n"
            "# 自由に直してください。同じファイルを使い回すと、対策の前と後を同じ条件で比べられます。\n"
            "# id は変えないでください (結果の突き合わせに使います)。\n")
    body = yaml.safe_dump({"questions": questions}, allow_unicode=True, sort_keys=False)
    path.write_text(head + body, encoding="utf-8")


def load(path: Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    qs = data.get("questions") or []
    out, ids = [], set()
    for i, q in enumerate(qs, 1):
        if isinstance(q, str):
            q = {"id": f"q{i:02d}", "text": q}
        if not q.get("text"):
            continue
        qid = str(q.get("id") or f"q{i:02d}")
        if qid in ids:
            raise ValueError(f"{path}: id が重複しています: {qid}")
        ids.add(qid)
        out.append({"id": qid, "text": str(q["text"]).strip()})
    if not out:
        raise ValueError(f"{path}: 質問がありません")
    return out
