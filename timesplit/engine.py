"""The application service: everything the tracker and the dashboard share.

Holds the database, config, rules, and the learned model, and owns the two
operations that matter -- classifying an activity, and applying a correction.
Keeping the correction path in one place is what guarantees the model, the
rules, and the stored sessions never drift apart.
"""

from __future__ import annotations

import threading
from datetime import timezone
from zoneinfo import ZoneInfo

from .config import EXCLUDED, UNKNOWN, Config
from .core.classify import Classifier, ClassifierContext
from .core.clock import add_days, local_day, resolve_zone
from .core.features import activity_from_row, extract_features
from .core.models import SOURCE_MANUAL, Activity, Decision
from .core.naive_bayes import NaiveBayes, correction_weight, rebuild
from .core.reclassify import reclassify_range, reclassify_sessions
from .core.rules import RuleSet, infer_rule
from .logging_setup import get as get_logger
from .store import (
    repo_categories,
    repo_corrections,
    repo_model,
    repo_rules,
    repo_sessions,
)
from .store.db import Database

log = get_logger("engine")

#: How far back a rule change re-sweeps, on top of all old unknowns.
RECLASSIFY_WINDOW_DAYS = 30


class Engine:
    def __init__(self, db: Database, cfg: Config):
        self.db = db
        self.cfg = cfg
        self.tz: timezone | ZoneInfo = resolve_zone(cfg.timezone)
        self._lock = threading.RLock()
        self.category_ids: dict[str, int] = {}
        self.model = NaiveBayes(alpha=cfg.classifier.alpha)
        self.classifier: Classifier | None = None
        self.reload()

    # ---- setup ---------------------------------------------------------

    def reload(self) -> None:
        """Re-read categories, rules and model counts from the database."""
        with self._lock:
            self.category_ids = repo_categories.sync_from_config(self.db, self.cfg)
            tokens, classes = repo_model.load_counts(self.db)
            self.model.alpha = self.cfg.classifier.alpha
            self.model.load(tokens, classes)
            self._rebuild_classifier()

    def ensure_seeded(self) -> int:
        """Put the shipped rules back if the database has none.

        This matters more than it looks. A database that had to be quarantined
        and recreated comes back empty, and without this the tracker would
        carry on happily filing everything as uncategorised. It also makes
        `run --demo` useful on a fresh machine.
        """
        from .store import repo_rules
        from .wizard.seeds import apply_seed_rules, seed_model_priors

        if repo_rules.count(self.db) > 0:
            return 0
        with self._lock:
            added = apply_seed_rules(self.db, self.category_ids)
            seed_model_priors(self.db, self.category_ids, self.model)
            self.reload()
        if added:
            log.info("restored %d starting rules to an empty database", added)
        return added

    def _rebuild_classifier(self) -> None:
        job_ids = [c["id"] for c in repo_categories.job_categories(self.db)]
        ctx = ClassifierContext(
            rules=RuleSet(repo_rules.all_rules(self.db)),
            model=self.model,
            job_category_ids=job_ids,
            unknown_id=self.category_ids.get(UNKNOWN),
            excluded_id=self.category_ids.get(EXCLUDED),
        )
        self.classifier = Classifier(ctx, self.cfg.classifier, self.cfg.privacy)

    # ---- classification ------------------------------------------------

    def classify(self, activity: Activity) -> Decision:
        assert self.classifier is not None
        with self._lock:
            return self.classifier.classify(activity)

    def explain(self, activity: Activity) -> dict:
        assert self.classifier is not None
        with self._lock:
            return self.classifier.explain(activity)

    def is_excluded(self, activity: Activity) -> bool:
        assert self.classifier is not None
        return self.classifier.is_excluded(activity)

    # ---- corrections ---------------------------------------------------

    def apply_correction(
        self,
        session_id: int,
        new_category_key: str,
        scope: str = "session",
    ) -> dict:
        """You told the app it was wrong. Everything follows from here.

        Writes the correction, teaches the model, creates a rule when the
        scope is wider than one session, and re-sweeps affected history.
        """
        with self._lock:
            row = repo_sessions.session(self.db, session_id)
            if row is None:
                return {"ok": False, "error": "That session no longer exists."}
            new_id = self.category_ids.get(new_category_key)
            if new_id is None:
                return {"ok": False, "error": f"Unknown category: {new_category_key}"}

            activity = activity_from_row(row)
            features = extract_features(activity)
            old_id = row["category_id"]
            weight = correction_weight(float(row["duration_s"]))

            # 1. Teach the model. Only human corrections ever do this.
            if features:
                if old_id is not None and old_id != new_id and row["source"] not in (
                    "none", "excluded"
                ):
                    self.model.unlearn(features, int(old_id), weight)
                self.model.learn(features, new_id, weight)
                tokens, classes = self.model.take_delta()
                if tokens or classes:
                    repo_model.save_delta(self.db, tokens, classes)

            # 2. Record it, so the model can always be rebuilt from scratch.
            repo_corrections.add(
                self.db,
                session_id=session_id,
                old_category_id=old_id,
                new_category_id=new_id,
                features=features,
                scope=scope,
                weight=weight,
            )

            # 3. Lock this session -- your decision is not up for revision.
            repo_sessions.set_category(
                self.db, session_id, new_id,
                confidence=1.0, source=SOURCE_MANUAL, rule_id=None,
                needs_review=False, lock=True,
            )

            result = {"ok": True, "updated": 1, "rule": None, "reclassified": 0}

            # 4. A wider scope becomes a rule, which is what makes it stick.
            if scope != "session":
                inferred = infer_rule(activity, scope)
                if inferred:
                    kind, pattern = inferred
                    try:
                        rule_id = repo_rules.add_rule(
                            self.db, kind, pattern, new_id,
                            source="user", note=f"from a correction ({scope})",
                        )
                        result["rule"] = {"id": rule_id, "kind": kind, "pattern": pattern}
                    except ValueError as exc:
                        log.warning("could not build a rule from the correction: %s", exc)

            self._rebuild_classifier()

            # 5. Apply it to history.
            if scope != "session":
                matched = self._sessions_matching(activity, scope)
                matched = [i for i in matched if i != session_id]
                result["reclassified"] = reclassify_sessions(
                    self.db, self.classifier, matched
                )
            self.prune_vocabulary()
            return result

    def _sessions_matching(self, activity: Activity, scope: str) -> list[int]:
        if scope == "same_title":
            return repo_sessions.matching_sessions(self.db, title=activity.title)
        if scope == "same_domain":
            if not activity.domain:
                return []
            return repo_sessions.matching_sessions(self.db, domain=activity.domain)
        if scope == "same_exe":
            return repo_sessions.matching_sessions(self.db, exe=activity.exe_name)
        return []

    def unlock_session(self, session_id: int) -> bool:
        """Hand a session back to the classifier."""
        with self._lock:
            self.db.execute(
                "UPDATE sessions SET is_locked = 0 WHERE id = ?", (session_id,)
            )
            assert self.classifier is not None
            reclassify_sessions(self.db, self.classifier, [session_id])
            return True

    def mark_reviewed(self, session_id: int) -> bool:
        """'This guess was right' -- clears the flag and teaches the model."""
        with self._lock:
            row = repo_sessions.session(self.db, session_id)
            if row is None or row["category_id"] is None:
                return False
            key = row.get("category_key")
            if not key or key in (UNKNOWN, EXCLUDED):
                self.db.execute(
                    "UPDATE sessions SET needs_review = 0 WHERE id = ?", (session_id,)
                )
                return True
        return self.apply_correction(session_id, key, "session").get("ok", False)

    # ---- rules ---------------------------------------------------------

    def add_rule(self, kind: str, pattern: str, category_key: str, source: str = "user") -> dict:
        with self._lock:
            category_id = self.category_ids.get(category_key)
            if category_id is None:
                return {"ok": False, "error": f"Unknown category: {category_key}"}
            try:
                rule_id = repo_rules.add_rule(
                    self.db, kind, pattern, category_id, source=source
                )
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            self._rebuild_classifier()
            changed = self._sweep()
            return {"ok": True, "rule_id": rule_id, "reclassified": changed}

    def delete_rule(self, rule_id: int) -> dict:
        with self._lock:
            repo_rules.delete_rule(self.db, rule_id)
            self._rebuild_classifier()
            return {"ok": True, "reclassified": self._sweep()}

    def set_rule_enabled(self, rule_id: int, enabled: bool) -> dict:
        with self._lock:
            repo_rules.set_enabled(self.db, rule_id, enabled)
            self._rebuild_classifier()
            return {"ok": True, "reclassified": self._sweep()}

    def _sweep(self, now_ts: float | None = None) -> int:
        """Re-decide recent history plus every old unknown."""
        import time

        assert self.classifier is not None
        today = local_day(now_ts if now_ts is not None else time.time(), self.tz)
        return reclassify_range(
            self.db,
            self.classifier,
            add_days(today, -RECLASSIFY_WINDOW_DAYS),
            today,
        )

    # ---- model maintenance ---------------------------------------------

    def rebuild_model(self) -> int:
        """Reconstruct the model from the correction log and store it."""
        with self._lock:
            corrections = repo_corrections.all_corrections(self.db)
            rebuilt = rebuild(corrections, alpha=self.cfg.classifier.alpha)
            repo_model.replace_all(self.db, rebuilt.tokens, rebuilt.classes)
            self.model = rebuilt
            self._rebuild_classifier()
            return len(corrections)

    def prune_vocabulary(self) -> int:
        removed = repo_model.prune_vocabulary(self.db, self.cfg.classifier.max_vocab)
        if removed:
            tokens, classes = repo_model.load_counts(self.db)
            self.model.load(tokens, classes)
        return removed

    # ---- convenience ---------------------------------------------------

    def category_key(self, category_id: int | None) -> str:
        if category_id is None:
            return UNKNOWN
        for key, cid in self.category_ids.items():
            if cid == category_id:
                return key
        return UNKNOWN

    def job_categories(self) -> list[dict]:
        return repo_categories.job_categories(self.db)

    def model_status(self) -> dict:
        from .store import repo_corrections as rc

        return {
            "documents": self.model.total_docs,
            "corrections": rc.count(self.db),
            "vocabulary": len(self.model.vocabulary),
            "warm": self.model.is_warm(self.cfg.classifier.min_docs),
            "min_docs": self.cfg.classifier.min_docs,
        }
