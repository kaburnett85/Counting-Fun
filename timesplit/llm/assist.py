"""The optional Claude assist: gating, batching, and what happens to a reply.

Off unless you turn it on and supply a key. Even then it is wrapped in hard
caps that are checked before any network call, because an always-running
background process with an API key is exactly the kind of thing that quietly
runs up a bill.

One deliberate constraint: the learned model is never trained on Claude's
labels. Only your corrections teach it. Training a model on its own upstream
suggestions creates a loop where one bad guess becomes a settled belief, and
you would have no way to see it happen.
"""

from __future__ import annotations

import time

from ..core.features import activity_from_row, signature
from ..core.models import RULE_PRIORITY_LLM, Activity
from ..core.urls import host_of
from ..logging_setup import get as get_logger
from ..store import repo_rules
from ..store.db import Database
from .redact import redact_domain, redact_title

log = get_logger("llm.assist")


# ---- accounting ----------------------------------------------------------


def usage_stats(db: Database) -> dict:
    month_start = int(time.time()) - 30 * 86400
    day_start = int(time.time()) - 86400
    return {
        "month_cost": float(
            db.scalar(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM llm_requests"
                " WHERE created_at >= ? AND status = 'ok'",
                (month_start,), default=0.0,
            )
        ),
        "month_requests": int(
            db.scalar(
                "SELECT COUNT(*) FROM llm_requests WHERE created_at >= ?",
                (month_start,), default=0,
            )
        ),
        "day_requests": int(
            db.scalar(
                "SELECT COUNT(*) FROM llm_requests WHERE created_at >= ?",
                (day_start,), default=0,
            )
        ),
        "last_request": db.scalar("SELECT MAX(created_at) FROM llm_requests", default=0) or 0,
        "recent_failures": int(
            db.scalar(
                "SELECT COUNT(*) FROM llm_requests WHERE status != 'ok' AND created_at >= ?",
                (day_start,), default=0,
            )
        ),
    }


def check_caps(db: Database, cfg) -> tuple[bool, str]:
    """Every limit is enforced here, before anything reaches the network."""
    llm = cfg.llm_assist
    if not llm.enabled:
        return False, "the Claude assist is switched off"
    stats = usage_stats(db)
    if stats["day_requests"] >= llm.daily_request_cap:
        return False, f"daily limit reached ({llm.daily_request_cap} requests)"
    if stats["month_cost"] >= llm.monthly_cost_cap_usd:
        return False, f"monthly spend limit reached (${llm.monthly_cost_cap_usd:.2f})"
    if stats["last_request"] and time.time() - stats["last_request"] < llm.min_interval_s:
        wait = int(llm.min_interval_s - (time.time() - stats["last_request"]))
        return False, f"too soon since the last request (wait {wait // 60} min)"
    if stats["recent_failures"] >= llm.max_consecutive_failures:
        return False, "paused until tomorrow after repeated failures"
    return True, ""


# ---- payload -------------------------------------------------------------


def gather_unknowns(engine, limit: int = 40) -> list[dict]:
    """Unresolved windows, deduplicated so one page costs one item."""
    rows = engine.db.query(
        "SELECT s.*, a.exe_name, a.exe_path FROM sessions s"
        " LEFT JOIN apps a ON a.id = s.app_id"
        " LEFT JOIN categories c ON c.id = s.category_id"
        " WHERE s.is_locked = 0 AND s.needs_review = 1"
        " AND (s.category_id IS NULL OR c.key = 'unknown')"
        " ORDER BY s.duration_s DESC LIMIT 600"
    )
    seen: set[str] = set()
    already_asked = {
        r["signature"] for r in engine.db.query("SELECT DISTINCT signature FROM llm_items")
    }
    items: list[dict] = []
    for row in rows:
        activity = activity_from_row(dict(row))
        if engine.is_excluded(activity):
            continue
        sig = signature(activity)
        if sig in seen or sig in already_asked:
            continue
        seen.add(sig)
        items.append(_build_item(engine, sig, activity, int(row["id"])))
        if len(items) >= limit:
            break
    return items


def _build_item(engine, sig: str, activity: Activity, session_id: int) -> dict:
    privacy = engine.cfg.privacy
    item = {
        "id": sig[:12],
        "signature": sig,
        "session_id": session_id,
        "app": (activity.exe_name or "unknown").lower(),
        "domain": redact_domain(activity.domain or host_of(activity.url)),
        "title": redact_title(activity.title or "", privacy.exclude_title_patterns),
    }
    if engine.cfg.llm_assist.send_urls and activity.url:
        item["url"] = activity.url
    return item


def preview_payload(engine, limit: int = 10) -> str:
    """Exactly what would be sent, rendered for the settings page.

    Shown before anything is ever transmitted, so the claim about what leaves
    the machine can be checked rather than trusted.
    """
    import json

    items = gather_unknowns(engine, limit=limit)
    if not items:
        return "There is nothing waiting to be sent — every session has a job assigned."
    wire = [
        {k: v for k, v in item.items() if k not in ("signature", "session_id")}
        for item in items
    ]
    header = (
        f"{len(wire)} window(s) would be sent to the Claude API.\n"
        "Nothing else leaves this computer: no screenshots, no file contents,\n"
        "no keystrokes, and no full web addresses"
        f"{' (you have opted in to sending addresses)' if engine.cfg.llm_assist.send_urls else ''}.\n\n"
    )
    return header + json.dumps(wire, indent=2)


