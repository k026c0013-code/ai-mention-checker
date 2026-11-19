"""取り込んだ回答を、残しておいたコピーの原文 (raw_paste) から読み直す。

本文と出典の分け方 (capture.parse_paste) を直したときに使う。人がもう一度コピーしなくてよい。
元の answers.jsonl は answers.before_reparse.jsonl として残す。

    python tools/reparse_captures.py runs/<対象>/<日時>
    python run.py --config config/target.yaml --report runs/<対象>/<日時>
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.capture import parse_paste  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(run_dir: str) -> int:
    p = Path(run_dir) / "answers.jsonl"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    changed = 0
    for r in rows:
        raw = r.get("raw_paste")
        if not raw or r.get("no_answer"):
            continue
        body, sources = parse_paste(raw)
        if body != r["answer"] or sources != r["sources"]:
            changed += 1
            r["answer"], r["sources"] = body, sources
    if not changed:
        print("変更なし")
        return 0
    backup = p.with_name("answers.before_reparse.jsonl")
    if not backup.exists():
        shutil.copyfile(p, backup)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"{changed}件を読み直しました (元のファイル: {backup.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
