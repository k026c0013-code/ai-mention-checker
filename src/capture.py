"""② (手入力) 本物のAI検索の画面から回答を取り込む。

流れ (1問ごと):
  1. 質問をクリップボードに入れ、その画面をブラウザで開く (URL で質問を渡せる画面は、そのまま検索される)
  2. 人が回答の「コピー」ボタンを押し、ここで Enter
  3. クリップボードの文章を読み、回答と出典URLに分けて answers.jsonl に保存する

ブラウザの自動操作はしない。開くのはふつうのリンクと同じ URL だけ。
"""
from __future__ import annotations

import re
import webbrowser
from pathlib import Path

from . import surfaces
from .normalize import domain_of
from .store import append_jsonl, now_iso

_MD_IMAGE = re.compile(r"!\[[^\]\n]{0,300}\]\((https?://[^\s)]+)\)")
_MD_LINK = re.compile(r"\[([^\]\n]{0,300})\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"https?://[^\s<>\"'）」』】\])]+")
_IMG_EXT = re.compile(r"\.(png|jpe?g|gif|webp|svg|ico|avif)(\?|$)", re.I)
# 画面の画像・アイコンの置き場所。出典ではないので数えない (2026-11-19 の ChatGPT の取り込みで実際に混ざった)
_IMG_HOSTS = ("images.openai.com", "gstatic.com", "googleusercontent.com")


def is_image_url(url: str) -> bool:
    host = domain_of(url)
    path = re.sub(r"^[a-z]+://[^/]+", "", url.lower())
    return (host.endswith(_IMG_HOSTS) or (host.endswith("google.com") and path.startswith("/s2/favicons"))
            or bool(_IMG_EXT.search(path.split("#")[0])))


def parse_paste(text: str) -> tuple[str, list[dict]]:
    """コピーした文章を (判定に使う本文, 出典) に分ける。

    本文からは URL を外す (URL の中の文字列で「会社名が出た」と数えないため)。
    Markdown のリンク [タイトル](URL) はタイトルだけを残す。
    """
    sources, seen = [], set()

    def add(url: str, title: str = "") -> None:
        url = url.rstrip(".,、。;:").replace("\\&", "&")
        if is_image_url(url):
            return
        key = url.split("#")[0]
        if key in seen:
            return
        seen.add(key)
        sources.append({"title": title.strip(), "url": url, "domain": domain_of(url)})

    def md(m: re.Match) -> str:
        add(m.group(2), m.group(1))
        return m.group(1)

    body = _MD_IMAGE.sub("", text)
    body = _MD_LINK.sub(md, body)

    def bare(m: re.Match) -> str:
        add(m.group(0))
        return ""

    body = _URL.sub(bare, body)
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\(\s*\)|（\s*）", "", body)
    return body.strip(), sources


HELP = "  Enter=取り込む / n=AIの回答が出なかった / s=今回は飛ばす / q=やめる（続きは次回）"


def run(jobs: list[dict], answers_path: Path, *, input_fn=input, open_fn=webbrowser.open,
        clip_get=None, clip_set=None, log=print) -> str:
    """戻り値: "done" (全部取り込んだ) / "quit" (途中でやめた)。"""
    if clip_get is None or clip_set is None:
        from . import clipboard
        clip_get = clip_get or clipboard.get_text
        clip_set = clip_set or clipboard.set_text

    # 同じ画面を続けて開けるように、サーフェスごとにまとめる
    order = {m: i for i, m in enumerate(dict.fromkeys(j["mode"] for j in jobs))}
    jobs = sorted(jobs, key=lambda j: (order[j["mode"]], j["question_id"], j.get("repeat", 1)))
    last_answer = None
    current = None
    for i, j in enumerate(jobs, 1):
        s = surfaces.get(j["mode"])
        if current != j["mode"]:
            current = j["mode"]
            log(f"\n=== {s['label']} ===\n  コピーのしかた: {s.get('howto', '回答をコピー')}\n{HELP}")
        log(f"\n[{i}/{len(jobs)}] {s['label']} {j['question_id']}: {j['question']}")
        clip_set(j["question"])
        url = surfaces.url_for(j["mode"], j["question"])
        open_fn(url)
        if s.get("paste_question"):
            log("  質問をクリップボードに入れました。画面に貼り付けて (Ctrl+V) 送信してください。")
        else:
            log("  ブラウザで開きました。質問が入っていなければ、Ctrl+V で貼り付けて送信してください。")
        while True:
            cmd = input_fn("  回答をコピーしたら Enter > ").strip().lower()
            if cmd == "q":
                return "quit"
            if cmd == "s":
                log("  飛ばしました（次回また聞きます）")
                break
            if cmd == "n":
                _save(answers_path, j, "", [], "", no_answer=True)
                log("  「AIの回答なし」として記録しました")
                break
            if cmd == "?":
                log(HELP)
                continue
            raw = clip_get() or ""
            problem = _check(raw, j["question"], last_answer)
            if problem and cmd != "f":
                log(f"  {problem}  (それでも取り込むなら f)")
                continue
            body, sources = parse_paste(raw)
            _save(answers_path, j, body, sources, raw)
            last_answer = raw
            log(f"  取り込みました（{len(body)}文字、出典 {len(sources)}件）")
            break
    return "done"


def _check(raw: str, question: str, last: str | None) -> str | None:
    t = raw.strip()
    if not t:
        return "クリップボードが空です。回答をコピーしてください。"
    if t == question.strip():
        return "クリップボードが質問のままです。回答をコピーしてください。"
    if last is not None and t == last.strip():
        return "前の回答と同じ内容です。今の回答をコピーしたか確かめてください。"
    if len(t) < 20:
        return "短すぎます。回答全体をコピーしたか確かめてください。"
    return None


def _save(path: Path, j: dict, body: str, sources: list[dict], raw: str, no_answer: bool = False) -> None:
    append_jsonl(path, {
        **j,
        "engine": f"manual:{j['mode']}",
        "model_version": None,
        "grounding": surfaces.has_search(j["mode"]),
        "asked_at": now_iso(),
        "answer": body,
        "sources": sources,
        "search_queries": [],
        "raw_paste": raw,
        "no_answer": no_answer,
    })
