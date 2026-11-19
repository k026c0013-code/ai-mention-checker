"""回数制限・再試行 (N-02, N-03)。

- 1分あたり (rpm) と1日あたり (rpd) の上限はモデルごとの設定値。
- 使った回数は runs/usage.json に、太平洋時間の日付ごとに残す (Gemini の1日の上限は太平洋時間の0時で戻る)。
- 429 や 5xx は、待ち時間を増やしながら最大 retries 回やり直す。1日の上限なら、すぐ止める。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .engines.base import DailyQuotaError, TransientError

PT = ZoneInfo("America/Los_Angeles")
GROUNDING = "_grounding"


class QuotaExhausted(Exception):
    """設定した1日の上限に達した、または API が1日の上限を返した。"""


class Limiter:
    def __init__(self, usage_path: Path, limits: dict, *, sleep=time.sleep, clock=time.monotonic,
                 now=lambda: datetime.now(PT), log=print):
        self.usage_path = Path(usage_path)
        self.limits = limits
        self.sleep, self.clock, self.now, self.log = sleep, clock, now, log
        self._last: dict[str, float] = {}
        self._good: dict[tuple, str] = {}  # モデルの候補 -> 最後に通ったモデル

    # ---- usage file ----
    def _today(self) -> str:
        return self.now().astimezone(PT).strftime("%Y-%m-%d")

    def _load(self) -> dict:
        try:
            return json.loads(self.usage_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def used(self, key: str) -> int:
        return self._load().get(self._today(), {}).get(key, 0)

    def _add(self, key: str, n: int = 1) -> None:
        data = self._load()
        day = data.setdefault(self._today(), {})
        day[key] = day.get(key, 0) + n
        # 古い日付は7日分だけ残す
        for d in sorted(data)[:-7]:
            data.pop(d)
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.usage_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.usage_path)

    def lim(self, model: str) -> dict:
        return {**self.limits["default"], **self.limits.get(model, {})}

    def remaining(self, model) -> int:
        if not isinstance(model, str):  # 候補のリスト -> 合計
            return sum(self.remaining(m) for m in model)
        return max(0, int(self.lim(model)["rpd"]) - self.used(model))

    def remaining_grounding(self) -> int:
        return max(0, int(self.limits.get("grounding_rpd", 0)) - self.used(GROUNDING))

    # ---- call ----
    def call(self, model: str, fn, *, grounding: bool = False, retries: int | None = None):
        retries = int(self.limits.get("retries", 3)) if retries is None else retries
        backoff = float(self.limits.get("backoff_seconds", 15))
        attempt = 0
        while True:
            if self.remaining(model) <= 0:
                raise QuotaExhausted(f"{model}: 設定した1日の上限 ({self.lim(model)['rpd']}回) に達しました")
            if grounding and self.remaining_grounding() <= 0:
                raise QuotaExhausted("検索ありの1日の上限に達しました")
            self._pace(model)
            self._last[model] = self.clock()
            try:
                result = fn()
            except DailyQuotaError as e:
                raise QuotaExhausted(f"{model}: APIが1日の上限を返しました ({_short(e)})") from e
            except TransientError as e:
                attempt += 1
                if attempt > retries:
                    raise
                wait = max(e.retry_after or 0, backoff * 2 ** (attempt - 1))
                self.log(f"  一時的なエラー ({_short(e)})。{wait:.0f}秒待って再試行 {attempt}/{retries}")
                self.sleep(wait)
                continue
            self._add(model)
            if grounding:
                self._add(GROUNDING)
            return result

    def call_models(self, models, make_fn, *, grounding: bool = False):
        """models を順に試す。混雑 (再試行しても通らない一時的なエラー) や1日の上限なら、次のモデルへ。

        make_fn(model) は、そのモデルで呼ぶ関数を返す。戻り値は (使ったモデル, 結果)。
        """
        models = [models] if isinstance(models, str) else list(models)
        group = tuple(models)
        good = self._good.get(group)
        if good in models:  # 前に通ったモデルを先に試す
            models = [good] + [m for m in models if m != good]
        last = None
        for i, m in enumerate(models):
            # 次の候補があるときは、混雑したモデルを長く待たない (1回だけやり直して次へ)
            retries = 1 if i + 1 < len(models) else None
            try:
                result = self.call(m, make_fn(m), grounding=grounding, retries=retries)
                self._good[group] = m
                return m, result
            except (TransientError, QuotaExhausted) as e:
                last = e
                if i + 1 < len(models):
                    self.log(f"  {m} が使えないため、{models[i + 1]} に切り替えます ({_short(e, 80)})")
        raise last

    def _pace(self, model: str) -> None:
        rpm = float(self.lim(model).get("rpm") or 0)
        if rpm <= 0 or model not in self._last:
            return
        gap = 60.0 / rpm - (self.clock() - self._last[model])
        if gap > 0:
            self.sleep(gap)


def _short(e: Exception, n: int = 160) -> str:
    s = " ".join(str(e).split())
    return s if len(s) <= n else s[:n] + "…"
