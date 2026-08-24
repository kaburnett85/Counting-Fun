"""The dashboard: security model, pages, and the correction API.

A real server on a real socket, driven with http.client -- the security
behaviour is the point, and mocking it would test nothing.
"""

from __future__ import annotations

import http.client
import json
import xml.etree.ElementTree as ET

import pytest

from timesplit.config import Config
from timesplit.core.models import SessionRecord
from timesplit.engine import Engine
from timesplit.store import repo_sessions
from timesplit.store.db import Database
from timesplit.web.server import DashboardServer
from timesplit.wizard.seeds import apply_seed_rules


@pytest.fixture
def dashboard(tmp_path):
    cfg = Config()
    cfg.timezone = "UTC"
    cfg.dashboard.auto_shutdown_min = 0  # no watchdog during tests
    db = Database(tmp_path / "test.db")
    engine = Engine(db, cfg)
    apply_seed_rules(db, engine.category_ids)
    engine.reload()

    day = "2026-08-24"
    from timesplit.core.clock import day_bounds

    base = day_bounds(day, engine.tz)[0] + 9 * 3600
    rows = [
        ("chrome.exe", "Canvas Gradebook - Google Chrome",
         "https://canvas.instructure.com/courses/1", "canvas.instructure.com", 3600),
        ("chrome.exe", "12 Oak St - Zillow", "https://zillow.com/homes/12",
         "zillow.com", 1800),
        ("quickbooks.exe", "Chart of Accounts", None, None, 2400),
    ]
    start = base
    for exe, title, url, domain, seconds in rows:
        record = SessionRecord(
            start_ts=start, end_ts=start + seconds, exe_name=exe, exe_path=f"C:\\{exe}",
            title=title, url=url, domain=domain, local_day=day, closed=True,
        )
        from timesplit.core.models import Activity

        decision = engine.classify(Activity(exe, title, url, domain))
        record.category_id = decision.category_id
        record.source = decision.source
        record.confidence = decision.confidence
        record.needs_review = decision.needs_review
        repo_sessions.insert_session(db, record)
        start += seconds

    server = DashboardServer(engine, cfg, port=0)
    # Port 0 lets the OS choose, so parallel test runs never collide.
    server.port = 0
    url = server.start()
    assert url
    yield server, engine, day
    server.stop()
    db.close()


