from datetime import datetime
from types import SimpleNamespace

import pytest

from src import generate_questions, judge
from src.engines.base import DailyQuotaError, TransientError
from src.engines.gemini import source_from_chunk
from src.limiter import PT, Limiter, QuotaExhausted
from src.normalize import domain_matches, find_first, norm_name, norm_text

T = {"name": "ミドリ株式会社", "aliases": ["ミドリ", "Midori"], "ambiguous_aliases": [],
     "domains": ["midori.example.co.jp"], "industry": "x", "region": "", "notes": ""}


# ---- normalize ----
@pytest.mark.parametrize("a,b", [
    ("ミドリ株式会社", "株式会社ミドリ"),
    ("㈱ミドリ", "ミドリ"),
    ("ＭＩＤＯＲＩ Inc.", "midori"),
    ("Midori Co., Ltd.", "MIDORI"),
    ("ミドリ　（株）", "ミドリ"),
])
def test_norm_name_variants(a, b):
    assert norm_name(a) == norm_name(b)


def test_corp_suffix_only_as_word():
    assert norm_name("Princeton") == "princeton"
    assert norm_name("Incubate Inc.") == "incubate"


def test_glued_detection():
    t, _ = norm_text("ミドリムシの保護団体と、ミドリ株式会社")
    pos, n, glued = find_first(t, ["ミドリ"])
    assert not glued and t[pos:].startswith("ミドリ") and pos > 5  # 2つ目 (会社) が選ばれる
    t2, _ = norm_text("ミドリムシについて")
    assert find_first(t2, ["ミドリ"])[2] is True
    # 空白を外す前の文で前後を見る: "Top Midori" は別の単語どうし
    src = "Top Midori Inc."
    t3, idx = norm_text(src)
    assert find_first(t3, ["midori"], src.lower(), idx)[2] is False
    assert find_first(t3, ["midori"])[2] is True  # 空白を外した文だけだと、続きに見える


def test_domain_match():
    assert domain_matches("https://www.midori.example.co.jp/x", ["midori.example.co.jp"])
    assert domain_matches("blog.midori.example.co.jp", ["midori.example.co.jp"])
    assert not domain_matches("notmidori.example.co.jp", ["midori.example.co.jp"])


# ---- judge ----
def ans(text, sources=(), mode="grounded"):
    return {"question_id": "q01", "mode": mode, "repeat": 1, "answer": text, "sources": list(sources)}


def ext(*names, ok=True):
    return {"names": list(names), "parse_ok": ok}


def test_rank_and_hallucination_dropped():
    a = ans("1. 株式会社ミライ\n2. ＭＩＤＯＲＩ株式会社\n3. シグナル合同会社")
    m = judge.judge_one(a, ext("ミライ", "Midori", "シグナル", "架空商事"), T)
    assert m["mentioned"] and m["rank"] == 2
    assert m["dropped_by_check"] == ["架空商事"]
    assert m["competitors"] == ["ミライ", "シグナル"]
    assert not m["needs_review"]


def test_python_finds_target_even_if_llm_missed():
    a = ans("おすすめは株式会社ミライとミドリ株式会社です。")
    m = judge.judge_one(a, ext("ミライ"), T)
    assert m["mentioned"] and m["rank"] == 2
    assert m["needs_review"]  # 食い違いは人が確かめる


def test_name_inside_another_company():
    # 対象の名前「ミドリ」を含む別の会社「ミドリ電機」は、競合にも対象にも数えず「要確認」
    a = ans("1. ミドリ電機株式会社\n2. 株式会社ミライ\n3. ミドリ株式会社")
    m = judge.judge_one(a, ext("ミドリ電機株式会社", "株式会社ミライ", "ミドリ株式会社"), T)
    assert m["mentioned"] and m["rank"] == 2  # ミドリ電機の中の「ミドリ」は数えない
    assert m["competitors"] == ["株式会社ミライ"] and m["needs_review"]
    # 「ミドリ電機」の中にしか出ない -> 出たと数えるが「要確認」
    a = ans("1. ミドリ電機株式会社\n2. 株式会社ミライ")
    m = judge.judge_one(a, ext("ミドリ電機株式会社", "株式会社ミライ"), T)
    assert m["mentioned"] and m["needs_review"] and "一部としてだけ" in m["review_reasons"][0]


def test_not_mentioned_and_citation():
    a = ans("候補は株式会社ミライです。", [{"domain": "midori.example.co.jp", "url": "", "title": ""}])
    m = judge.judge_one(a, ext("株式会社ミライ"), T)
    assert not m["mentioned"] and m["rank"] is None and m["cited_own_site"]


