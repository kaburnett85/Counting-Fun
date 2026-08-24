"""Loading the shipped seed rules into the database.

Seed rules do two things: they work immediately, before any training, and they
inject pseudo-counts into the learned model so its first guesses lean the
right way instead of being a coin flip.
"""

from __future__ import annotations

import json

from .. import paths
from ..core.features import extract_features
from ..core.models import Activity
from ..core.naive_bayes import NaiveBayes
from ..logging_setup import get as get_logger
from ..store import repo_model, repo_rules
from ..store.db import Database

log = get_logger("wizard.seeds")

SEED_PSEUDO_WEIGHT = 5.0


def load_seed_definitions() -> dict[str, dict[str, list[str]]]:
    path = paths.package_data_dir() / "seed_rules.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - packaging accident
        log.warning("could not read seed rules: %s", exc)
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def apply_seed_rules(db: Database, category_ids: dict[str, int]) -> int:
    """Insert the seed pack. Existing user rules are never downgraded."""
    added = 0
    for category_key, kinds in load_seed_definitions().items():
        category_id = category_ids.get(category_key)
        if category_id is None:
            continue
        for kind, patterns in kinds.items():
            for pattern in patterns:
                try:
                    repo_rules.add_rule(
                        db, kind, pattern, category_id,
                        source="seed", note="shipped default", replace=False,
                    )
                    added += 1
                except ValueError:
                    continue
    return added


def seed_model_priors(db: Database, category_ids: dict[str, int], model: NaiveBayes) -> None:
    """Give the model a starting lean from the rules, without faking corrections.

    Seeded counts shape predictions but do not count as documents, so the model
    still reports itself cold until you have actually corrected things.
    """
    for category_key, kinds in load_seed_definitions().items():
        category_id = category_ids.get(category_key)
        if category_id is None:
            continue
        for kind, patterns in kinds.items():
            for pattern in patterns:
                activity = _activity_for(kind, pattern)
                if activity is None:
                    continue
                feats = extract_features(activity)
                if feats:
                    model.seed(feats, category_id, weight=SEED_PSEUDO_WEIGHT)
    tokens, classes = model.take_delta()
    if tokens or classes:
        repo_model.save_delta(db, tokens, classes)


def seed_model_from_rules(db: Database, model: NaiveBayes) -> None:
    """Same idea, but from whatever rules exist now (including yours)."""
    for rule in repo_rules.all_rules(db):
        activity = _activity_for(rule.kind, rule.pattern)
        if activity is None:
            continue
        feats = extract_features(activity)
        if feats:
            model.seed(feats, rule.category_id, weight=SEED_PSEUDO_WEIGHT)
    tokens, classes = model.take_delta()
    if tokens or classes:
        repo_model.save_delta(db, tokens, classes)


def _activity_for(kind: str, pattern: str) -> Activity | None:
    """A synthetic activity representing what this rule matches."""
    if kind in ("domain", "domain_suffix"):
        return Activity(exe_name="", title="", url=f"https://{pattern}/", domain=pattern)
    if kind == "url_prefix":
        return Activity(exe_name="", title="", url=pattern)
    if kind == "title_contains":
        return Activity(exe_name="", title=pattern)
    if kind == "exe":
        return Activity(exe_name=pattern, title="")
    return None
