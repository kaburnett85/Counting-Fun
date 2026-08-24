"""The optional Claude assist.

Two claims are made to the user on the settings page, and both are tested
here rather than asserted: that hard caps are enforced before any network
call, and that nothing identifying leaves the machine.
"""

from __future__ import annotations

import json
import time

import pytest

from timesplit.config import Config
from timesplit.core.models import SessionRecord
from timesplit.engine import Engine
from timesplit.llm import assist
from timesplit.llm.redact import contains_sensitive, redact_domain, redact_title
from timesplit.store import repo_sessions
from timesplit.store.db import Database


@pytest.fixture
def engine(tmp_path):
    cfg = Config()
    cfg.timezone = "UTC"
    cfg.llm_assist.enabled = True
    db = Database(tmp_path / "llm.db")
    engine = Engine(db, cfg)
    yield engine
    db.close()


def add_unknown(engine, exe: str, title: str, url=None, domain=None, seconds=1800):
    record = SessionRecord(
        start_ts=1_787_000_000, end_ts=1_787_000_000 + seconds, exe_name=exe,
        exe_path=f"C:\\{exe}", title=title, url=url, domain=domain,
        local_day="2026-08-24", closed=True,
        category_id=engine.category_ids["unknown"], needs_review=True, source="none",
    )
    return repo_sessions.insert_session(engine.db, record)


# ---- redaction -----------------------------------------------------------


@pytest.mark.parametrize("title", [
    "Purchase agreement - john.smith@gmail.com - dotloop",
    "C:\\Users\\Kirk\\Documents\\Closing 4429381.pdf - Adobe",
    "Case 2024-119384 - Escrow instructions",
    "\\\\fileserver\\shared\\clients\\Smith - Explorer",
    "Card 4111 1111 1111 1111 on file",
    "SSN 123-45-6789 verification",
    "Loan application - 555-867-5309",
])
def test_identifying_detail_never_survives_redaction(title):
    cleaned = redact_title(title)
    assert not contains_sensitive(cleaned), f"{title!r} -> {cleaned!r}"


def test_redaction_keeps_the_words_that_identify_the_job():
    cleaned = redact_title("Purchase agreement - john@x.com - dotloop - Google Chrome")
    assert "Purchase agreement" in cleaned and "dotloop" in cleaned


def test_ip_addresses_are_not_sent_as_domains():
    assert redact_domain("192.168.1.44") == "[local]"
    assert redact_domain("dotloop.com") == "dotloop.com"


def test_the_serialised_payload_contains_nothing_sensitive(engine):
    add_unknown(engine, "chrome.exe", "Closing for bob.jones@realty.com - loop 8827341",
                "https://dotloop.com/my/loops/8827341", "dotloop.com")
    add_unknown(engine, "acrobat.exe", "C:\\Users\\Kirk\\Desktop\\Smith closing.pdf")
    items = assist.gather_unknowns(engine)
    wire = [{k: v for k, v in i.items() if k not in ("signature", "session_id")} for i in items]
    blob = json.dumps(wire)
    assert not contains_sensitive(blob), blob
    assert "bob.jones@realty.com" not in blob
    assert "C:\\Users" not in blob


def test_full_urls_are_withheld_unless_opted_in(engine):
    add_unknown(engine, "chrome.exe", "A loop", "https://dotloop.com/my/loops/9", "dotloop.com")
    wire = json.dumps(assist.gather_unknowns(engine))
    assert "/my/loops/9" not in wire
    assert "dotloop.com" in wire  # the domain is what it needs

    engine.cfg.llm_assist.send_urls = True
    wire = json.dumps(assist.gather_unknowns(engine))
    assert "/my/loops/9" in wire


def test_excluded_windows_are_never_offered_to_the_api(engine):
    add_unknown(engine, "keepassxc.exe", "My vault")
    add_unknown(engine, "chrome.exe", "Zillow (Incognito)", "https://zillow.com/x", "zillow.com")
    add_unknown(engine, "quickbooks.exe", "Chart of accounts")
    apps = {i["app"] for i in assist.gather_unknowns(engine)}
    assert "keepassxc.exe" not in apps
    assert apps == {"quickbooks.exe"}


