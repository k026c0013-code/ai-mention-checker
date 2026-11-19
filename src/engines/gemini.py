"""Gemini API (google-genai SDK)."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from ..normalize import domain_of, looks_like_domain
from .base import DailyQuotaError, Engine, EngineError, GroundingUnavailable, Result, TransientError

_REDIRECT_HOSTS = ("vertexaisearch.cloud.google.com",)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def resolve_redirect(url: str, timeout: float = 10) -> str | None:
    """Google の転送URLに HEAD を送り、転送先 (Location) だけを読む。転送先には接続しない。"""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "ai-mention-checker"})
    try:
        opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        return loc if loc and loc.startswith("http") else None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    return None


def source_from_chunk(web, resolve: bool) -> dict:
    """grounding chunk の web から {title, url, domain} を作る。

    Gemini API の出典URLは多くが Google の転送URLで、ドメインは web.domain か web.title に入る。
    """
    url = getattr(web, "uri", None) or ""
    title = getattr(web, "title", None) or ""
    dom = getattr(web, "domain", None) or ""
    host = domain_of(url)
    if host and not host.endswith(_REDIRECT_HOSTS):
        dom = dom or host
    if not dom and looks_like_domain(title):
        dom = title
    if not dom and resolve and host.endswith(_REDIRECT_HOSTS):
        loc = resolve_redirect(url)
        if loc:
            dom = domain_of(loc)
            url = loc
    return {"title": title, "url": url, "domain": domain_of(dom) if dom else ""}


def _retry_after(err) -> float | None:
    text = json.dumps(getattr(err, "details", None) or {}, ensure_ascii=False) + str(err)
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', text) or re.search(r"retry in (\d+(?:\.\d+)?)s", text)
    return float(m.group(1)) if m else None


def _is_daily(err) -> bool:
    text = (json.dumps(getattr(err, "details", None) or {}, ensure_ascii=False) + str(err)).lower()
    return "perday" in text or "per day" in text or "per_day" in text


def _is_network_error(e: BaseException) -> bool:
    """httpx / httpcore (SDK によって httpx2 / httpcore2) の通信エラーか、OS の接続エラー。"""
    if isinstance(e, (ConnectionError, TimeoutError)):
        return True
    mod = type(e).__module__.split(".")[0]
    return mod.startswith(("httpx", "httpcore")) and any(
        w in type(e).__name__ for w in ("Error", "Timeout"))


class GeminiEngine(Engine):
    name = "gemini"
    supports_grounding = True

    def __init__(self, api_key: str | None = None, resolve_redirects: bool = True, timeout: float = 300):
        # timeout はサーバー側の締め切りにもなる (短いと混雑時に 504 DEADLINE_EXCEEDED になる)
        from google import genai  # 遅延 import: テストではSDKを読み込まない

        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise EngineError("GEMINI_API_KEY がありません。.env に書いてください (README 参照)。")
        from google.genai import types

        # 1回の通信は最大 timeout 秒で打ち切る。再試行は SDK に任せず limiter.py がやる
        # (SDK と二重に再試行すると、混雑時に何分も止まったように見える)。
        self._client = genai.Client(api_key=key, http_options=types.HttpOptions(
            timeout=int(timeout * 1000), retry_options=types.HttpRetryOptions(attempts=1)))
        self._resolve = resolve_redirects

    def generate(self, model, prompt, *, grounding=False, json_schema=None):
        from google.genai import errors, types

        kw = {}
        if grounding:
            kw["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        if json_schema is not None:
            kw["response_mime_type"] = "application/json"
            kw["response_json_schema"] = json_schema
        try:
            resp = self._client.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(**kw) if kw else None)
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code == 429:
                if _is_daily(e):
                    raise DailyQuotaError(str(e)) from e
                if grounding and _retry_after(e) is None and "quotaid" not in str(e).lower() \
                        and "quotaid" not in json.dumps(getattr(e, "details", None) or {}).lower():
                    # 2026-11 の実測: 無料枠のキーで検索ありを頼むと、待ち時間もクォータ名も無い 429 が返る
                    raise GroundingUnavailable(
                        f"{model} では、このキーの無料枠で Google 検索 (検索あり) を使えません。"
                        "設定の ask.modes を [plain] にしてください (README の「無料枠について」)。") from e
                raise TransientError(str(e), _retry_after(e)) from e
            if code == 404 and "no longer available" in str(e):
                raise EngineError(f"{model} は新しいユーザーには提供されていません。設定の models を変えてください。") from e
            if code is not None and code >= 500:
                raise TransientError(str(e)) from e
            raise EngineError(f"{code}: {e}") from e
        except Exception as e:  # noqa: BLE001
            if _is_network_error(e):  # 接続が切れた・タイムアウト -> やり直せば通るかもしれない
                raise TransientError(f"通信エラー: {type(e).__name__}: {e}") from e
            raise

        text = resp.text or ""
        sources, queries = [], []
        cand = (resp.candidates or [None])[0]
        gm = getattr(cand, "grounding_metadata", None) if cand else None
        if gm:
            queries = list(gm.web_search_queries or [])
            for ch in gm.grounding_chunks or []:
                if getattr(ch, "web", None):
                    sources.append(source_from_chunk(ch.web, self._resolve))
        if not text and cand is not None:
            reason = getattr(cand, "finish_reason", None)
            text = ""
            if reason and str(reason) not in ("FinishReason.STOP", "STOP"):
                raise EngineError(f"空の回答 (finish_reason={reason})")
        return Result(text=text, model=getattr(resp, "model_version", None) or model,
                      sources=sources, search_queries=queries)
