"""GeminiEngine を、通信しない偽のクライアントで確かめる (APIキー不要)。"""
from types import SimpleNamespace

import pytest
from google.genai import errors

from src.engines.base import DailyQuotaError, EngineError, GroundingUnavailable, TransientError
from src.engines.gemini import GeminiEngine


def make(resp=None, exc=None):
    eng = GeminiEngine(api_key="dummy", resolve_redirects=False)
    seen = {}

    def gen(model, contents, config):
        seen.update(model=model, contents=contents, config=config)
        if exc:
            raise exc
        return resp

    eng._client = SimpleNamespace(models=SimpleNamespace(generate_content=gen))
    return eng, seen


def grounded_resp():
    web1 = SimpleNamespace(uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/a",
                           title="hikaku.example.jp", domain=None)
    web2 = SimpleNamespace(uri="https://vertexaisearch.cloud.google.com/grounding-api-redirect/b",
                           title="midori.example.co.jp", domain=None)
    gm = SimpleNamespace(web_search_queries=["AI検索 対策 会社"],
                         grounding_chunks=[SimpleNamespace(web=web1), SimpleNamespace(web=web2)])
    cand = SimpleNamespace(grounding_metadata=gm, finish_reason="STOP")
    return SimpleNamespace(text="回答です", candidates=[cand], model_version="gemini-2.5-flash-001")


def test_grounded_request_and_sources():
    eng, seen = make(grounded_resp())
    r = eng.generate("gemini-2.5-flash", "質問", grounding=True)
    assert seen["config"].tools[0].google_search is not None
    assert [s["domain"] for s in r.sources] == ["hikaku.example.jp", "midori.example.co.jp"]
    assert r.search_queries == ["AI検索 対策 会社"] and r.model == "gemini-2.5-flash-001"


def test_json_request():
    resp = SimpleNamespace(text='{"companies": []}', candidates=[], model_version=None)
    eng, seen = make(resp)
    eng.generate("m", "p", json_schema={"type": "object"})
    assert seen["config"].response_mime_type == "application/json"
    assert seen["config"].tools is None


def err(code, details):
    return errors.ClientError(code, {"error": {"code": code, "message": "x", "status": "S", "details": details}})


def test_429_per_minute_is_transient_with_retry_delay():
    e = err(429, [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                   "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
                  {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}])
    eng, _ = make(exc=e)
    with pytest.raises(TransientError) as ei:
        eng.generate("m", "p")
    assert ei.value.retry_after == 37.0


def test_429_per_day_stops():
    e = err(429, [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                   "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}])
    eng, _ = make(exc=e)
    with pytest.raises(DailyQuotaError):
        eng.generate("m", "p")


def test_grounding_not_in_free_tier():
    # 2026-11-19 に実際に返ってきた形 (Help のリンクだけで、RetryInfo も QuotaFailure も無い)
    e = err(429, [{"@type": "type.googleapis.com/google.rpc.Help",
                   "links": [{"description": "Learn more", "url": "https://aistudio.google.com/docs/rate-limits"}]}])
    eng, _ = make(exc=e)
    with pytest.raises(GroundingUnavailable):
        eng.generate("gemini-3.5-flash-lite", "p", grounding=True)
    eng, _ = make(exc=e)
    with pytest.raises(TransientError):  # 検索なしなら、ふつうの回数制限として待つ
        eng.generate("gemini-3.5-flash-lite", "p")


def test_retired_model_message():
    e = errors.ClientError(404, {"error": {"code": 404, "status": "NOT_FOUND",
                                           "message": "This model models/gemini-2.5-flash is no longer available to new users."}})
    eng, _ = make(exc=e)
    with pytest.raises(EngineError, match="新しいユーザー"):
        eng.generate("gemini-2.5-flash", "p")


def test_bad_model_is_not_retried():
    eng, _ = make(exc=err(404, []))
    with pytest.raises(EngineError):
        eng.generate("no-such-model", "p")


def test_network_error_is_transient():
    ReadError = type("ReadError", (Exception,), {"__module__": "httpx2._exceptions"})
    eng, _ = make(exc=ReadError("[WinError 10053] connection aborted"))
    with pytest.raises(TransientError):
        eng.generate("m", "p")
    eng, _ = make(exc=ConnectionResetError("reset"))
    with pytest.raises(TransientError):
        eng.generate("m", "p")
    eng, _ = make(exc=ValueError("bug"))  # 通信以外のエラーは握りつぶさない
    with pytest.raises(ValueError):
        eng.generate("m", "p")


def test_server_error_is_transient():
    eng, _ = make(exc=errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}}))
    with pytest.raises(TransientError):
        eng.generate("m", "p")
