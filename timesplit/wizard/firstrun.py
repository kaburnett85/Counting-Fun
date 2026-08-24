"""First-run setup: about ninety seconds, and it makes day one mostly right."""

from __future__ import annotations

import contextlib
import sys

from .. import paths
from ..config import REAL_ESTATE, SCHOOL, CategoryConfig, Config, load_config, save_config
from ..core.naive_bayes import NaiveBayes
from ..store import repo_categories, repo_model, repo_rules
from ..store.db import open_database
from .seeds import apply_seed_rules, seed_model_priors

RULE_KIND_FOR_INPUT = "domain"


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


def _ask_yes(prompt: str, default: bool = True) -> bool:
    answer = _ask(f"{prompt} (y/n)", "y" if default else "n").lower()
    return answer.startswith("y")


def _split(raw: str) -> list[str]:
    parts = []
    for chunk in raw.replace(";", ",").split(","):
        item = chunk.strip().strip("\"'")
        if item:
            parts.append(item)
    return parts


def run_wizard(cfg: Config | None = None) -> int:
    paths.ensure_data_dir()
    cfg = cfg or load_config()

    print("\n  TimeSplit setup")
    print("  " + "-" * 46)
    print("  Two jobs, tracked automatically in the background.")
    print("  Everything stays on this computer.\n")

    # 1. The two jobs.
    first = cfg.categories[0] if cfg.categories else CategoryConfig(SCHOOL, "School", "#3b7dd8")
    second = (
        cfg.categories[1]
        if len(cfg.categories) > 1
        else CategoryConfig(REAL_ESTATE, "Real Estate", "#2f9e63")
    )
    first.display_name = _ask("  Name of your first job", first.display_name)
    second.display_name = _ask("  Name of your second job", second.display_name)

    print("\n  One sentence about each, so it can tell them apart.")
    first.description = _ask(f"  {first.display_name} is", first.description)
    second.description = _ask(f"  {second.display_name} is", second.description)
    cfg.categories = [first, second]

    # 2. Sites and programs, which become rules straight away.
    print("\n  Which websites and programs belong to each? Comma separated.")
    print("  You can skip these and just correct things as you go.\n")
    answers: dict[str, dict[str, list[str]]] = {}
    for cat in (first, second):
        sites = _split(_ask(f"  {cat.display_name} websites", ""))
        apps = _split(_ask(f"  {cat.display_name} programs", ""))
        answers[cat.key] = {"domain": sites, "exe": apps}

    # 3. Idle threshold.
    print()
    raw = _ask("  Count you as away after how many minutes idle", str(cfg.idle.threshold_s // 60))
    with contextlib.suppress(ValueError):
        cfg.idle.threshold_s = max(30, int(float(raw) * 60))

    # 4. Timezone.
    from ..core.clock import resolve_zone

    detected = str(resolve_zone(""))
    cfg.timezone = _ask("  Timezone", cfg.timezone or detected)

    # 5. Write everything.
    cfg.first_run_complete = True
    save_config(cfg)

    db = open_database()
    try:
        category_ids = repo_categories.sync_from_config(db, cfg)

        seeded = 0
        if _ask_yes("\n  Add the built-in list of school and real-estate sites?", True):
            seeded = apply_seed_rules(db, category_ids)

        added = 0
        for key, kinds in answers.items():
            category_id = category_ids.get(key)
            if category_id is None:
                continue
            for kind, patterns in kinds.items():
                for pattern in patterns:
                    value = pattern.lower()
                    if kind == "exe" and not value.endswith(".exe"):
                        value += ".exe"
                    if kind == "domain":
                        from ..core.urls import host_of

                        value = host_of(value) or value
                    try:
                        repo_rules.add_rule(db, kind, value, category_id, source="wizard",
                                            note="from setup")
                        added += 1
                    except ValueError:
                        continue

        model = NaiveBayes(alpha=cfg.classifier.alpha)
        tokens, classes = repo_model.load_counts(db)
        model.load(tokens, classes)
        seed_model_priors(db, category_ids, model)

        print(f"\n  Done. {added} rule(s) from your answers, {seeded} built-in.")
        print(f"  Data folder: {paths.data_dir()}")

        if sys.platform == "win32" and _ask_yes("  Start TimeSplit when you log in?", True):
            from ..backends.factory import get_autostart

            ok, message = get_autostart().install()
            print(f"  {message}")

        print("\n  Start it with:  python -m timesplit run")
        print("  Then open the dashboard from the tray icon.\n")
    finally:
        db.close()
    return 0
