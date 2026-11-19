"""run.py を、APIを使わないエンジンで最初から最後まで通す。途中で落ちても続きから完了することを確かめる。"""
import json
import shutil
from pathlib import Path

import pytest
import yaml

import run as runmod
from src.engines.base import DailyQuotaError
from src.engines.fake import FakeEngine


@pytest.fixture
def cfgfile(tmp_path):
    cfg = yaml.safe_load((runmod.ROOT / "config/target.example.yaml").read_text(encoding="utf-8"))
    cfg["questions"] = {"count": 6, "file": str(tmp_path / "questions.yaml")}
    cfg["runs_dir"] = str(tmp_path / "runs")
    cfg["ask"]["modes"] = ["grounded", "plain"]  # 偽のエンジンは検索ありも返せる
    p = tmp_path / "target.yaml"
    p.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return p


def run_dirs(tmp_path):
    base = tmp_path / "runs" / "_demo"
    return sorted(d for t in base.iterdir() if t.is_dir() for d in t.iterdir() if d.is_dir())


def test_end_to_end(cfgfile, tmp_path, capsys):
    assert runmod.main(["--config", str(cfgfile), "--engine", "fake"]) == 0
    (d,) = run_dirs(tmp_path)
    for f in ["questions.yaml", "answers.jsonl", "extracted.jsonl", "mentions.jsonl", "summary.json",
              "report.html", "details.csv", "run.json"]:
        assert (d / f).exists(), f
    answers = [json.loads(l) for l in (d / "answers.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(answers) == 12 and {a["mode"] for a in answers} == {"grounded", "plain"}
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))
    assert s["by_mode"]["grounded"]["answers"] == 6
    html = (d / "report.html").read_text(encoding="utf-8")
    assert "ダミーデータ" in html and "<script" not in html


def test_crash_and_resume(cfgfile, tmp_path, monkeypatch):
    calls = {"n": 0}
    orig = FakeEngine.generate

    def crashing(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 6:
            raise KeyboardInterrupt  # 強制終了のまね
        return orig(self, *a, **k)

    monkeypatch.setattr(FakeEngine, "generate", crashing)
    assert runmod.main(["--config", str(cfgfile), "--engine", "fake"]) == 130
    (d,) = run_dirs(tmp_path)
    assert len((d / "answers.jsonl").read_text(encoding="utf-8").splitlines()) == 4  # ①1回 + ②4件

    monkeypatch.setattr(FakeEngine, "generate", orig)
    assert runmod.main(["--config", str(cfgfile), "--engine", "fake"]) == 0
    assert run_dirs(tmp_path) == [d]  # 新しいフォルダを作らず、同じ実行を完了した
    lines = (d / "answers.jsonl").read_text(encoding="utf-8").splitlines()
    keys = [(json.loads(l)["question_id"], json.loads(l)["mode"]) for l in lines]
    assert len(keys) == 12 == len(set(keys))  # 重複なし
    assert json.loads((d / "run.json").read_text(encoding="utf-8"))["status"] == "done"


def test_daily_quota_exit_code(cfgfile, tmp_path, monkeypatch):
    orig = FakeEngine.generate

    def quota(self, model, prompt, **k):
        if k.get("grounding"):
            raise DailyQuotaError("PerDay")
        return orig(self, model, prompt, **k)

    monkeypatch.setattr(FakeEngine, "generate", quota)
    assert runmod.main(["--config", str(cfgfile), "--engine", "fake"]) == 2


def test_history_and_report_rebuild(cfgfile, tmp_path):
    assert runmod.main(["--config", str(cfgfile), "--engine", "fake"]) == 0
    assert runmod.main(["--config", str(cfgfile), "--engine", "fake", "--new"]) == 0
    d1, d2 = run_dirs(tmp_path)
    assert "言及率の推移" in (d2 / "report.html").read_text(encoding="utf-8")
    (d2 / "report.html").unlink()
    assert runmod.main(["--config", str(cfgfile), "--report", str(d2)]) == 0
    assert (d2 / "report.html").exists()


def test_html_escapes_answer(tmp_path):
    from src import report
    cfg = {"target": {"name": "<b>x</b>", "industry": "i", "region": "", "domains": []},
           "ask": {"repeats": 1}}
    q = [{"id": "q01", "text": "<img src=x onerror=alert(1)>"}]
    a = [{"question_id": "q01", "mode": "plain", "answer": "<script>alert(1)</script>", "sources": []}]
    m = [{"question_id": "q01", "mode": "plain", "mentioned": False, "rank": None, "needs_review": False,
          "review_reasons": [], "cited_own_site": False, "companies": [], "source_domains": []}]
    s = {"by_mode": {"plain": {"answers": 1, "mentioned": 0, "mention_rate": 0.0, "avg_rank": None, "top3": 0,
                               "top3_rate": 0.0, "needs_review": 0, "competitors": [], "sources": [],
                               "own_site_cite_rate": None}}}
    html = report.render({}, cfg, q, a, m, s, [])
    assert "<script>" not in html and "<img" not in html and "<b>x" not in html
