"""APIを使わない、決まった答えを返すエンジン。テストと、APIキーなしのデモ (--engine fake) 用。

レポートには「ダミーデータ」と大きく出る。
"""
from __future__ import annotations

import hashlib
import json
import re

from .base import Engine, Result

_POOL = [
    ("ヒカリ総研株式会社", "hikari-soken.example"),
    ("株式会社ミライ検索", "mirai-kensaku.example"),
    ("ブルーライン合同会社", "blueline.example"),
    ("株式会社ナレッジワークス", "knowledgeworks.example"),
    ("グロースパートナーズ株式会社", "growth-partners.example"),
    ("株式会社シグナル", "signal.example"),
    ("Northwind Inc.", "northwind.example"),
]
_MEDIA = ["hikaku-media.example", "biz-news.example", "note.com", "prtimes.jp"]


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest(), 16)


class FakeEngine(Engine):
    name = "fake"
    supports_grounding = True

    def __init__(self, target_name: str = "対象株式会社", target_domain: str = "target.example"):
        self.target = (target_name, target_domain)

    def generate(self, model, prompt, *, grounding=False, json_schema=None):
        if json_schema and "questions" in json.dumps(json_schema):
            n = int(re.search(r"(\d+)個", prompt).group(1)) if re.search(r"(\d+)個", prompt) else 10
            topics = ["おすすめ", "比較", "評判のいい", "実績が多い", "料金が安い", "中小企業向けの",
                      "相談しやすい", "最新の", "大手の", "初心者向けの"]
            qs = [f"{t}AI検索対策の会社を教えて（{i + 1}）" for i, t in
                  zip(range(n), (topics * 5))]
            return Result(text=json.dumps({"questions": qs}, ensure_ascii=False), model=model)
        if json_schema and "companies" in json.dumps(json_schema):
            ans = prompt.split("<answer>")[-1]
            names = [n for n, _ in _POOL + [self.target] if n.split("株式会社")[-1][:4] in ans]
            names.sort(key=lambda n: ans.find(n.replace("株式会社", "")[:4]))
            names.append("存在しない架空商事")  # LLMの誤抽出のまね: Python側の検証で落ちるはず
            return Result(text=json.dumps({"companies": [{"name": n} for n in names]},
                                          ensure_ascii=False), model=model)
        seed = _h(prompt, grounding)
        pool = list(_POOL)
        k = 3 + seed % 3
        picks = sorted(pool, key=lambda p: _h(seed, p[0]))[:k]
        with_target = seed % 10 < (6 if grounding else 3)
        if with_target:
            picks.insert(seed % (len(picks) + 1), self.target)
        lines = ["ご質問の条件に合いそうな会社をいくつか挙げます。"]
        for i, (n, _) in enumerate(picks, 1):
            lines.append(f"{i}. **{n}**：実績が多く、相談しやすいと評判です。")
        lines.append("最終的には、各社の公式サイトで料金と事例を確かめてください。")
        sources = []
        if grounding:
            for n, d in picks[:3]:
                sources.append({"title": d, "url": f"https://{d}/", "domain": d})
            m = _MEDIA[seed % len(_MEDIA)]
            sources.append({"title": m, "url": f"https://{m}/ranking", "domain": m})
        return Result(text="\n".join(lines), model=model, sources=sources,
                      search_queries=[prompt] if grounding else [])
