"""First-run setup, the LLM client's parsing, and the Excel export."""

from __future__ import annotations

import json
import sys

import pytest

from timesplit.config import load_config
from timesplit.llm import client, prompt
from timesplit.store import repo_categories, repo_rules
from timesplit.store.db import open_database
from timesplit.wizard.firstrun import run_wizard

# ---- the wizard ----------------------------------------------------------


def answers(monkeypatch, responses: list[str]) -> None:
    queue = list(responses)

    def fake_input(_prompt: str = "") -> str:
        return queue.pop(0) if queue else ""

    monkeypatch.setattr("builtins.input", fake_input)


def test_wizard_writes_config_rules_and_categories(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path))
    answers(monkeypatch, [
        "Teaching",                     # first job name
        "Property",                     # second job name
        "high school biology teacher",  # first description
        "flipping and renting houses",  # second description
        "myschool.edu, gradebook.example.com",  # school sites
        "powerpnt, onenote",            # school programs
        "myrealty.com",                 # property sites
        "quickbooks",                   # property programs
        "5",                            # idle minutes
        "America/New_York",             # timezone
        "y",                            # add the seed pack
    ])

    assert run_wizard() == 0

    cfg = load_config()
    assert cfg.first_run_complete
    assert [c.display_name for c in cfg.categories] == ["Teaching", "Property"]
    assert cfg.categories[0].description == "high school biology teacher"
    assert cfg.idle.threshold_s == 300
    assert cfg.timezone == "America/New_York"

    db = open_database()
    try:
        ids = repo_categories.id_map(db)
        rules = repo_rules.all_rules(db)
        wizard_rules = {(r.kind, r.pattern): r.category_id for r in rules
                        if r.source == "wizard"}
        assert ("domain", "myschool.edu") in wizard_rules
        assert wizard_rules[("exe", "powerpnt.exe")] == ids["school"], "adds .exe for you"
        assert wizard_rules[("domain", "myrealty.com")] == ids["real_estate"]
        assert any(r.source == "seed" for r in rules), "the shipped pack should be added"
    finally:
        db.close()


def test_wizard_accepts_all_defaults(tmp_path, monkeypatch):
    """Pressing enter through the whole thing must still produce a working setup."""
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path))
    answers(monkeypatch, [])
    assert run_wizard() == 0

    cfg = load_config()
    assert cfg.first_run_complete
    assert [c.display_name for c in cfg.categories] == ["School", "Real Estate"]

    db = open_database()
    try:
        assert repo_rules.count(db) > 100, "the seed pack is the default"
    finally:
        db.close()


def test_wizard_declining_the_seed_pack_leaves_only_your_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path))
    answers(monkeypatch, [
        "School", "Real Estate", "", "",
        "myschool.edu", "", "", "",
        "3", "UTC",
        "n",  # no seed pack
    ])
    run_wizard()
    db = open_database()
    try:
        rules = repo_rules.all_rules(db)
        assert all(r.source != "seed" for r in rules)
        assert any(r.pattern == "myschool.edu" for r in rules)
    finally:
        db.close()


def test_wizard_tolerates_messy_input(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path))
    answers(monkeypatch, [
        "School", "Real Estate", "", "",
        " https://www.MySchool.edu/portal ; 'other.edu' , ",  # messy list
        "", "", "",
        "not a number",  # bad idle value
        "UTC", "n",
    ])
    assert run_wizard() == 0
    cfg = load_config()
    assert cfg.idle.threshold_s == 180, "a bad number falls back to the default"

    db = open_database()
    try:
        patterns = {r.pattern for r in repo_rules.all_rules(db)}
        assert "myschool.edu" in patterns, "a pasted URL is reduced to the host"
        assert "other.edu" in patterns
    finally:
        db.close()


# ---- the LLM client ------------------------------------------------------


class FakeUsage:
    input_tokens = 1200
    output_tokens = 300


class FakeBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class FakeResponse:
    def __init__(self, text: str):
        self.content = [FakeBlock(text)]
        self.usage = FakeUsage()


