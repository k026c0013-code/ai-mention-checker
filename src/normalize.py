"""表記ゆれの正規化と、回答文の中での会社名の検索 (F-06, risk 表記ゆれ)."""
from __future__ import annotations

import re
import unicodedata

# 法人格。前後どちらに付いても外す。
_CORP = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社", "一般社団法人", "一般財団法人",
    "公益社団法人", "公益財団法人", "特定非営利活動法人", "npo法人", "税理士法人", "弁護士法人",
    "社会保険労務士法人", "司法書士法人", "医療法人", "学校法人",
    "(株)", "(有)", "(同)", "inc.", "inc", "co.,ltd.", "co., ltd.", "co.,ltd", "co. ltd.", "ltd.",
    "ltd", "llc", "corporation", "corp.", "corp", "k.k.", "g.k.",
]
_CORP_SORTED = sorted(_CORP, key=len, reverse=True)
# 英字の法人格は単語として出たときだけ外す (Princeton の "inc" を消さないように)。
_CORP_RE = re.compile("|".join(
    (r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])") if c[0].isascii() and c[0].isalpha()
    else re.escape(c)
    for c in _CORP_SORTED))
# 空白と記号を外す。長音「ー」は語の一部なので残す。
_DROP = re.compile(r"[\s・･\-‐－―_.,、。「」『』【】\"'’“”()（）\[\]]")


def nfkc(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("㈱", "(株)").replace("㈲", "(有)")
    return s.lower()


def norm_name(s: str) -> str:
    """会社名どうしを比べるための形。法人格・空白・記号を外す。"""
    return _DROP.sub("", _CORP_RE.sub("", nfkc(s)))


def norm_text(s: str) -> tuple[str, list[int]]:
    """回答文を norm_name と同じ規則で縮める。戻り値の2つ目は、縮めた文字 -> 元の位置。"""
    src = nfkc(s)
    keep = [True] * len(src)
    for m in _CORP_RE.finditer(src):
        for j in range(m.start(), m.end()):
            keep[j] = False
    out, idx = [], []
    for i, ch in enumerate(src):
        if keep[i] and not _DROP.match(ch):
            out.append(ch)
            idx.append(i)
    return "".join(out), idx


def _is_word_char(ch: str) -> bool:
    """カタカナと英字。数字は「2. ミドリ」の番号があるので入れない。"""
    if not ch:
        return False
    if ch.isascii():
        return ch.isalpha()
    name = unicodedata.name(ch, "")
    return "KATAKANA" in name or "LATIN" in name


def find_first(text_norm: str, needles: list[str], src: str | None = None,
               idx: list[int] | None = None) -> tuple[int, str, bool] | None:
    """needle を探す。戻り値は (正規化後の位置, 見つかった needle, 前後が語の続きか)。

    「ミドリ」が「ミドリムシ」の一部として出た場合のように、前後がカタカナ・英字で
    続いているときは、別の語の一部かもしれないので 3つ目を True にする (-> 要確認)。
    単独で出た場所があればそれを優先し、無ければ一番早い「続き」の場所を返す。
    src と idx (norm_text の戻り値) を渡すと、前後の文字は空白を外す前の文で調べる。
    """
    def neighbors(i: int, n: str) -> tuple[str, str]:
        if src is not None and idx is not None:
            a, b = idx[i], idx[i + len(n) - 1]
            return (src[a - 1] if a > 0 else ""), (src[b + 1] if b + 1 < len(src) else "")
        return (text_norm[i - 1] if i > 0 else ""), \
            (text_norm[i + len(n)] if i + len(n) < len(text_norm) else "")

    clean, glued_hits = [], []
    for n in needles:
        if not n:
            continue
        start = 0
        while (i := text_norm.find(n, start)) >= 0:
            before, after = neighbors(i, n)
            glued = (_is_word_char(n[0]) and _is_word_char(before)) or \
                (_is_word_char(n[-1]) and _is_word_char(after))
            (glued_hits if glued else clean).append((i, n, glued))
            if not glued:
                break
            start = i + 1
    if clean:
        return min(clean, key=lambda x: (x[0], -len(x[1])))
    if glued_hits:
        return min(glued_hits, key=lambda x: (x[0], -len(x[1])))
    return None


def domain_of(url_or_host: str) -> str:
    s = (url_or_host or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0].split("@")[-1].split(":")[0]
    return s.removeprefix("www.")


def looks_like_domain(s: str) -> bool:
    return bool(re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}", (s or "").strip().lower()))


def domain_matches(domain: str, targets: list[str]) -> bool:
    d = domain_of(domain)
    return any(d == t or d.endswith("." + t) for t in targets)
