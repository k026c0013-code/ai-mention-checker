"""⑤ レポート (F-08, F-09)。外部ファイルを読まない HTML 1枚。ブラウザで開け、そのまま PDF に印刷できる。"""
from __future__ import annotations

import csv
import html
from pathlib import Path

from .surfaces import has_search, is_manual, label, note

# 推移のグラフの線の色 (調べ先ごと)
PALETTE = ["var(--hit)", "var(--accent)", "#16a34a", "#9333ea", "#ca8a04", "#0891b2", "#db2777"]

CSS = """
:root{--bg:#fff;--fg:#1d2330;--mute:#5b6475;--line:#e3e6ec;--card:#f6f7f9;--accent:#2563eb;--hit:#e8590c;--bar:#9aa7bd;--ok:#16794a;--ng:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--fg:#e8ebf0;--mute:#9aa3b2;--line:#2b313b;--card:#1c2028;--accent:#6ea0ff;--hit:#ff8a4c;--bar:#5d6a80;--ok:#4cc38a;--ng:#ff7b72}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.7 "Yu Gothic UI","Hiragino Sans","Noto Sans JP",system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:40px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.meta{color:var(--mute);font-size:13px}
.demo{background:#fff4e5;color:#7a4100;border:1px solid #f5c27a;border-radius:8px;padding:10px 14px;margin:16px 0;font-weight:600}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:20px 0}
.hero div{background:var(--card);border-radius:10px;padding:16px 18px}
.hero .big{font-size:36px;font-weight:700;line-height:1.2}.hero .sub{color:var(--mute);font-size:13px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}
th{color:var(--mute);font-weight:600}td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(430px,100%),1fr));gap:24px 32px}
.bars{width:100%;display:block}.grid2>div>b{display:block;margin-bottom:4px}.bars text{font-size:12px;fill:var(--fg)}.bars .v{fill:var(--mute)}
.ok{color:var(--ok);font-weight:700}.ng{color:var(--ng)}.warn{color:var(--hit);font-size:12px}
details summary{cursor:pointer;color:var(--accent);font-size:13px}details pre{white-space:pre-wrap;background:var(--card);padding:10px;border-radius:6px;font:13px/1.6 inherit;margin:6px 0}
.notes li{margin:4px 0}.small{font-size:12px;color:var(--mute)}
@media print{body{font-size:12px}main{padding:0}details{display:none}.hero .big{font-size:26px}h2{break-after:avoid}tr{break-inside:avoid}.grid2>div,.hero div{break-inside:avoid}}
"""


def e(s) -> str:
    return html.escape("" if s is None else str(s))


def fmt(v, unit=""):
    return "—" if v is None else f"{v}{unit}"


def bars(items: list[tuple[str, int, bool]], width=440, lab_w=170, max_chars=14) -> str:
    if not items:
        return '<p class="small">データなし</p>'
    mx = max(c for _, c, _ in items) or 1
    row = 24
    h = row * len(items) + 4
    out = [f'<svg class="bars" viewBox="0 0 {width} {h}" style="max-width:{width}px" role="img">']
    for i, (label, c, hl) in enumerate(items):
        y = i * row + 2
        w = max(2, (width - lab_w - 40) * c / mx)
        lab = label if len(label) <= max_chars else label[:max_chars - 1] + "…"
        color = "var(--hit)" if hl else "var(--bar)"
        bold = ' font-weight="700"' if hl else ""
        out.append(f'<text x="{lab_w - 6}" y="{y + 15}" text-anchor="end"'
                   f'{bold}><title>{e(label)}</title>{e(lab)}</text>'
                   f'<rect x="{lab_w}" y="{y + 3}" width="{w:.1f}" height="16" rx="3" fill="{color}"/>'
                   f'<text class="v" x="{lab_w + w + 6:.1f}" y="{y + 15}">{c}</text>')
    out.append("</svg>")
    return "".join(out)


def history_svg(hist: list[dict], width=640, height=180) -> str:
    """過去の実行の言及率の推移 (F-09)。"""
    modes = list(dict.fromkeys(m for h in hist for m, v in h["rates"].items() if v is not None))
    if len(hist) < 2 or not modes:
        return ""
    pad_l, pad_r, pad_t, pad_b = 36, 12, 10, 28
    W, H = width - pad_l - pad_r, height - pad_t - pad_b
    xs = [pad_l + (W * i / (len(hist) - 1)) for i in range(len(hist))]
    y = lambda v: pad_t + H * (1 - v / 100)
    out = [f'<svg class="bars" viewBox="0 0 {width} {height}" style="max-width:{width}px" role="img">']
    for g in (0, 50, 100):
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y(g)}" y2="{y(g)}" stroke="var(--line)"/>'
                   f'<text class="v" x="{pad_l - 6}" y="{y(g) + 4}" text-anchor="end">{g}%</text>')
    for m, color in zip(modes, PALETTE):
        pts = [(x, h["rates"].get(m)) for x, h in zip(xs, hist)]
        pts = [(x, v) for x, v in pts if v is not None]
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="'
                   + " ".join(f"{x:.1f},{y(v):.1f}" for x, v in pts) + '"/>')
        for x, v in pts:
            out.append(f'<circle cx="{x:.1f}" cy="{y(v):.1f}" r="3.5" fill="{color}"><title>{v}%</title></circle>')
    for x, h in zip(xs, hist):
        out.append(f'<text class="v" x="{x:.1f}" y="{height - 8}" text-anchor="middle">{e(h["label"])}</text>')
    out.append("</svg>")
    legend = "　".join(f'<span style="color:{c}">●</span> {e(label(m))}' for m, c in zip(modes, PALETTE))
    return "".join(out) + f'<p class="small">{legend}</p>'