# ---- deduplication -------------------------------------------------------


def test_the_same_window_is_only_asked_about_once(engine):
    for _ in range(25):
        add_unknown(engine, "quickbooks.exe", "Burnett Group LLC - Chart of Accounts")
    items = assist.gather_unknowns(engine)
    assert len(items) == 1, "25 sessions on one window must cost one item"


def test_a_window_already_asked_about_is_not_asked_again(engine):
    session_id = add_unknown(engine, "quickbooks.exe", "Chart of Accounts")
    items = assist.gather_unknowns(engine)
    assert items
    request_id = engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
        (int(time.time()), 1, "test", "ok"),
    )
    assist.apply_results(engine, request_id, items, [
        {"id": items[0]["id"], "category": "unclear", "confidence": 0.2, "reason": "unsure"}
    ])
    assert assist.gather_unknowns(engine) == []
    assert session_id


# ---- caps ----------------------------------------------------------------


def test_disabled_by_default():
    cfg = Config()
    assert cfg.llm_assist.enabled is False


def test_caps_block_before_any_network_call(engine):
    ok, _ = assist.check_caps(engine.db, engine.cfg)
    assert ok

    engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, cost_usd, status)"
        " VALUES(?,?,?,?,?)",
        (int(time.time()), 10, "test", 0.0, "ok"),
    )
    ok, why = assist.check_caps(engine.db, engine.cfg)
    assert not ok and "too soon" in why


def test_daily_request_cap(engine):
    for _ in range(engine.cfg.llm_assist.daily_request_cap):
        engine.db.execute(
            "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
            (int(time.time()) - 7200, 10, "test", "ok"),
        )
    ok, why = assist.check_caps(engine.db, engine.cfg)
    assert not ok and "daily limit" in why


def test_monthly_spend_cap(engine):
    engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, cost_usd, status)"
        " VALUES(?,?,?,?,?)",
        (int(time.time()) - 7200, 10, "test", 99.0, "ok"),
    )
    ok, why = assist.check_caps(engine.db, engine.cfg)
    assert not ok and "spend limit" in why


def test_repeated_failures_pause_it(engine):
    for _ in range(engine.cfg.llm_assist.max_consecutive_failures):
        engine.db.execute(
            "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
            (int(time.time()) - 7200, 10, "test", "error"),
        )
    ok, why = assist.check_caps(engine.db, engine.cfg)
    assert not ok and "failures" in why


def test_run_once_refuses_without_a_key(engine):
    for i in range(15):
        add_unknown(engine, f"app{i}.exe", f"Window {i}")
    result = assist.run_once(engine, api_key="")
    assert result["ok"] is False and result["skipped"]


def test_run_once_waits_for_a_full_batch(engine):
    add_unknown(engine, "one.exe", "Only one")
    result = assist.run_once(engine, api_key="test-key")
    assert result["skipped"] and "waiting for" in result["reason"]


# ---- applying results ----------------------------------------------------


def test_a_confident_suggestion_becomes_a_rule(engine):
    add_unknown(engine, "quickbooks.exe", "Chart of accounts")
    items = assist.gather_unknowns(engine)
    request_id = engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
        (int(time.time()), 1, "test", "ok"),
    )
    result = assist.apply_results(engine, request_id, items, [{
        "id": items[0]["id"], "category": "real_estate", "confidence": 0.95,
        "suggested_rule": {"kind": "exe", "pattern": "quickbooks.exe"},
        "reason": "bookkeeping for the property business",
    }])
    assert result["rules"] == 1

    from timesplit.core.models import Activity

    decision = engine.classify(Activity("quickbooks.exe", "anything at all"))
    assert engine.category_key(decision.category_id) == "real_estate"


