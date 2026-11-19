"""Load config/target.yaml and fill in defaults (F-01)."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "target": {
        "name": "",
        "aliases": [],          # 略称・英語名・サービス名
        "ambiguous_aliases": [],  # 同じ名前の別物がありそうな別名 -> hits become 要確認
        "domains": [],          # 公式サイトのドメイン (example.co.jp)
        "industry": "",
        "region": "",
        "notes": "",            # 質問生成のヒント (公開情報だけ)
    },
    "questions": {
        "count": 20,
        "file": "config/questions.yaml",  # 人が直して使い回す質問セット (F-03)
    },
    "ask": {
        # 検索なしだけ。2026-11 時点、新しいキーの無料枠では検索ありを使えない (README)
        "modes": ["plain"],
        "repeats": 1,                     # N-06
    },
    "extract": {
        "modes": "all",   # 抽出する調べ先。回数が足りないときは [chatgpt, ...] のように絞る
    },
    "models": {
        "questions": "gemini-3.5-flash",
        "grounded": "gemini-3.8-flash",
        "plain": "gemini-3.8-flash",
        "extract": "gemini-3.5-flash-lite",
    },
    # 上限は AI Studio の Rate limit 画面で確認した値を書く (N-02)。モデルごと。
    "limits": {
        "default": {"rpm": 5, "rpd": 20},
        "grounding_rpd": 500,
        "retries": 3,
        "backoff_seconds": 15,
    },
    "sources": {
        "resolve_redirects": True,  # 出典URLがGoogleの転送URLのとき、転送先のドメインを調べる
    },
    "runs_dir": "runs",
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigError(ValueError):
    pass


def load(path: str | Path) -> dict:
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    cfg = _merge(DEFAULTS, raw)
    t = cfg["target"]
    if not t["name"]:
        raise ConfigError("target.name is required")
    if not t["industry"]:
        raise ConfigError("target.industry is required")
    t["aliases"] = [a for a in t["aliases"] if a]
    t["domains"] = [d.lower().removeprefix("www.") for d in t["domains"] if d]
    n = cfg["questions"]["count"]
    if not (1 <= int(n) <= 50):
        raise ConfigError("questions.count must be 1..50")
    from .surfaces import SURFACES
    if not cfg["ask"]["modes"]:
        raise ConfigError("ask.modes が空です")
    for m in cfg["ask"]["modes"]:
        if m not in SURFACES:
            raise ConfigError(f"ask.modes の「{m}」は知らない調べ先です。使えるもの: {', '.join(SURFACES)}")
        if SURFACES[m]["kind"] == "api" and not cfg["models"].get(m):
            raise ConfigError(f"models.{m} にモデル名を書いてください")
    cfg["_path"] = str(path)
    return cfg


def resolve(cfg: dict, rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def limits_for(cfg: dict, model: str) -> dict:
    lim = cfg["limits"]
    return {**lim["default"], **lim.get(model, {})}
