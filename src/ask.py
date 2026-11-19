"""② AIに質問 (F-04)。回答と出典をそのまま answers.jsonl に残す。終わった分は飛ばす (N-04)。"""
from __future__ import annotations

from pathlib import Path

from . import surfaces
from .store import append_jsonl, key, now_iso, read_jsonl


def plan(questions: list[dict], cfg: dict) -> list[dict]:
    jobs = []
    for q in questions:
        for mode in cfg["ask"]["modes"]:
            for r in range(1, int(cfg["ask"]["repeats"]) + 1):
                jobs.append({"question_id": q["id"], "question": q["text"], "mode": mode, "repeat": r})
    return jobs


def pending(jobs: list[dict], answers_path: Path) -> list[dict]:
    done = {key(r) for r in read_jsonl(answers_path)}
    return [j for j in jobs if key(j) not in done]


def run(jobs: list[dict], cfg: dict, engine, limiter, answers_path: Path, log=print) -> int:
    """API で聞く分だけを回す。手入力の調べ先は capture.py。"""
    todo = [j for j in pending(jobs, answers_path) if not surfaces.is_manual(j["mode"])]
    for i, j in enumerate(todo, 1):
        grounded = j["mode"] == "grounded"
        models = cfg["models"][j["mode"]]
        log(f"  [{i}/{len(todo)}] {j['question_id']} {'検索あり' if grounded else '検索なし'}: {j['question'][:40]}")
        model, res = limiter.call_models(
            models, lambda m: (lambda: engine.generate(m, j["question"], grounding=grounded)),
            grounding=grounded)
        append_jsonl(answers_path, {
            **j,
            "engine": f"{engine.name}:{model}",
            "model_version": res.model,
            "grounding": grounded,
            "asked_at": now_iso(),
            "answer": res.text,
            "sources": res.sources,
            "search_queries": res.search_queries,
        })
    return len(todo)
