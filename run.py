"""①〜⑤をまとめて実行する。

    python run.py --config config/target.yaml            # 続きがあれば続きから、無ければ新しく
    python run.py --config config/target.yaml --new      # 前の実行が途中でも、新しく始める
    python run.py --config config/target.yaml --questions-only   # ①だけ (質問を作って直したいとき)
    python run.py --config config/target.yaml --engine fake      # APIを使わないデモ
    python run.py --report runs/<対象>/<日時>                     # レポートだけ作り直す

終了コード: 0 = 完了 / 2 = 1日の上限で止まった (翌日もう一度実行すると続きから) / 1 = エラー
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src import ask, capture, extract, generate_questions, judge, report, surfaces  # noqa: E402
from src.config import ConfigError, load, resolve  # noqa: E402
from src.engines.base import EngineError, TransientError  # noqa: E402
from src.limiter import Limiter, QuotaExhausted  # noqa: E402
from src.store import key, now_iso, read_json, read_jsonl, write_json  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def slug(name: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("_")
    return s or "target"


def make_engine(kind: str, cfg: dict):
    if kind == "fake":
        from src.engines.fake import FakeEngine
        t = cfg["target"]
        return FakeEngine(t["name"], (t["domains"] or ["target.example"])[0])
    from src.engines.gemini import GeminiEngine
    return GeminiEngine(resolve_redirects=cfg["sources"]["resolve_redirects"])


def fake_capture_io(engine) -> dict:
    """--engine fake のデモ用。人がコピーする代わりに、偽のエンジンの回答をクリップボードに入れたことにする。"""
    st = {"q": "", "url": "", "clip": ""}

    def clip_set(t):
        st["q"] = st["clip"] = t

    def open_fn(url):
        st["url"] = url

    def input_fn(prompt=""):
        r = engine.generate("fake", st["q"] + "|" + st["url"], grounding=True)
        links = "\n".join(f"[{s['title']}]({s['url']})" for s in r.sources)
        st["clip"] = r.text + "\n" + links
        return ""

    return dict(input_fn=input_fn, open_fn=open_fn, clip_get=lambda: st["clip"], clip_set=clip_set,
                log=lambda *a: None)


def qhash(questions: list[dict]) -> str:
    return hashlib.sha1("\n".join(f'{q["id"]}\t{q["text"]}' for q in questions).encode()).hexdigest()[:10]


def find_unfinished(base: Path) -> Path | None:
    if not base.exists():
        return None
    for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        st = read_json(d / "run.json", {})
        if st and st.get("status") != "done":
            return d
    return None


def _mkey(m) -> tuple:
    return (m,) if isinstance(m, str) else tuple(m)


def estimate(cfg: dict, n_jobs_by_mode: dict, n_extract: int) -> dict:
    """{(モデルの候補...): 必要な回数}"""
    need: dict[tuple, int] = {}
    for mode, n in n_jobs_by_mode.items():
        if surfaces.is_manual(mode) or not n:
            continue
        k = _mkey(cfg["models"][mode])
        need[k] = need.get(k, 0) + n
    if n_extract:
        k = _mkey(cfg["models"]["extract"])
        need[k] = need.get(k, 0) + n_extract
    return need


def build_outputs(run_dir: Path, cfg: dict, log=print) -> Path:
    st = read_json(run_dir / "run.json", {})
    questions = generate_questions.load(run_dir / "questions.yaml")
    answers = read_jsonl(run_dir / "answers.jsonl")
    extracted = {key(x): x for x in read_jsonl(run_dir / "extracted.jsonl")}
    mentions = [judge.judge_one(a, extracted.get(key(a)), cfg["target"]) for a in answers]
    (run_dir / "mentions.jsonl").write_text(
        "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in mentions), encoding="utf-8")
    summary = judge.summarize(mentions, answers, cfg["target"], order=cfg["ask"]["modes"])
    summary.update({"target": cfg["target"]["name"], "questions": len(questions),
                    "question_set": qhash(questions), "started_at": st.get("started_at"),
                    "engine": st.get("engine")})
    write_json(run_dir / "summary.json", summary)
    report.write_csv(run_dir / "details.csv", questions, mentions)

    hist = []
    for d in sorted(p for p in run_dir.parent.iterdir() if p.is_dir()):
        s = read_json(d / "summary.json")
        if not s or s.get("question_set") != summary["question_set"] or s.get("engine") != summary["engine"]:
            continue
        hist.append({"label": d.name[5:10].replace("-", "/") if len(d.name) >= 10 else d.name,
                     "rates": {m: v["mention_rate"] for m, v in s["by_mode"].items()}})
    html = report.render({**st, "dir": str(run_dir.relative_to(ROOT)) if run_dir.is_relative_to(ROOT) else str(run_dir)},
                         cfg, questions, answers, mentions, summary, hist)
    out = run_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    return out


def capture_only(cfg: dict, base: Path, capture_io: dict | None) -> int:
    """途中の実行の、本物の画面からの取り込みだけをする。API・抽出・レポートはしない。

    回答は answers.jsonl に1件ずつ追記するだけなので、API の実行と並行しても壊れない。
    """
    run_dir = find_unfinished(base / slug(cfg["target"]["name"]))
    if run_dir is None:
        print("途中の実行がありません。先に python run.py --config ... を実行してください。")
        return 1
    questions = generate_questions.load(run_dir / "questions.yaml")
    jobs = ask.plan(questions, cfg)
    todo = [j for j in ask.pending(jobs, run_dir / "answers.jsonl") if surfaces.is_manual(j["mode"])]
    print(f"実行フォルダ: {run_dir}\n取り込み: 残り {len(todo)}件")
    if not todo:
        return 0
    try:
        res = capture.run(todo, run_dir / "answers.jsonl", **(capture_io or {}))
    except KeyboardInterrupt:
        res = "quit"
    left = [j for j in ask.pending(jobs, run_dir / "answers.jsonl") if surfaces.is_manual(j["mode"])]
    print(f"\n取り込み: 残り {len(left)}件。" + ("続きは同じコマンドで。" if left else
          "全部終わりました。python run.py --config ... で抽出とレポートを作ります。"))
    return 0 if res == "done" and not left else 3


def main(argv=None, capture_io: dict | None = None) -> int:
    """capture_io はテスト用 (input_fn / open_fn / clip_get / clip_set を差し替える)。"""
    ap = argparse.ArgumentParser(description="AI言及チェッカー")
    ap.add_argument("--config", default="config/target.yaml")
    ap.add_argument("--engine", default="gemini", choices=["gemini", "fake"])
    ap.add_argument("--new", action="store_true", help="途中の実行があっても新しく始める")
    ap.add_argument("--questions-only", action="store_true", help="①質問を作って保存するだけ")
    ap.add_argument("--regen-questions", action="store_true", help="質問ファイルがあっても作り直す")
    ap.add_argument("--report", metavar="RUN_DIR", help="この実行フォルダのレポートだけ作り直す")
    ap.add_argument("--capture-only", action="store_true",
                    help="本物の画面からの取り込みだけをする (API は使わない。API の実行中に並行してできる)")
    ap.add_argument("--no-capture", action="store_true",
                    help="本物の画面からの取り込み (手入力) をしない。API の分だけ回す (自動実行用)")
    args = ap.parse_args(argv)
    load_dotenv(ROOT / ".env")

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute() and not cfg_path.exists():
        cfg_path = ROOT / cfg_path
    try:
        cfg = load(cfg_path)
    except (ConfigError, FileNotFoundError) as e:
        print(f"設定ファイルのエラー: {e}")
        return 1

    if args.report:
        out = build_outputs(Path(args.report).resolve(), cfg)
        print(f"レポート: {out}")
        return 0

    base = resolve(cfg, cfg["runs_dir"])
    if args.capture_only:
        return capture_only(cfg, base, capture_io)
    usage = base / "usage.json"
    limiter = Limiter(usage, cfg["limits"])
    try:
        engine = make_engine(args.engine, cfg)
    except EngineError as e:
        print(e)
        return 1
    if args.engine == "fake":
        base = base / "_demo"
        limiter = Limiter(base / "usage.json", {"default": {"rpm": 0, "rpd": 10**6},
                                                 "grounding_rpd": 10**6, "retries": 0})
    tdir = base / slug(cfg["target"]["name"])

    try:
        # ① 質問
        qfile = resolve(cfg, cfg["questions"]["file"])
        run_dir = None if args.new else find_unfinished(tdir)
        if run_dir is None:
            if args.regen_questions or not qfile.exists():
                print(f"① 質問を作ります ({cfg['questions']['count']}個、{cfg['models']['questions']})")
                qs = generate_questions.generate(cfg, engine, limiter)
                if args.engine == "fake":
                    qfile = base / "questions.yaml"
                generate_questions.save(qfile, qs, cfg)
                print(f"  保存: {qfile}")
            else:
                print(f"① 質問ファイルを使います: {qfile}")
            if args.questions_only:
                print("  質問を確認・修正してから、--questions-only を付けずにもう一度実行してください。")
                return 0
            questions = generate_questions.load(qfile)
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            run_dir, n = tdir / stamp, 2
            while run_dir.exists():
                run_dir, n = tdir / f"{stamp}-{n}", n + 1
            run_dir.mkdir(parents=True)
            shutil.copyfile(qfile, run_dir / "questions.yaml")
            write_json(run_dir / "run.json", {"status": "running", "started_at": now_iso(),
                                              "engine": args.engine, "config": cfg["_path"],
                                              "question_set": qhash(questions)})
        else:
            print(f"途中の実行の続きから回します: {run_dir}")
            questions = generate_questions.load(run_dir / "questions.yaml")
        print(f"  質問 {len(questions)} 問 / 実行フォルダ {run_dir}")

        # 回数の見積もり (N-02)
        jobs = ask.plan(questions, cfg)
        todo = ask.pending(jobs, run_dir / "answers.jsonl")
        by_mode = {m: sum(1 for j in todo if j["mode"] == m) for m in cfg["ask"]["modes"]}
        done_ext = {key(x) for x in read_jsonl(run_dir / "extracted.jsonl")}
        n_ext = sum(1 for j in jobs if extract.wants(cfg, j["mode"]) and key(j) not in done_ext)
        need = estimate(cfg, by_mode, n_ext)
        short = []
        for ms, n in need.items():
            left = limiter.remaining(list(ms))
            name = " → ".join(ms)
            print(f"  必要な回数 {name}: {n}回 (今日の残り {left}回)")
            if n > left:
                short.append(name)
        if by_mode.get("grounded"):
            print(f"  検索あり: {by_mode['grounded']}回 (今日の残り {limiter.remaining_grounding()}回)")
            if by_mode["grounded"] > limiter.remaining_grounding():
                short.append("grounding")
        if short:
            print(f"  警告: 今日の残り回数が足りません ({', '.join(short)})。上限に達したら止まり、翌日の再実行で続きから回ります。")

        manual_n = sum(n for m, n in by_mode.items() if surfaces.is_manual(m))
        if manual_n:
            print(f"  本物の画面からの取り込み: {manual_n}件 (人の操作が必要)")

        # ② 問い合わせ (API)
        if any(n for m, n in by_mode.items() if not surfaces.is_manual(m)):
            print("② AIに質問します (API)")
            ask.run(jobs, cfg, engine, limiter, run_dir / "answers.jsonl")
        # ② 取り込み (本物の画面)
        manual_todo = [j for j in ask.pending(jobs, run_dir / "answers.jsonl") if surfaces.is_manual(j["mode"])]
        if manual_todo:
            if capture_io is None and args.engine == "fake" and not args.no_capture:
                capture_io = fake_capture_io(engine)
            interactive = capture_io is not None or (sys.stdin.isatty() and not args.no_capture)
            if interactive:
                print("② 本物のAI検索の画面から回答を取り込みます")
                capture.run(manual_todo, run_dir / "answers.jsonl", **(capture_io or {}))
            else:
                print(f"  本物の画面からの取り込みが {len(manual_todo)}件 残っています (--no-capture のため飛ばしました)")
        # ③ 抽出
        print(f"③ 会社名を抽出します ({cfg['models']['extract']})")
        extract.run(run_dir / "answers.jsonl", run_dir / "extracted.jsonl", cfg, engine, limiter)
    except QuotaExhausted as e:
        print(f"\n止めました: {e}\n翌日 (太平洋時間の0時 = 日本時間の17時ごろ以降) にもう一度実行すると、続きから回ります。")
        return 2
    except (EngineError, TransientError) as e:
        print(f"\nAPIのエラーで止めました: {e}\nもう一度実行すると、終わった分は飛ばして続きから回ります。")
        return 1
    except KeyboardInterrupt:
        print("\n中断しました。もう一度実行すると続きから回ります。")
        return 130

    # ④⑤
    print("④ 判定・集計 / ⑤ レポート")
    out = build_outputs(run_dir, cfg)
    left = ask.pending(jobs, run_dir / "answers.jsonl")
    s = read_json(run_dir / "summary.json")
    for m, v in s["by_mode"].items():
        print(f"  {surfaces.label(m)}: 言及率 {v['mention_rate']}% ({v['answers']}回答中 {v['mentioned']})")
    print(f"レポート: {out}")
    if left:
        print(f"\nまだ {len(left)}件 残っています (レポートはここまでの分)。もう一度実行すると続きから回ります。")
        return 3
    st = read_json(run_dir / "run.json", {})
    st.update({"status": "done", "finished_at": now_iso()})
    write_json(run_dir / "run.json", st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
