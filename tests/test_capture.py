"""本物の画面からの取り込み (capture.py)。ブラウザとクリップボードは偽物に差し替える。"""
import json

import yaml

import run as runmod
from src import capture, surfaces


def test_parse_paste_markdown_and_bare_urls():
    text = ("おすすめは次の会社です。\n1. **株式会社ミライ**（[mirai.example.jp](https://mirai.example.jp/?utm_source=chatgpt.com)）\n"
            "2. ミドリ株式会社 https://www.midori.example.co.jp/service\n"
            "出典: https://hikaku.example.com/ranking#top")
    body, src = capture.parse_paste(text)
    assert "http" not in body  # URL の文字列で「出た」と数えない
    assert "mirai.example.jp" in body and "ミドリ株式会社" in body
    assert [s["domain"] for s in src] == ["mirai.example.jp", "midori.example.co.jp", "hikaku.example.com"]


def test_image_links_are_not_sources():
    # 2026-11-19 の ChatGPT の取り込みで実際に混ざった形
    text = ("おすすめはTechSuite株式会社です。\n"
            "[記事のタイトル](https://images.openai.com/static-rsc-4/abc)\n"
            "![image](https://www.google.com/s2/favicons?domain=https://ai-search.techsuite.co.jp\\&sz=32)\n"
            "[TechSuite](https://ai-search.techsuite.co.jp/) https://example.com/logo.png")
    body, src = capture.parse_paste(text)
    assert [s["domain"] for s in src] == ["ai-search.techsuite.co.jp"]
    assert "favicons" not in body


def test_url_for_encodes_question():
    u = surfaces.url_for("google_ai_mode", "東京 おすすめ&比較")
    assert u.startswith("https://www.google.com/search?udm=50&q=") and " " not in u and "&比較" not in u


class FakeIO:
    def __init__(self, script, clips):
        self.script, self.clips = list(script), list(clips)
        self.opened, self.clip = [], ""

    def input_fn(self, prompt=""):
        cmd = self.script.pop(0)
        if cmd == "" and self.clips:
            self.clip = self.clips.pop(0)  # 人が「コピー」ボタンを押したことにする
        return cmd

    def clip_get(self):
        return self.clip

    def clip_set(self, t):
        self.clip = t

    def kw(self):
        return dict(input_fn=self.input_fn, open_fn=self.opened.append, clip_get=self.clip_get,
                    clip_set=self.clip_set, log=lambda *a: None)


def jobs():
    return [{"question_id": "q01", "question": "おすすめのAI検索対策会社は？", "mode": "chatgpt", "repeat": 1},
            {"question_id": "q02", "question": "中小企業向けのAI検索対策会社は？", "mode": "chatgpt", "repeat": 1},
            {"question_id": "q01", "question": "おすすめのAI検索対策会社は？", "mode": "google_ai_mode", "repeat": 1}]


def test_capture_flow(tmp_path):
    p = tmp_path / "answers.jsonl"
    ans1 = "1. 株式会社ミライ\n2. ミドリ株式会社 https://midori.example.co.jp/"
    io = FakeIO(script=["", "n", ""], clips=[ans1, "AIモードの回答: 株式会社シグナルが有名です。ほかにもあります。"])
    assert capture.run(jobs(), p, **io.kw()) == "done"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    assert [(r["question_id"], r["mode"]) for r in rows] == [("q01", "chatgpt"), ("q02", "chatgpt"), ("q01", "google_ai_mode")]
    assert rows[0]["sources"][0]["domain"] == "midori.example.co.jp" and rows[0]["raw_paste"] == ans1
    assert rows[1]["no_answer"] and rows[1]["answer"] == ""
    assert io.opened[0].startswith("https://chatgpt.com/?q=")


