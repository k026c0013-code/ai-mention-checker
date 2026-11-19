"""問い合わせ先の共通の形 (N-09)。ChatGPT や Claude を足すときは、この形のクラスを1つ書く。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Result:
    text: str
    model: str
    sources: list[dict] = field(default_factory=list)   # [{"title", "url", "domain"}]
    search_queries: list[str] = field(default_factory=list)


class EngineError(Exception):
    """やり直しても無駄なエラー (APIキーが無効、モデル名の間違いなど)。"""


class GroundingUnavailable(EngineError):
    """このキー (無料枠) では Google 検索を使えない。"""


class TransientError(Exception):
    """少し待てば通るかもしれないエラー (429 の1分あたり上限、5xx)。"""

    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class DailyQuotaError(Exception):
    """1日の上限に当たった。翌日 (太平洋時間の0時以降) に続きから回す。"""


class Engine:
    name = "base"
    supports_grounding = False

    def generate(self, model: str, prompt: str, *, grounding: bool = False,
                 json_schema: dict | None = None) -> Result:
        raise NotImplementedError
