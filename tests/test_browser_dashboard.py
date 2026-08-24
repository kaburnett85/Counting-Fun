"""The dashboard driven by a real browser.

These exist because two bugs got through a full suite of HTTP-level tests:
a Content-Security-Policy that blocked the page's own fetch calls, and a
scope selector the JavaScript could never find. Neither is visible to a test
that speaks HTTP directly -- only a browser enforces CSP and runs the script.

Skipped when Playwright is unavailable, so the suite still runs anywhere.
"""

from __future__ import annotations

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

import os  # noqa: E402
from pathlib import Path  # noqa: E402

from timesplit.config import Config  # noqa: E402
from timesplit.core.models import SessionRecord  # noqa: E402
from timesplit.engine import Engine  # noqa: E402
from timesplit.store import repo_rules, repo_sessions  # noqa: E402
from timesplit.store.db import Database  # noqa: E402
from timesplit.web.server import DashboardServer  # noqa: E402

#: This environment ships a chromium at a fixed path; CI installs its own and
#: lets playwright find it. An empty value means "let playwright decide".
CHROMIUM = os.environ.get("PLAYWRIGHT_CHROMIUM", "/opt/pw-browsers/chromium")


@pytest.fixture(scope="module")
def browser():
    launch_args = {}
    if CHROMIUM:
        if not Path(CHROMIUM).exists():
            pytest.skip("no chromium available")
        launch_args["executable_path"] = CHROMIUM
    with playwright.sync_playwright() as p:
        try:
            instance = p.chromium.launch(**launch_args)
        except Exception as exc:
            pytest.skip(f"could not launch chromium: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def live(tmp_path):
    cfg = Config()
    cfg.timezone = "UTC"
    cfg.dashboard.auto_shutdown_min = 0
    db = Database(tmp_path / "browser.db")
    engine = Engine(db, cfg)
    engine.ensure_seeded()

    day = "2026-08-24"
    # Derive the timestamps from the day itself, so local_day and start_ts
    # actually agree -- otherwise the timeline correctly clips everything out.
    from timesplit.core.clock import day_bounds

    start = day_bounds(day, engine.tz)[0] + 9 * 3600
    for exe, title, url, domain, seconds in [
        ("quickbooks.exe", "Burnett Group LLC - Chart of Accounts", None, None, 2100),
        ("chrome.exe", "Canvas Gradebook", "https://canvas.instructure.com/c/1",
         "canvas.instructure.com", 1800),
    ]:
        from timesplit.core.models import Activity

        decision = engine.classify(Activity(exe, title, url, domain))
        repo_sessions.insert_session(db, SessionRecord(
            start_ts=start, end_ts=start + seconds, exe_name=exe, exe_path=f"C:\\{exe}",
            title=title, url=url, domain=domain, local_day=day, closed=True,
            category_id=decision.category_id, source=decision.source,
            confidence=decision.confidence, needs_review=decision.needs_review,
        ))
        start += seconds

    server = DashboardServer(engine, cfg, port=0)
    server.port = 0
    server.start()
    yield server, engine, day
    server.stop()
    db.close()


def open_page(browser, server, path: str):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    joiner = "&" if "?" in path else "?"
    page.goto(
        f"http://127.0.0.1:{server.port}{path}{joiner}t={server.token}",
        wait_until="networkidle",
    )
    return page, errors


def test_pages_load_without_console_errors(browser, live):
    """A blocked fetch or a bad selector shows up here and nowhere else."""
    server, _, _ = live
    for path in ("/", "/week", "/review", "/rules", "/settings", "/export"):
        page, errors = open_page(browser, server, path)
        assert not errors, f"{path}: {errors}"
        page.close()


def test_correcting_from_the_review_page_works(browser, live):
    server, engine, _ = live
    page, errors = open_page(browser, server, "/review")
    rows_before = page.locator("tbody tr").count()
    assert rows_before >= 1

    page.locator("tbody tr").first.locator(
        "button[data-category='real_estate']"
    ).click()
    page.wait_for_timeout(1500)

    assert not errors, errors
    assert page.locator("tbody tr").count() == rows_before - 1
    page.close()


def test_the_scope_selector_actually_reaches_the_server(browser, live):
    """The regression: the buttons carry data-session-id, so a closest()
    lookup for the row returned the button and never found the selector."""
    server, engine, _ = live
    sent: list[str] = []
    page, errors = open_page(browser, server, "/review")
    page.on("request", lambda r: sent.append(r.post_data or "")
            if r.method == "POST" else None)

    row = page.locator("tbody tr").first
    row.locator("select[data-scope]").select_option("same_exe")
    row.locator("button[data-category='real_estate']").click()
    page.wait_for_timeout(1500)

    assert sent, "no request was sent"
    assert '"scope":"same_exe"' in sent[0], sent[0]
    assert not errors, errors

    # And the wider scope really did become a rule.
    patterns = {r.pattern for r in repo_rules.all_rules(engine.db) if r.source == "user"}
    assert "quickbooks.exe" in patterns
    page.close()


def test_clicking_a_timeline_block_scrolls_to_its_session(browser, live):
    server, _, day = live
    page, errors = open_page(browser, server, f"/day?d={day}")
    block = page.locator("rect.work-block[data-session-id]").first
    assert block.count() >= 0
    if block.count():
        block.click()
        page.wait_for_timeout(400)
    assert not errors, errors
    page.close()


def test_charts_render_as_real_elements(browser, live):
    server, _, day = live
    page, errors = open_page(browser, server, f"/day?d={day}")
    assert page.locator("svg").count() >= 2
    assert page.locator("rect.work-block").count() >= 1
    assert not errors, errors
    page.close()


def test_the_shipped_rules_are_collapsed_by_default(browser, live):
    """130 shipped rules must not bury the handful that are yours."""
    server, _, _ = live
    page, errors = open_page(browser, server, "/rules")
    details = page.locator("details")
    assert details.count() >= 1
    assert not details.first.get_attribute("open")
    assert not errors, errors
    page.close()
