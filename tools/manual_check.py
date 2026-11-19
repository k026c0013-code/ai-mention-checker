"""完了条件「回答を人が10件読んで数えた結果と、ツールの判定が一致する」を確かめる。

    python tools/manual_check.py runs/<対象>/<日時> --make        # check_sheet.csv を作る (10件、ツールの答えは伏せる)
    (Excel などで human 列に 1=出た / 0=出ていない を書く)
    python tools/manual_check.py runs/<対象>/<日時> --compare     # ツールの判定と突き合わせる
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--make", action="store_true")
    g.add_argument("--compare", action="store_true")
    ap.add_argument("-n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    d = Path(a.run_dir)
    sheet = d / "check_sheet.csv"

    if a.make:
        answers = rows(d / "answers.jsonl")
        mentions = {(m["question_id"], m["mode"], m.get("repeat", 1)): m for m in rows(d / "mentions.jsonl")}
        # 出た/出ていないが半々くらいになるように選ぶ (片方だけだと確かめにならない)
        rnd = random.Random(a.seed)
        hit = [x for x in answers if mentions.get((x["question_id"], x["mode"], x.get("repeat", 1)), {}).get("mentioned")]
        miss = [x for x in answers if x not in hit]
        rnd.shuffle(hit)
        rnd.shuffle(miss)
        k = min(len(hit), a.n // 2)
        pick = hit[:k] + miss[:a.n - k]
        if len(pick) < a.n:
            pick += hit[k:k + a.n - len(pick)]
        rnd.shuffle(pick)
        with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["question_id", "mode", "repeat", "human", "question", "answer"])
            for x in pick:
                w.writerow([x["question_id"], x["mode"], x.get("repeat", 1), "", x["question"], x["answer"]])
        print(f"作りました: {sheet}\nhuman 列に 1 (対象が出た) か 0 (出ていない) を書いて保存し、--compare を実行してください。")
        return 0

    mentions = {(m["question_id"], m["mode"], str(m.get("repeat", 1))): m for m in rows(d / "mentions.jsonl")}
    agree, total, diff = 0, 0, []
    with open(sheet, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            h = (r["human"] or "").strip()
            if h not in ("0", "1"):
                continue
            m = mentions[(r["question_id"], r["mode"], r["repeat"])]
            total += 1
            if int(h) == int(m["mentioned"]):
                agree += 1
            else:
                diff.append(f'{r["question_id"]} {r["mode"]}: 人={h} ツール={int(m["mentioned"])} {m.get("review_reasons") or ""}')
    if not total:
        print("human 列が埋まっていません")
        return 1
    print(f"一致: {agree}/{total}")
    for x in diff:
        print("  不一致", x)
    return 0 if agree == total else 1


if __name__ == "__main__":
    sys.exit(main())