def render(run: dict, cfg: dict, questions: list[dict], answers: list[dict], mentions: list[dict],
           summary: dict, history: list[dict]) -> str:
    t = cfg["target"]
    bm = summary["by_mode"]
    modes = list(bm)
    nq = len(questions)
    reps = int(cfg["ask"]["repeats"])
    fake = run.get("engine") == "fake"
    models = sorted({a.get("engine", "") for a in answers if not str(a.get("engine", "")).startswith("manual:")})
    manual = [m for m in modes if is_manual(m)]
    where = "、".join(label(m) for m in modes)

    p = []
    p.append(f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
             f'<meta name="viewport" content="width=device-width,initial-scale=1">'
             f'<title>AI言及レポート {e(t["name"])}</title><style>{CSS}</style></head><body><main>')
    p.append(f'<h1>AI言及レポート：{e(t["name"])}</h1>')
    p.append(f'<div class="meta">業界：{e(t["industry"])}　地域：{e(t["region"] or "指定なし")}　'
             f'実行：{e(run.get("started_at", "")[:16].replace("T", " "))}　'
             f'調べ先：{e(where)}'
             + (f'　APIのモデル：{e(", ".join(models))}' if models else "") + '</div>')
    if fake:
        p.append('<div class="demo">これはダミーデータによるデモです（--engine fake）。実際のAIの回答ではありません。</div>')

    # 1. 結論
    p.append('<div class="hero">')
    for m in modes:
        s = bm[m]
        p.append(f'<div><div class="sub">{e(label(m))}の言及率</div>'
                 f'<div class="big">{fmt(s["mention_rate"], "%")}</div>'
                 f'<div class="sub">{s["answers"]}回答中 {s["mentioned"]}回答で名前が挙がった'
                 f'{"（" + str(nq) + "問×" + str(reps) + "回）" if reps > 1 else ""}<br>{e(note(m))}</div></div>')
    p.append('</div>')

    # 2. 指標
    p.append('<h2>指標</h2><div style="overflow-x:auto"><table><tr><th>指標</th>'
             + "".join(f'<th class="num">{e(label(m))}</th>' for m in modes) + '</tr>')
    rows = [
        ("言及率", lambda s: fmt(s["mention_rate"], "%")),
        ("名前が出た回答 / 回答数", lambda s: f'{s["mentioned"]} / {s["answers"]}'),
        ("第一想起率（1番目に挙がった）", lambda s: fmt(s.get("first_rate"), "%")),
        ("平均順位（出たときだけ）", lambda s: fmt(s["avg_rank"], "位")),
        ("トップ3率", lambda s: fmt(s["top3_rate"], "%")),
        ("自社サイトの引用率", lambda s: fmt(s["own_site_cite_rate"], "%") if s["own_site_cite_rate"] is not None else "—"),
        ("要確認の判定", lambda s: f'{s["needs_review"]}件'),
    ]
    if any(bm[m].get("no_ai_answer") for m in modes):
        rows.append(("AIの回答が出なかった", lambda s: f'{s.get("no_ai_answer", 0)}件'))
    for name, f in rows:
        p.append(f'<tr><td>{name}</td>' + "".join(f'<td class="num">{f(bm[m])}</td>' for m in modes) + '</tr>')
    p.append('</table></div>')
    if not t["domains"]:
        p.append('<p class="small">公式サイトのドメインが設定されていないため、自社サイトの引用率は出していません。</p>')

    # 3. 競合
    p.append('<h2>一緒に名前が挙がった会社（上位10社）</h2><p class="small">名前が出た回答の数。対象の会社は色を変えています。</p><div class="grid2">')
    for m in modes:
        s = bm[m]
        items = [(c["name"], c["count"], False) for c in s["competitors"]]
        items.append((t["name"], s["mentioned"], True))
        items.sort(key=lambda x: -x[1])
        p.append(f'<div><b>{e(label(m))}</b>{bars(items[:11])}</div>')
    p.append('</div>')

    # 4. 出典 (検索する調べ先ごと)
    src_modes = [m for m in modes if has_search(m) and bm[m]["sources"]]
    if src_modes:
        p.append('<h2>よく使われた出典サイト（上位10件）</h2>'
                 '<p class="small">そのドメインが出典に入った回答の数。ここに載るサイトに情報があると、候補に入りやすくなります。'
                 '自社サイトは色を変えています。</p><div class="grid2">')
        for m in src_modes:
            items = [(d["domain"], d["count"],
                      any(d["domain"] == x or d["domain"].endswith("." + x) for x in t["domains"]))
                     for d in bm[m]["sources"]]
            p.append(f'<div><b>{e(label(m))}</b>{bars(items, width=440, lab_w=200, max_chars=28)}</div>')
        p.append('</div>')

    # F-09 推移
    hs = history_svg(history)
    if hs:
        p.append('<h2>言及率の推移</h2><p class="small">同じ対象の過去の実行。質問セットが同じ回だけを並べています。</p>' + hs)

    # 5. 明細
    p.append('<h2>質問ごとの明細</h2><div style="overflow-x:auto"><table><tr><th>ID</th><th>質問</th>'
             + "".join(f'<th>{e(label(m))}</th>' for m in modes) + '</tr>')
    ans_by = {(a["question_id"], a["mode"], a.get("repeat", 1)): a for a in answers}
    men_by = {(x["question_id"], x["mode"], x.get("repeat", 1)): x for x in mentions}
    for q in questions:
        p.append(f'<tr><td>{e(q["id"])}</td><td>{e(q["text"])}</td>')
        for m in modes:
            cells = []
            for r in range(1, reps + 1):
                x = men_by.get((q["id"], m, r))
                a = ans_by.get((q["id"], m, r))
                if not x:
                    cells.append('<span class="small">未実行</span>')
                    continue
                if a and a.get("no_answer"):
                    cells.append('<span class="small">AIの回答なし</span>')
                    continue
                mark = (f'<span class="ok">○ {x["rank"]}位</span>' if x["mentioned"] else '<span class="ng">×</span>')
                if x["needs_review"]:
                    mark += f' <span class="warn" title="{e(" / ".join(x["review_reasons"]))}">要確認</span>'
                if x["cited_own_site"]:
                    mark += ' <span class="small">自社サイト引用</span>'
                body = ""
                if a:
                    src = "".join(f'<li>{e(s.get("domain") or s.get("title"))}</li>' for s in a.get("sources") or [])
                    comp = "、".join(e(c) for c in x["companies"]) or "なし"
                    body = (f'<details><summary>回答を見る</summary><pre>{e(a["answer"])}</pre>'
                            f'<div class="small">出た会社（順）：{comp}</div>'
                            + (f'<div class="small">出典：</div><ul class="small">{src}</ul>' if src else "")
                            + '</details>')
                cells.append(mark + body)
            p.append('<td>' + "<hr>".join(cells) + '</td>')
        p.append('</tr>')
    p.append('</table></div>')

    # 6. 注意書き
    once = "1回分" if reps == 1 else f"各質問{reps}回分"
    manual_note = ""
    if manual:
        manual_note = ('<li>' + e("、".join(label(m) for m in manual))
                       + ' は、実際の画面に質問して、表示された回答を取り込みました。'
                       '回答はログイン状態・地域・時間で変わります。出典は、コピーした文章に入っていたリンクだけを数えています。</li>')
    p.append('<h2>注意</h2><ul class="notes">'
             f'<li>AIの回答は聞くたびに変わります。この数字は{once}の結果です。</li>'
             f'<li>調べたのは {e(where)} です。ほかのAIでは結果が変わる可能性があります。</li>'
             + manual_note +
             '<li>「名前が出たか」は、回答文に会社名・別名が書かれているかを、プログラムのルールで判定しています。'
             'AIが抜き出した会社名は、回答文に実際に書かれているものだけを使っています。</li>'
             '<li>「要確認」は、同じ名前の別のものかもしれない判定です。回答を読んで確かめてください。</li>'
             '</ul>')
    p.append(f'<p class="small">設定：{e(cfg.get("_path", ""))}　質問 {nq} 問　'
             f'実行フォルダ：{e(run.get("dir", ""))}　生成：ai-mention-checker</p>')
    p.append('</main></body></html>')
    return "".join(p)


def write_csv(path: Path, questions, mentions) -> None:
    qtext = {q["id"]: q["text"] for q in questions}
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["question_id", "question", "mode", "repeat", "mentioned", "rank", "needs_review",
                    "cited_own_site", "companies", "source_domains"])
        for m in mentions:
            w.writerow([m["question_id"], qtext.get(m["question_id"], ""), m["mode"], m.get("repeat", 1),
                        int(m["mentioned"]), m["rank"] or "", int(m["needs_review"]), int(m["cited_own_site"]),
                        " / ".join(m["companies"]), " / ".join(m["source_domains"])])