def request(server, method: str, path: str, *, cookie: str | None = None,
            body: dict | None = None, headers: dict | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    hdrs = dict(headers or {})
    if cookie is not None:
        hdrs["Cookie"] = f"ts_auth={cookie}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        hdrs.setdefault("Content-Type", "application/json")
        hdrs.setdefault("Origin", f"http://127.0.0.1:{server.port}")
    conn.request(method, path, body=payload, headers=hdrs)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    return response, data


# ---- security ------------------------------------------------------------


def test_no_cookie_is_403(dashboard):
    server, _, _ = dashboard
    response, _ = request(server, "GET", "/")
    assert response.status == 403


def test_wrong_cookie_is_403(dashboard):
    server, _, _ = dashboard
    response, _ = request(server, "GET", "/", cookie="not-the-token")
    assert response.status == 403


def test_token_in_the_url_sets_a_strict_cookie_and_redirects(dashboard):
    server, _, _ = dashboard
    response, _ = request(server, "GET", f"/?t={server.token}")
    assert response.status == 302
    cookie = response.getheader("Set-Cookie") or ""
    assert "SameSite=Strict" in cookie
    assert "HttpOnly" in cookie
    assert response.getheader("Location") == "/"


def test_correction_requires_the_token_header(dashboard):
    """The cookie alone must not be enough, or a cross-site POST would work."""
    server, _, _ = dashboard
    response, _ = request(
        server, "POST", "/api/correct", cookie=server.token,
        body={"session_id": 1, "category": "school"},
    )
    assert response.status == 403


def test_correction_from_a_foreign_origin_is_refused(dashboard):
    server, _, _ = dashboard
    response, _ = request(
        server, "POST", "/api/correct", cookie=server.token,
        body={"session_id": 1, "category": "school"},
        headers={"Origin": "https://evil.example", "X-TS-Token": server.token},
    )
    assert response.status == 403


def test_post_with_no_origin_at_all_is_refused(dashboard):
    server, _, _ = dashboard
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    payload = json.dumps({"session_id": 1, "category": "school"}).encode()
    conn.request("POST", "/api/correct", body=payload, headers={
        "Cookie": f"ts_auth={server.token}",
        "Content-Type": "application/json",
        "X-TS-Token": server.token,
    })
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.status == 403


def test_responses_carry_hardening_headers(dashboard):
    server, _, _ = dashboard
    response, _ = request(server, "GET", "/", cookie=server.token)
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert response.getheader("Referrer-Policy") == "no-referrer"
    assert "default-src 'none'" in (response.getheader("Content-Security-Policy") or "")


# ---- pages ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/week", "/review", "/rules", "/export", "/settings"])
def test_every_page_renders(dashboard, path):
    server, _, _ = dashboard
    response, body = request(server, "GET", path, cookie=server.token)
    assert response.status == 200, path
    text = body.decode()
    assert "<html" in text and "TimeSplit" in text


def test_unknown_path_is_404(dashboard):
    server, _, _ = dashboard
    response, _ = request(server, "GET", "/nope", cookie=server.token)
    assert response.status == 404


def test_today_page_shows_both_jobs(dashboard):
    server, _, day = dashboard
    _, body = request(server, "GET", f"/day?d={day}", cookie=server.token)
    text = body.decode()
    assert "School" in text and "Real Estate" in text


def test_charts_are_valid_svg(dashboard):
    server, _, day = dashboard
    _, body = request(server, "GET", f"/day?d={day}", cookie=server.token)
    text = body.decode()
    svgs = [
        text[m : text.index("</svg>", m) + 6]
        for m in [i for i in range(len(text)) if text.startswith("<svg", i)]
    ]
    assert svgs, "the day page should contain charts"
    for svg in svgs:
        ET.fromstring(svg)  # raises if malformed


def test_review_page_lists_the_uncategorised_session(dashboard):
    server, _, _ = dashboard
    _, body = request(server, "GET", "/review", cookie=server.token)
    assert "Chart of Accounts" in body.decode()


# ---- the correction API --------------------------------------------------


def _correct(server, session_id: int, category: str, scope: str = "session"):
    return request(
        server, "POST", "/api/correct", cookie=server.token,
        body={"session_id": session_id, "category": category, "scope": scope},
        headers={"X-TS-Token": server.token,
                 "Origin": f"http://127.0.0.1:{server.port}"},
    )


def test_a_correction_changes_the_totals(dashboard):
    server, engine, day = dashboard
    from timesplit.core.aggregate import totals_for_day

    row = repo_sessions.review_queue(engine.db, 1)[0]
    before = totals_for_day(engine.db, day).seconds_for("real_estate")

    response, body = _correct(server, int(row["id"]), "real_estate")
    assert response.status == 200
    assert json.loads(body)["ok"] is True

    after = totals_for_day(engine.db, day).seconds_for("real_estate")
    assert after == before + float(row["duration_s"])


def test_a_wider_correction_creates_a_rule_and_sticks(dashboard):
    server, engine, _ = dashboard
    row = repo_sessions.review_queue(engine.db, 1)[0]
    _, body = _correct(server, int(row["id"]), "real_estate", scope="same_exe")
    payload = json.loads(body)
    assert payload["ok"] and payload["rule"]["kind"] == "exe"

    from timesplit.core.models import Activity

    decision = engine.classify(Activity("quickbooks.exe", "Something else entirely"))
    assert engine.category_key(decision.category_id) == "real_estate"


def test_correcting_a_missing_session_fails_cleanly(dashboard):
    server, _, _ = dashboard
    _, body = _correct(server, 999_999, "school")
    payload = json.loads(body)
    assert payload["ok"] is False and payload["error"]


def test_a_manual_choice_is_never_overwritten(dashboard):
    """You decided; a later rule sweep must not undo it."""
    server, engine, _ = dashboard
    rows = repo_sessions.sessions_for_day(engine.db, "2026-08-24")
    zillow = next(r for r in rows if r["domain"] == "zillow.com")
    _correct(server, int(zillow["id"]), "school")

    engine.add_rule("domain", "zillow.com", "real_estate")
    after = repo_sessions.session(engine.db, int(zillow["id"]))
    assert after["category_id"] == engine.category_ids["school"]
    assert after["is_locked"] == 1


def test_status_endpoint_reports_the_day(dashboard):
    server, _, _ = dashboard
    _, body = request(server, "GET", "/api/status", cookie=server.token)
    payload = json.loads(body)
    assert "summary" in payload and "categories" in payload


def test_adding_a_rule_via_the_form_redirects_and_applies(dashboard):
    server, engine, _ = dashboard
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    from urllib.parse import urlencode

    payload = urlencode({
        "token": server.token, "kind": "exe",
        "pattern": "quickbooks.exe", "category": "real_estate",
    })
    conn.request("POST", "/rules/add", body=payload, headers={
        "Cookie": f"ts_auth={server.token}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": f"http://127.0.0.1:{server.port}",
    })
    response = conn.getresponse()
    response.read()
    conn.close()
    assert response.status == 303
    assert "/rules" in (response.getheader("Location") or "")

    from timesplit.core.models import Activity

    decision = engine.classify(Activity("quickbooks.exe", "anything"))
    assert engine.category_key(decision.category_id) == "real_estate"