# ---- running a batch -----------------------------------------------------


def run_once(engine, api_key: str | None = None) -> dict:
    """Ask about a batch of unknowns, if every cap allows it."""
    db, cfg = engine.db, engine.cfg
    allowed, why = check_caps(db, cfg)
    if not allowed:
        return {"ok": False, "skipped": True, "reason": why}

    if api_key is None:
        from ..backends.factory import get_secrets

        api_key = get_secrets().get_secret()
    if not api_key:
        return {"ok": False, "skipped": True, "reason": "no API key is set"}

    items = gather_unknowns(engine, limit=cfg.llm_assist.max_batch)
    if len(items) < cfg.llm_assist.min_batch:
        return {
            "ok": False,
            "skipped": True,
            "reason": f"only {len(items)} unknown windows; waiting for "
            f"{cfg.llm_assist.min_batch}",
        }

    from .client import classify_batch

    categories = [
        {"key": c["key"], "display_name": c["display_name"], "description": c["description"]}
        for c in engine.job_categories()
    ]
    example_rules = [
        {"kind": r["kind"], "pattern": r["pattern"], "category_key": r["category_key"]}
        for r in repo_rules.rules_with_stats(db)
        if r["source"] in ("user", "wizard")
    ][:15]

    wire = [{k: v for k, v in i.items() if k not in ("signature", "session_id")} for i in items]
    result = classify_batch(
        api_key=api_key,
        model=cfg.llm_assist.model,
        items=wire,
        categories=categories,
        example_rules=example_rules,
    )

    request_id = db.execute(
        "INSERT INTO llm_requests(created_at, n_items, model, input_tokens, output_tokens,"
        " cost_usd, status, error) VALUES(?,?,?,?,?,?,?,?)",
        (int(time.time()), len(items), cfg.llm_assist.model, result.input_tokens,
         result.output_tokens, result.cost_usd, "ok" if result.ok else "error",
         result.error[:400]),
    )
    if not result.ok:
        log.warning("Claude assist failed: %s", result.error)
        return {"ok": False, "error": result.error}

    return apply_results(engine, request_id, items, result.results)


def apply_results(engine, request_id: int, items: list[dict], results: list[dict]) -> dict:
    """Turn a reply into rules and labels.

    High-confidence answers become rules so the question is not asked again,
    but the first session each rule touches is still flagged for review -- you
    get to veto it.
    """
    by_id = {item["id"]: item for item in items}
    cfg = engine.cfg
    applied = rules_made = 0

    for entry in results:
        item = by_id.get(str(entry.get("id")))
        if item is None:
            continue
        category_key = str(entry.get("category") or "unclear")
        confidence = float(entry.get("confidence") or 0.0)
        category_id = engine.category_ids.get(category_key)

        engine.db.execute(
            "INSERT INTO llm_items(request_id, signature, exe_name, domain, redacted_title,"
            " category_key, confidence, accepted) VALUES(?,?,?,?,?,?,?,?)",
            (request_id, item["signature"], item["app"], item["domain"], item["title"],
             category_key, confidence, 0),
        )
        if category_key == "unclear" or category_id is None:
            continue

        suggestion = entry.get("suggested_rule")
        if (
            confidence >= cfg.llm_assist.rule_threshold
            and isinstance(suggestion, dict)
            and suggestion.get("pattern")
        ):
            kind = str(suggestion.get("kind") or "")
            pattern = str(suggestion.get("pattern") or "")
            if _rule_is_safe(engine, kind, pattern):
                try:
                    repo_rules.add_rule(
                        engine.db, kind, pattern, category_id,
                        source="llm", note="suggested by Claude — check this",
                        replace=False,
                    )
                    rules_made += 1
                except ValueError:
                    pass
        applied += 1

    if rules_made:
        engine.reload()
        engine._sweep()
    _flag_llm_rules_for_review(engine)
    return {"ok": True, "items": len(items), "applied": applied, "rules": rules_made}


def _rule_is_safe(engine, kind: str, pattern: str) -> bool:
    """Never let a suggestion overwrite something you decided."""
    if kind not in ("domain", "domain_suffix", "exe", "title_contains"):
        return False
    pattern = pattern.strip().lower()
    if not pattern or len(pattern) < 3:
        return False
    # A suffix rule this broad would sweep up half the internet.
    if kind == "domain_suffix" and pattern in ("com", "net", "org", "io", "co"):
        return False
    existing = engine.db.query_one(
        "SELECT priority FROM rules WHERE kind = ? AND pattern = ?", (kind, pattern)
    )
    return not (existing and int(existing["priority"]) > RULE_PRIORITY_LLM)


def _flag_llm_rules_for_review(engine) -> None:
    """Anything a suggestion decided stays in the review queue until you agree."""
    engine.db.execute(
        "UPDATE sessions SET needs_review = 1 WHERE source = 'llm_rule' AND is_locked = 0"
    )
