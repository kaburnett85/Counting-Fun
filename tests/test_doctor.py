"""The diagnostic command.

It has to work on a broken install -- that is when it gets used -- so the
tests point it at empty, missing and damaged data.
"""

from __future__ import annotations

from timesplit.doctor import run_doctor, selftest_win


def test_doctor_runs_on_a_fresh_machine(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path / "brand-new"))
    code = run_doctor()
    output = capsys.readouterr().out
    assert "TimeSplit" in output
    assert "setup complete" in output
    # Nothing set up yet, so it should say so rather than claim all is well.
    assert code == 1
    assert "wizard" in output


def test_doctor_reports_a_configured_install(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path))
    from timesplit.config import Config, save_config
    from timesplit.core.models import SessionRecord
    from timesplit.engine import Engine
    from timesplit.store import repo_sessions
    from timesplit.store.db import open_database

    cfg = Config()
    cfg.first_run_complete = True
    cfg.timezone = "UTC"
    save_config(cfg)

    db = open_database()
    engine = Engine(db, cfg)
    repo_sessions.insert_session(db, SessionRecord(
        start_ts=1_787_000_000, end_ts=1_787_003_600, exe_name="chrome.exe",
        title="Canvas", local_day="2026-08-24", closed=True,
        category_id=engine.category_ids["school"],
    ))
    db.close()

    run_doctor()
    output = capsys.readouterr().out
    assert "schema version" in output
    assert "days recorded" in output
    # Diagnostics are often pasted into an email. Window titles must not be.
    assert "Canvas" not in output, "doctor must not print window titles"


def test_doctor_survives_a_damaged_database(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TIMESPLIT_HOME", str(tmp_path))
    (tmp_path).mkdir(exist_ok=True)
    (tmp_path / "timesplit.db").write_bytes(b"not a database" * 100)
    run_doctor()
    output = capsys.readouterr().out
    assert "schema version" in output, "it should recover and carry on"


def test_windows_selftest_is_a_no_op_off_windows(capsys):
    assert selftest_win() == 0
    assert "not running on Windows" in capsys.readouterr().out
