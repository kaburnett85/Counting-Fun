"""The store and config: migrations, recovery, and surviving a bad file."""

from __future__ import annotations

import json

from timesplit import paths
from timesplit.config import Config, from_dict, load_config, save_config
from timesplit.core.models import SessionRecord
from timesplit.store import repo_categories, repo_rules, repo_sessions
from timesplit.store.db import Database, open_database

# ---- config --------------------------------------------------------------


def test_defaults_are_sensible():
    cfg = Config()
    assert cfg.category_keys() == ["school", "real_estate"]
    assert cfg.browser.browser_exes == ["chrome.exe", "comet.exe"]
    assert cfg.llm_assist.enabled is False
    assert cfg.idle.threshold_s == 180


def test_a_missing_config_file_is_not_an_error(tmp_path):
    cfg = load_config(tmp_path / "nope.json")
    assert cfg.category_keys() == ["school", "real_estate"]


def test_a_corrupt_config_is_set_aside_and_defaults_are_used(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.category_keys() == ["school", "real_estate"]
    assert (tmp_path / "config.json.bad").exists(), "the bad file should be kept"


def test_a_partial_config_keeps_defaults_for_everything_else(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"idle": {"threshold_s": 600}}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.idle.threshold_s == 600
    assert cfg.sampling.interval_s == 3


def test_absurd_values_are_clamped_rather_than_rejected():
    cfg = from_dict({
        "sampling": {"interval_s": 99999},
        "classifier": {"tau_assign": 5.0, "tau_review": -1},
        "dashboard": {"port": 99},
    })
    assert cfg.sampling.interval_s == 60
    assert cfg.classifier.tau_assign <= 0.999
    assert cfg.classifier.tau_review >= 0
    assert cfg.dashboard.port == 8756


def test_unknown_keys_do_not_break_loading():
    cfg = from_dict({"something_new": {"x": 1}, "idle": {"threshold_s": 300, "future": True}})
    assert cfg.idle.threshold_s == 300


def test_config_round_trips(tmp_path):
    cfg = Config()
    cfg.categories[0].display_name = "Teaching"
    cfg.timezone = "America/New_York"
    save_config(cfg, tmp_path / "c.json")
    loaded = load_config(tmp_path / "c.json")
    assert loaded.categories[0].display_name == "Teaching"
    assert loaded.timezone == "America/New_York"


def test_saving_is_atomic(tmp_path):
    """A crash mid-write must not leave a truncated config."""
    path = tmp_path / "c.json"
    save_config(Config(), path)
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".config-")]
    assert not leftovers


def test_timesplit_home_redirects_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path / "elsewhere"))
    assert paths.data_dir() == tmp_path / "elsewhere"
    assert paths.db_path().parent == tmp_path / "elsewhere"


# ---- database ------------------------------------------------------------


def test_migrations_are_idempotent(tmp_path):
    db = Database(tmp_path / "m.db")
    assert db.get_meta("schema_version") == "1"
    assert db.migrate() == 1
    assert db.migrate() == 1
    db.close()


def test_reopening_an_existing_database_preserves_data(tmp_path):
    path = tmp_path / "keep.db"
    db = Database(path)
    repo_categories.sync_from_config(db, Config())
    db.close()

    db = Database(path)
    assert "school" in repo_categories.id_map(db)
    db.close()


def test_a_corrupt_database_is_quarantined_not_lost(tmp_path):
    path = tmp_path / "bad.db"
    path.write_bytes(b"this is definitely not a sqlite file" * 40)
    db = open_database(path)
    assert db.get_meta("schema_version") == "1"
    quarantined = list(tmp_path.glob("bad.corrupt*.db"))
    assert quarantined, "the damaged file should be kept for recovery"
    db.close()


def test_sessions_left_open_by_a_crash_are_closed_at_their_last_heartbeat(tmp_path):
    """Never extend an unclosed session to now -- that could be days later."""
    db = Database(tmp_path / "crash.db")
    repo_categories.sync_from_config(db, Config())
    record = SessionRecord(
        start_ts=1000, end_ts=1600, exe_name="word.exe", title="Doc",
        local_day="2026-08-24", closed=False,
    )
    session_id = repo_sessions.insert_session(db, record)

    repo_sessions.finalize_open_sessions(db, end_ts=999_999_999)
    row = repo_sessions.session(db, session_id)
    assert row["closed"] == 1
    assert row["end_ts"] == 1600, "the heartbeat value must be trusted, not 'now'"
    assert row["duration_s"] == 600
    db.close()