def test_capture_rejects_question_and_duplicate(tmp_path):
    p = tmp_path / "answers.jsonl"
    same = "同じ回答です。株式会社ミライがおすすめです。"
    io = FakeIO(script=[], clips=[])
    # (Enter を押す前にクリップボードをどうするか, 押すキー)
    steps = [
        (None, ""),   # 1問目: コピーせずに Enter -> クリップボードは質問のまま -> 断られる
        (same, ""),   # 1問目: 回答をコピーして Enter -> 取り込む
        (same, ""),   # 2問目: 前と同じ回答のまま Enter -> 断られる
        (None, "q"),  # やめる
    ]

    def input_fn(prompt=""):
        clip, cmd = steps.pop(0)
        if clip is not None:
            io.clip = clip
        return cmd

    kw = io.kw()
    kw["input_fn"] = input_fn
    assert capture.run(jobs(), p, **kw) == "quit"
    assert not steps
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["question_id"] == "q01"


def test_pipeline_with_manual_surface(tmp_path):
    cfg = yaml.safe_load((runmod.ROOT / "config/target.example.yaml").read_text(encoding="utf-8"))
    cfg["questions"] = {"count": 2, "file": str(tmp_path / "questions.yaml")}
    cfg["runs_dir"] = str(tmp_path / "runs")
    cfg["ask"]["modes"] = ["chatgpt", "plain"]
    cfgp = tmp_path / "t.yaml"
    cfgp.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    pastes = ["1. 株式会社サンプル（[sample.co.jp](https://sample.co.jp/)）\n2. 株式会社ミライ",
              "おすすめは株式会社ミライと株式会社シグナルです。どちらも実績があります。"]
    io = FakeIO(script=["", ""], clips=pastes)
    assert runmod.main(["--config", str(cfgp), "--engine", "fake"], capture_io=io.kw()) == 0
    base = tmp_path / "runs" / "_demo"
    (d,) = [x for t in base.iterdir() if t.is_dir() for x in t.iterdir() if x.is_dir()]
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))["by_mode"]
    assert list(s) == ["chatgpt", "plain"]
    assert s["chatgpt"]["answers"] == 2 and s["chatgpt"]["mentioned"] == 1
    assert s["chatgpt"]["own_site_cited"] == 1 and s["chatgpt"]["sources"][0]["domain"] == "sample.co.jp"
    html = (d / "report.html").read_text(encoding="utf-8")
    assert "ChatGPT（検索）" in html and "実際の画面に質問して" in html


def test_capture_only_then_finish(tmp_path):
    cfg = yaml.safe_load((runmod.ROOT / "config/target.example.yaml").read_text(encoding="utf-8"))
    cfg["questions"] = {"count": 2, "file": str(tmp_path / "questions.yaml")}
    cfg["runs_dir"] = str(tmp_path / "runs")
    cfg["ask"]["modes"] = ["chatgpt", "plain"]
    cfgp = tmp_path / "t.yaml"
    cfgp.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert runmod.main(["--config", str(cfgp), "--engine", "fake", "--no-capture"]) == 3  # API の分だけ
    # 本番は runs/<対象>、fake は runs/_demo/<対象>。capture-only は本番の場所を見るので、runs_dir を合わせる
    cfg["runs_dir"] = str(tmp_path / "runs" / "_demo")
    cfgp.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    io = FakeIO(script=["", ""], clips=["株式会社サンプルと株式会社ミライがおすすめです。どちらも実績があります。",
                                        "株式会社シグナルがおすすめです。料金も分かりやすいです。"])
    assert runmod.main(["--config", str(cfgp), "--capture-only"], capture_io=io.kw()) == 0
    cfg["runs_dir"] = str(tmp_path / "runs")
    cfgp.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    assert runmod.main(["--config", str(cfgp), "--engine", "fake", "--no-capture"]) == 0


def test_pipeline_pending_manual_returns_3(tmp_path):
    cfg = yaml.safe_load((runmod.ROOT / "config/target.example.yaml").read_text(encoding="utf-8"))
    cfg["questions"] = {"count": 2, "file": str(tmp_path / "questions.yaml")}
    cfg["runs_dir"] = str(tmp_path / "runs")
    cfg["ask"]["modes"] = ["plain", "perplexity"]
    cfgp = tmp_path / "t.yaml"
    cfgp.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    # 取り込みをしない (自動実行) -> API の分だけ終わって、3 で終わる
    assert runmod.main(["--config", str(cfgp), "--engine", "fake", "--no-capture"]) == 3
