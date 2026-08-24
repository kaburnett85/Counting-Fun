"""Dispatch table for the dashboard."""

from __future__ import annotations

import time
from urllib.parse import quote

from ..core import aggregate
from ..core.clock import add_days, day_bounds, local_day, week_start
from ..store import repo_categories, repo_rules, repo_sessions
from . import render

HTML = "text/html; charset=utf-8"
JSON = "application/json; charset=utf-8"


class Router:
    def __init__(self, engine, cfg, server):
        self.engine = engine
        self.cfg = cfg
        self.server = server

    # ---- helpers -------------------------------------------------------

    @property
    def tz(self):
        return self.engine.tz

    def today(self) -> str:
        return local_day(time.time(), self.tz)

    def categories(self) -> list[dict]:
        return repo_categories.job_categories(self.engine.db)

    def review(self) -> tuple[int, float]:
        return repo_sessions.review_totals(self.engine.db)

    def _html(self, body: str, status: int = 200):
        return status, body.encode("utf-8"), HTML

    def _json(self, payload: dict, status: int = 200):
        import json

        return status, json.dumps(payload).encode("utf-8"), JSON

    def _redirect(self, path: str):
        return 303, path.encode("utf-8"), "redirect"

    # ---- GET -----------------------------------------------------------

    def get(self, path: str, query: dict[str, list[str]]):
        if path in ("/", "/day"):
            day = (query.get("d") or [self.today()])[0]
            return self._day(day)
        if path == "/week":
            return self._week((query.get("d") or [self.today()])[0])
        if path == "/review":
            return self._review()
        if path == "/rules":
            return self._rules((query.get("msg") or [""])[0])
        if path == "/export":
            return self._export((query.get("msg") or [""])[0])
        if path == "/settings":
            return self._settings((query.get("msg") or [""])[0])
        if path == "/api/status":
            return self._json(self.status_payload())
        return None

    def _day(self, day: str):
        try:
            bounds = day_bounds(day, self.tz)
        except ValueError:
            day = self.today()
            bounds = day_bounds(day, self.tz)
        db = self.engine.db
        return self._html(
            render.day_page(
                day=day,
                totals=aggregate.totals_for_day(db, day),
                blocks=aggregate.timeline(db, day, self.tz),
                rows=repo_sessions.sessions_for_day(db, day),
                categories=self.categories(),
                tz=self.tz,
                day_bounds=bounds,
                review=self.review(),
                token=self.server.token,
                is_today=day == self.today(),
            )
        )

    def _week(self, day: str):
        db = self.engine.db
        days = aggregate.week_of(day, self.cfg.week_start)
        start = week_start(day, self.cfg.week_start)
        return self._html(
            render.week_page(
                days=days,
                per_day=aggregate.totals_for_days(db, days),
                categories=self.categories(),
                apps=aggregate.top_apps(db, days[0], days[-1]),
                domains=aggregate.top_domains(db, days[0], days[-1]),
                review=self.review(),
                token=self.server.token,
                week_label=f"Week of {days[0]}",
                prev_week=add_days(start, -7),
                next_week=add_days(start, 7),
            )
        )

    def _review(self):
        return self._html(
            render.review_page(
                rows=repo_sessions.review_queue(self.engine.db, limit=150),
                categories=self.categories(),
                tz=self.tz,
                review=self.review(),
                token=self.server.token,
            )
        )

    def _rules(self, message: str = ""):
        return self._html(
            render.rules_page(
                rules=repo_rules.rules_with_stats(self.engine.db),
                categories=self.categories(),
                model=self.engine.model_status(),
                token=self.server.token,
                message=message,
            )
        )

    def _export(self, message: str = ""):
        from ..export import xlsx_export

        days = repo_sessions.days_with_data(self.engine.db)
        today = self.today()
        return self._html(
            render.export_page(
                days=days,
                token=self.server.token,
                default_from=add_days(today, -13),
                default_to=today,
                message=message,
                xlsx_available=xlsx_export.available(),
            )
        )

    def _settings(self, message: str = "", preview: str = ""):
        from ..backends.factory import get_autostart, get_secrets
        from ..core.retention import database_size_bytes
        from ..llm import assist

        return self._html(
            render.settings_page(
                cfg=self.cfg,
                model=self.engine.model_status(),
                secrets_desc=get_secrets().describe(),
                llm_stats=assist.usage_stats(self.engine.db),
                db_size=database_size_bytes(self.engine.db),
                data_dir=str(self.engine.db.path.parent),
                autostart=get_autostart().status(),
                token=self.server.token,
                message=message,
                preview=preview,
            )
        )

    # ---- POST ----------------------------------------------------------

    def post(self, path: str, payload: dict):
        if path == "/api/correct":
            return self._json(
                self.engine.apply_correction(
                    int(payload.get("session_id", 0)),
                    str(payload.get("category", "")),
                    str(payload.get("scope", "session")),
                )
            )
        if path == "/api/confirm":
            ok = self.engine.mark_reviewed(int(payload.get("session_id", 0)))
            return self._json({"ok": ok})
        if path == "/api/rule/delete":
            return self._json(self.engine.delete_rule(int(payload.get("rule_id", 0))))
        if path == "/rules/add":
            result = self.engine.add_rule(
                str(payload.get("kind", "")),
                str(payload.get("pattern", "")),
                str(payload.get("category", "")),
            )
            message = (
                f"Rule added — {result['reclassified']} sessions updated."
                if result.get("ok")
                else f"Could not add that rule: {result.get('error')}"
            )
            return self._redirect(f"/rules?msg={quote(message)}")
        if path == "/settings/rebuild":
            n = self.engine.rebuild_model()
            return self._redirect(
                f"/settings?msg={quote(f'Model rebuilt from {n} corrections.')}"
            )
        if path == "/settings/llm-preview":
            from ..llm import assist

            preview = assist.preview_payload(self.engine)
            return self._settings(preview=preview)
        if path == "/export/run":
            return self._run_export(payload)
        return None

    def _run_export(self, payload: dict):
        from ..export import csv_export, xlsx_export

        today = self.today()
        start = payload.get("from") or add_days(today, -13)
        end = payload.get("to") or today
        fmt = payload.get("format", "csv")
        rounding = int(payload.get("rounding") or 0)
        exclude = bool(payload.get("exclude_unreviewed"))
        try:
            if fmt == "xlsx" and xlsx_export.available():
                written = [
                    xlsx_export.write_workbook(
                        self.engine, start, end, rounding_min=rounding,
                        exclude_unreviewed=exclude,
                    )
                ]
            else:
                written = csv_export.write_all(
                    self.engine, start, end, rounding_min=rounding,
                    exclude_unreviewed=exclude,
                )
        except Exception as exc:
            return self._redirect(f"/export?msg={quote(f'Export failed: {exc}')}")
        names = ", ".join(p.name for p in written)
        location = written[0].parent if written else ""
        return self._redirect(
            f"/export?msg={quote(f'Wrote {names} to {location}')}"
        )

    # ---- shared --------------------------------------------------------

    def status_payload(self) -> dict:
        totals = aggregate.totals_for_day(self.engine.db, self.today())
        count, seconds = self.review()
        return {
            "day": self.today(),
            "summary": aggregate.summary_line(totals),
            "categories": [
                {"key": c.key, "name": c.name, "seconds": c.seconds, "color": c.color}
                for c in totals.categories
            ],
            "idle_seconds": totals.idle_seconds,
            "review": {"count": count, "seconds": seconds},
        }