def test_rules_are_deduplicated_and_upgraded_by_priority(tmp_path):
    db = Database(tmp_path / "r.db")
    ids = repo_categories.sync_from_config(db, Config())
    repo_rules.add_rule(db, "domain", "zillow.com", ids["real_estate"], source="seed")
    repo_rules.add_rule(db, "domain", "ZILLOW.com", ids["school"], source="user")
    rules = repo_rules.all_rules(db)
    assert len(rules) == 1
    assert rules[0].category_id == ids["school"]
    assert rules[0].priority == 1000
    db.close()


def test_a_seed_rule_cannot_downgrade_one_of_yours(tmp_path):
    db = Database(tmp_path / "r2.db")
    ids = repo_categories.sync_from_config(db, Config())
    repo_rules.add_rule(db, "exe", "outlook.exe", ids["school"], source="user")
    repo_rules.add_rule(db, "exe", "outlook.exe", ids["real_estate"], source="seed",
                        replace=False)
    assert repo_rules.all_rules(db)[0].category_id == ids["school"]
    db.close()


def test_deleting_a_rule_detaches_it_from_sessions(tmp_path):
    db = Database(tmp_path / "r3.db")
    ids = repo_categories.sync_from_config(db, Config())
    rule_id = repo_rules.add_rule(db, "exe", "word.exe", ids["school"])
    record = SessionRecord(start_ts=0, end_ts=60, exe_name="word.exe", title="x",
                           local_day="2026-08-24", category_id=ids["school"],
                           rule_id=rule_id, closed=True)
    session_id = repo_sessions.insert_session(db, record)
    repo_rules.delete_rule(db, rule_id)
    assert repo_sessions.session(db, session_id)["rule_id"] is None
    db.close()


def test_categories_sync_is_idempotent(tmp_path):
    db = Database(tmp_path / "c.db")
    cfg = Config()
    first = repo_categories.sync_from_config(db, cfg)
    second = repo_categories.sync_from_config(db, cfg)
    assert first == second
    assert len(repo_categories.job_categories(db)) == 2
    db.close()


def test_renaming_a_job_keeps_its_history(tmp_path):
    db = Database(tmp_path / "rename.db")
    cfg = Config()
    ids = repo_categories.sync_from_config(db, cfg)
    record = SessionRecord(start_ts=0, end_ts=600, exe_name="word.exe", title="x",
                           local_day="2026-08-24", category_id=ids["school"], closed=True)
    repo_sessions.insert_session(db, record)

    cfg.categories[0].display_name = "Teaching"
    new_ids = repo_categories.sync_from_config(db, cfg)
    assert new_ids["school"] == ids["school"]
    rows = repo_sessions.sessions_for_day(db, "2026-08-24")
    assert rows[0]["category_name"] == "Teaching"
    db.close()


def test_an_empty_database_gets_its_starting_rules_back(tmp_path):
    """A quarantined-and-recreated database must not silently lose every rule.

    Without this the tracker would come back up and file everything as
    uncategorised, with nothing to indicate why.
    """
    from timesplit.engine import Engine

    db = Database(tmp_path / "empty.db")
    engine = Engine(db, Config())
    assert repo_rules.count(db) == 0

    added = engine.ensure_seeded()
    assert added > 100
    assert engine.ensure_seeded() == 0, "it must not re-seed on every start"

    from timesplit.core.models import Activity

    decision = engine.classify(Activity("chrome.exe", "x", "https://zillow.com/a", "zillow.com"))
    assert engine.category_key(decision.category_id) == "real_estate"
    db.close()


def test_seeding_does_not_overwrite_rules_you_already_have(tmp_path):
    from timesplit.engine import Engine

    db = Database(tmp_path / "mine.db")
    engine = Engine(db, Config())
    engine.add_rule("domain", "zillow.com", "school")

    assert engine.ensure_seeded() == 0, "a database with rules is left alone"

    from timesplit.core.models import Activity

    decision = engine.classify(Activity("chrome.exe", "x", "https://zillow.com/a", "zillow.com"))
    assert engine.category_key(decision.category_id) == "school"
    db.close()
