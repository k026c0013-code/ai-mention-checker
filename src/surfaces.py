"""調べる先 (サーフェス) の一覧。

- api    : プログラムが API で聞く (Gemini)。全自動。
- manual : 本物のAI検索の画面をブラウザで開き、人が回答をコピーしてツールに取り込む。
           お客さんが実際に見る画面そのものを測れる。自動操作はしない (各サービスの規約に合わせるため)。

url の {q} には質問文 (URLエンコード済み) が入る。質問を URL で渡せない画面は、
質問をクリップボードに入れておくので、人が貼り付けて送る。
"""
from __future__ import annotations

from urllib.parse import quote

SURFACES: dict[str, dict] = {
    # --- API (自動) ---
    "grounded": {"label": "Gemini API（検索あり）", "kind": "api", "search": True,
                 "note": "Google検索で調べてから答える"},
    "plain": {"label": "Gemini API（検索なし）", "kind": "api", "search": False,
              "note": "モデルの知識だけで答える（そもそも知られているか）"},
    # --- 本物の画面 (人が取り込む) ---
    "google_ai_mode": {"label": "Google AIモード", "kind": "manual", "search": True,
                       "url": "https://www.google.com/search?udm=50&q={q}",
                       "note": "Google検索のAIモード",
                       "howto": "回答の下の「コピー」ボタン（無ければ回答部分を選択してCtrl+C）"},
    "google_ai_overview": {"label": "Google AIによる概要", "kind": "manual", "search": True,
                           "url": "https://www.google.com/search?q={q}",
                           "note": "ふつうのGoogle検索の上に出るAIの概要",
                           "howto": "「AIによる概要」の部分を選択してCtrl+C（出なければ n で「出なかった」を記録）"},
    "chatgpt": {"label": "ChatGPT（検索）", "kind": "manual", "search": True,
                "url": "https://chatgpt.com/?q={q}&hints=search",
                "note": "ChatGPTのWeb検索つきの回答",
                "howto": "回答の下の「コピー」ボタン"},
    "perplexity": {"label": "Perplexity", "kind": "manual", "search": True,
                   "url": "https://www.perplexity.ai/search?q={q}",
                   "note": "Perplexityの回答",
                   "howto": "回答の下の「コピー」ボタン"},
    "gemini_app": {"label": "Geminiアプリ", "kind": "manual", "search": True,
                   "url": "https://gemini.google.com/app", "paste_question": True,
                   "note": "gemini.google.com の回答",
                   "howto": "質問を貼り付けて送信 → 回答の「コピー」ボタン"},
}


def get(mode: str) -> dict:
    if mode not in SURFACES:
        raise KeyError(mode)
    return SURFACES[mode]


def label(mode: str) -> str:
    return SURFACES.get(mode, {}).get("label", mode)


def note(mode: str) -> str:
    return SURFACES.get(mode, {}).get("note", "")


def is_manual(mode: str) -> bool:
    return SURFACES.get(mode, {}).get("kind") == "manual"


def has_search(mode: str) -> bool:
    return bool(SURFACES.get(mode, {}).get("search"))


def url_for(mode: str, question: str) -> str:
    return SURFACES[mode]["url"].replace("{q}", quote(question, safe=""))