def test_summary_rates():
    ms = [judge.judge_one(ans("ミドリ株式会社と株式会社ミライ"), ext("ミドリ株式会社", "株式会社ミライ"), T),
          judge.judge_one(ans("株式会社ミライ、株式会社シグナル、株式会社ノース、ミドリ"),
                          ext("株式会社ミライ", "株式会社シグナル", "株式会社ノース", "ミドリ"), T),
          judge.judge_one(ans("株式会社ミライ"), ext("株式会社ミライ"), T)]
    s = judge.summarize(ms, [], T)["by_mode"]["grounded"]
    assert s["mention_rate"] == 66.7 and s["avg_rank"] == 2.5 and s["top3"] == 1
    assert s["first"] == 1 and s["first_rate"] == 33.3
    assert s["competitors"][0] == {"name": "株式会社ミライ", "count": 3}


# ---- questions ----
def test_question_validation():
    ok, dropped = generate_questions.validate(
        ["東京でおすすめのAI検索対策会社は？", "東京でおすすめのAI検索対策会社は？",
         "ミドリの評判は？どうですか", "短い", 3], T)
    assert ok == ["東京でおすすめのAI検索対策会社は？"] and len(dropped) == 4


# ---- limiter ----
class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def sleep(self, s):
        self.slept.append(s)
        self.t += s

    def mono(self):
        return self.t


def mk(tmp_path, clock, **lim):
    limits = {"default": {"rpm": 0, "rpd": 3}, "grounding_rpd": 2, "retries": 3, "backoff_seconds": 10, **lim}
    return Limiter(tmp_path / "usage.json", limits, sleep=clock.sleep, clock=clock.mono,
                   now=lambda: datetime(2026, 11, 22, 10, tzinfo=PT), log=lambda *a: None)


def test_daily_limit_persists(tmp_path):
    c = Clock()
    lim = mk(tmp_path, c)
    for _ in range(3):
        lim.call("m", lambda: 1)
    with pytest.raises(QuotaExhausted):
        lim.call("m", lambda: 1)
    with pytest.raises(QuotaExhausted):  # 別のプロセスでも、ファイルから回数が読める
        mk(tmp_path, c).call("m", lambda: 1)


def test_grounding_limit(tmp_path):
    lim = mk(tmp_path, Clock(), default={"rpm": 0, "rpd": 99})
    lim.call("m", lambda: 1, grounding=True)
    lim.call("m", lambda: 1, grounding=True)
    lim.call("m", lambda: 1)  # 検索なしはまだ使える
    with pytest.raises(QuotaExhausted):
        lim.call("m", lambda: 1, grounding=True)


def test_retry_backoff_then_give_up(tmp_path):
    c = Clock()
    lim = mk(tmp_path, c, default={"rpm": 0, "rpd": 99})
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TransientError("429")
        return "ok"

    assert lim.call("m", flaky) == "ok" and c.slept == [10, 20]

    def always():
        raise TransientError("503")

    with pytest.raises(TransientError):
        lim.call("m", always)
    assert lim.used("m") == 1  # 失敗は数えない


def test_daily_quota_from_api_stops_immediately(tmp_path):
    lim = mk(tmp_path, Clock(), default={"rpm": 0, "rpd": 99})

    def f():
        raise DailyQuotaError("GenerateRequestsPerDay exceeded")

    with pytest.raises(QuotaExhausted):
        lim.call("m", f)


def test_fallback_to_next_model(tmp_path):
    lim = mk(tmp_path, Clock(), default={"rpm": 0, "rpd": 99}, retries=1)

    def make(m):
        def f():
            if m == "busy":
                raise TransientError("503 high demand")
            return m
        return f

    calls = []
    orig = make

    def make(m):  # noqa: F811
        calls.append(m)
        return orig(m)

    assert lim.call_models(["busy", "ok"], make) == ("ok", "ok")
    assert calls == ["busy", "ok"]
    calls.clear()
    assert lim.call_models(["busy", "ok"], make) == ("ok", "ok")
    assert calls == ["ok"]  # 2回目は、前に通ったモデルから試す
    assert lim.call_models("ok", make) == ("ok", "ok")
    with pytest.raises(TransientError):
        lim.call_models(["busy"], make)


def test_rpm_pacing(tmp_path):
    c = Clock()
    lim = mk(tmp_path, c, default={"rpm": 6, "rpd": 99})
    lim.call("m", lambda: 1)
    lim.call("m", lambda: 1)
    assert c.slept == [10.0]


# ---- gemini sources ----
def test_source_redirect_uses_title_domain():
    web = SimpleNamespace(uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
                          title="www.Example.co.jp", domain=None)
    s = source_from_chunk(web, resolve=False)
    assert s["domain"] == "example.co.jp"


def test_source_direct_url():
    web = SimpleNamespace(uri="https://news.example.com/a", title="記事のタイトル", domain=None)
    assert source_from_chunk(web, resolve=False)["domain"] == "news.example.com"
