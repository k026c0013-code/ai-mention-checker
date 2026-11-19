"""③ 会社名の抽出 (F-05)。Flash-Lite に JSON で出させ、mentions の材料として extracted.jsonl に残す。

LLM の答えはまだ信用しない。数字を決めるのは ④ judge.py。
"""
from __future__ import annotations

import json
from pathlib import Path

from .store import append_jsonl, key, now_iso, read_jsonl

SCHEMA = {
    "type": "object",
    "properties": {
        "companies": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        }
    },
    "required": ["companies"],
}

PROMPT = """次の <answer> の文章に、おすすめや候補として名前が出てくる会社・事務所・団体・サービスを、
出てきた順にすべて抜き出してください。

- 文章に書かれている表記のまま抜き出す（言い換え・補完・推測をしない）
- 同じものが何度出ても1回だけ（最初に出た位置で数える）
- 「〇〇などの大手」のような一般的な言い方、人名、地名、Webサイトの種類は入れない
- 1つも無ければ空の配列

JSON で {{"companies": [{{"name": "..."}}]}} の形だけを返してください。

<answer>
{answer}"""


def run(answers_path: Path, extracted_path: Path, cfg: dict, engine, limiter, log=print) -> int:
    done = {key(r) for r in read_jsonl(extracted_path)}
    todo = [a for a in read_jsonl(answers_path) if wants(cfg, a["mode"]) and key(a) not in done]
    model = cfg["models"]["extract"]
    for i, a in enumerate(todo, 1):
        log(f"  [{i}/{len(todo)}] 抽出 {a['question_id']} {a['mode']}")
        if not a["answer"].strip():
            names, ok = [], True
        else:
            prompt = PROMPT.format(answer=a["answer"])
            used, res = limiter.call_models(
                model, lambda m: (lambda: engine.generate(m, prompt, json_schema=SCHEMA)))
            names, ok = parse(res.text)
        append_jsonl(extracted_path, {
            "question_id": a["question_id"], "mode": a["mode"], "repeat": a.get("repeat", 1),
            "extract_model": used if a["answer"].strip() else None, "extracted_at": now_iso(), "names": names, "parse_ok": ok,
        })
    return len(todo)


def wants(cfg: dict, mode: str) -> bool:
    modes = cfg["extract"]["modes"]
    return modes == "all" or mode in modes


def parse(text: str) -> tuple[list[str], bool]:
    try:
        data = json.loads(text)
        items = data["companies"] if isinstance(data, dict) else data
        names = []
        for it in items:
            n = it.get("name") if isinstance(it, dict) else it
            if isinstance(n, str) and n.strip():
                names.append(n.strip())
        return names, True
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return [], False