def test_a_structured_reply_is_parsed(monkeypatch):
    payload = json.dumps({"results": [
        {"id": "abc123", "category": "real_estate", "confidence": 0.93,
         "suggested_rule": {"kind": "exe", "pattern": "quickbooks.exe"},
         "reason": "bookkeeping for the property business"},
    ]})
    result = client._parse(FakeResponse(payload), "claude-opus-5")
    assert result.ok
    assert result.results[0]["category"] == "real_estate"
    assert result.input_tokens == 1200
    assert result.cost_usd > 0


def test_an_unparseable_reply_is_an_error_not_a_crash():
    result = client._parse(FakeResponse("sorry, I can't do that"), "claude-opus-5")
    assert not result.ok and "parse" in result.error


def test_an_empty_reply_is_an_error():
    empty = FakeResponse("")
    empty.content = []
    result = client._parse(empty, "claude-opus-5")
    assert not result.ok and result.error


def test_cost_estimation_uses_the_model():
    opus = client.estimate_cost("claude-opus-5", 1_000_000, 100_000)
    haiku = client.estimate_cost("claude-haiku-4-5", 1_000_000, 100_000)
    assert opus > haiku > 0
    assert client.estimate_cost("something-unknown", 1000, 100) > 0


def test_a_missing_anthropic_package_is_reported_clearly(monkeypatch):
    """The assist must fail with an explanation, never take the app down."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __import__

    def blocked(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("no module named anthropic")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    result = client.classify_batch(
        api_key="k", model="claude-opus-5", items=[], categories=[], example_rules=[]
    )
    assert not result.ok and "not installed" in result.error


def test_the_prompt_names_both_jobs_and_allows_unclear():
    categories = [
        {"key": "school", "display_name": "School", "description": "teaching biology"},
        {"key": "real_estate", "display_name": "Real Estate", "description": "flipping houses"},
    ]
    system = prompt.build_system_prompt(categories, [
        {"kind": "domain", "pattern": "zillow.com", "category_key": "real_estate"},
    ])
    assert "teaching biology" in system
    assert "unclear" in system
    assert "zillow.com" in system

    schema = prompt.response_schema(["school", "real_estate"])
    enum = schema["properties"]["results"]["items"]["properties"]["category"]["enum"]
    assert enum == ["school", "real_estate", "unclear"]


def test_the_user_message_lists_every_item():
    message = prompt.build_user_message([
        {"id": "a1", "app": "chrome.exe", "domain": "dotloop.com", "title": "A loop"},
        {"id": "b2", "app": "word.exe", "domain": "", "title": "Lesson plan"},
    ])
    assert "a1" in message and "b2" in message
    assert "dotloop.com" in message


# ---- Excel ---------------------------------------------------------------


@pytest.mark.skipif(not __import__("importlib").util.find_spec("openpyxl"),
                    reason="openpyxl is not installed")
def test_xlsx_export_produces_a_readable_workbook(tmp_path, monkeypatch):
    from timesplit.config import Config
    from timesplit.core.models import SessionRecord
    from timesplit.engine import Engine
    from timesplit.export import xlsx_export
    from timesplit.store import repo_sessions
    from timesplit.store.db import Database

    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path))
    cfg = Config()
    cfg.timezone = "UTC"
    db = Database(tmp_path / "x.db")
    engine = Engine(db, cfg)
    day = "2026-08-24"
    repo_sessions.insert_session(db, SessionRecord(
        start_ts=1_787_000_000, end_ts=1_787_003_600, exe_name="chrome.exe",
        title="Canvas", local_day=day, closed=True,
        category_id=engine.category_ids["school"],
    ))

    path = xlsx_export.write_workbook(engine, day, day, out=tmp_path)
    assert path.exists()

    import openpyxl

    workbook = openpyxl.load_workbook(path)
    assert workbook.sheetnames == ["Summary", "Daily", "Detail"]
    assert workbook["Summary"]["A4"].value == "School"
    assert workbook["Summary"]["B4"].value == 1.0
    db.close()


def test_xlsx_availability_is_reported_honestly():
    from timesplit.export import xlsx_export

    assert isinstance(xlsx_export.available(), bool)