def test_a_suggested_rule_is_flagged_so_you_can_veto_it(engine):
    session_id = add_unknown(engine, "quickbooks.exe", "Chart of accounts")
    items = assist.gather_unknowns(engine)
    request_id = engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
        (int(time.time()), 1, "test", "ok"),
    )
    assist.apply_results(engine, request_id, items, [{
        "id": items[0]["id"], "category": "real_estate", "confidence": 0.95,
        "suggested_rule": {"kind": "exe", "pattern": "quickbooks.exe"},
        "reason": "bookkeeping",
    }])
    row = repo_sessions.session(engine.db, session_id)
    assert row["source"] == "llm_rule"
    assert row["needs_review"] == 1, "you must get the chance to disagree"


def test_a_low_confidence_answer_creates_no_rule(engine):
    add_unknown(engine, "mystery.exe", "Something")
    items = assist.gather_unknowns(engine)
    request_id = engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
        (int(time.time()), 1, "test", "ok"),
    )
    result = assist.apply_results(engine, request_id, items, [{
        "id": items[0]["id"], "category": "school", "confidence": 0.4,
        "suggested_rule": {"kind": "exe", "pattern": "mystery.exe"},
        "reason": "not sure",
    }])
    assert result["rules"] == 0


def test_a_suggestion_never_overrides_a_rule_you_set(engine):
    engine.add_rule("exe", "quickbooks.exe", "school")
    add_unknown(engine, "other.exe", "x")
    items = assist.gather_unknowns(engine)
    request_id = engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
        (int(time.time()), 1, "test", "ok"),
    )
    assist.apply_results(engine, request_id, items, [{
        "id": items[0]["id"], "category": "real_estate", "confidence": 0.99,
        "suggested_rule": {"kind": "exe", "pattern": "quickbooks.exe"},
        "reason": "override attempt",
    }])
    from timesplit.core.models import Activity

    decision = engine.classify(Activity("quickbooks.exe", "x"))
    assert engine.category_key(decision.category_id) == "school"


def test_absurdly_broad_suggestions_are_rejected(engine):
    add_unknown(engine, "chrome.exe", "Something", "https://x.com/a", "x.com")
    items = assist.gather_unknowns(engine)
    request_id = engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
        (int(time.time()), 1, "test", "ok"),
    )
    result = assist.apply_results(engine, request_id, items, [{
        "id": items[0]["id"], "category": "school", "confidence": 0.99,
        "suggested_rule": {"kind": "domain_suffix", "pattern": "com"},
        "reason": "everything is school",
    }])
    assert result["rules"] == 0, "a rule matching every .com site must be refused"


def test_the_learned_model_is_never_trained_on_suggestions(engine):
    """A model trained on its own upstream guesses cannot be corrected."""
    add_unknown(engine, "quickbooks.exe", "Chart of accounts")
    items = assist.gather_unknowns(engine)
    before = dict(engine.model.classes)
    request_id = engine.db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, status) VALUES(?,?,?,?)",
        (int(time.time()), 1, "test", "ok"),
    )
    assist.apply_results(engine, request_id, items, [{
        "id": items[0]["id"], "category": "real_estate", "confidence": 0.99,
        "suggested_rule": {"kind": "exe", "pattern": "quickbooks.exe"},
        "reason": "bookkeeping",
    }])
    assert engine.model.classes == before
    from timesplit.store import repo_corrections

    assert repo_corrections.count(engine.db) == 0


# ---- the preview ---------------------------------------------------------


def test_preview_shows_the_real_payload(engine):
    add_unknown(engine, "quickbooks.exe", "Chart of accounts for jane@x.com")
    preview = assist.preview_payload(engine)
    assert "quickbooks.exe" in preview
    assert "jane@x.com" not in preview
    assert "no screenshots" in preview


def test_preview_says_so_when_there_is_nothing_to_send(engine):
    assert "nothing waiting" in assist.preview_payload(engine)
