"""Turning a window into the tokens the classifier learns on.

Features are namespaced so the model can tell "the word chrome appeared in a
title" from "the process was chrome.exe":

    exe:chrome.exe          the program
    dom:portal.school.edu   the full host
    dom2:school.edu         the registrable domain
    seg:mls                 the first URL path segment
    t:escrow                a title word
    b:purchase_agreement    a pair of adjacent title words

Deliberately absent: time of day. It correlates spuriously -- "anything after
9pm is real estate" holds until the week it does not -- and makes the model's
behaviour feel arbitrary when a schedule shifts.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .. import paths
from .models import Activity
from .urls import first_path_segment, host_of, registrable_domain, strip_browser_suffix

MIN_TOKEN_LEN = 3
MAX_UNIGRAMS = 20
MAX_BIGRAMS = 10

_SPLIT = re.compile(r"[^a-z0-9]+")
_DIGITS = re.compile(r"^\d+$")


@lru_cache(maxsize=1)
def stopwords() -> frozenset[str]:
    path = paths.package_data_dir() / "stopwords.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:  # pragma: no cover - packaging accident
        return frozenset()
    return frozenset(
        line.strip().lower() for line in lines if line.strip() and not line.startswith("#")
    )


def tokenize_title(title: str) -> list[str]:
    """Lowercased words from a title, minus browser chrome and filler."""
    cleaned = strip_browser_suffix(title).lower()
    stops = stopwords()
    out: list[str] = []
    for raw in _SPLIT.split(cleaned):
        if len(raw) < MIN_TOKEN_LEN:
            continue
        if _DIGITS.match(raw):
            continue
        if raw in stops:
            continue
        out.append(raw)
        if len(out) >= MAX_UNIGRAMS:
            break
    return out


def extract_features(activity: Activity) -> list[str]:
    """The full feature list for one activity. Order is stable, duplicates removed."""
    feats: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        if token not in seen:
            seen.add(token)
            feats.append(token)

    exe = (activity.exe_name or "").lower().strip()
    if exe:
        add(f"exe:{exe}")

    host = host_of(activity.url) or (activity.domain or None)
    if host:
        add(f"dom:{host.lower()}")
        registrable = registrable_domain(host)
        if registrable and registrable != host:
            add(f"dom2:{registrable}")
    elif activity.domain:
        add(f"dom:{activity.domain.lower()}")

    segment = first_path_segment(activity.url)
    if segment:
        add(f"seg:{segment}")

    words = tokenize_title(activity.title or "")
    for word in words:
        add(f"t:{word}")
    for i in range(min(len(words) - 1, MAX_BIGRAMS)):
        add(f"b:{words[i]}_{words[i + 1]}")

    return feats


def signature(activity: Activity) -> str:
    """A stable identity for 'this same window again'.

    Used to deduplicate work before it reaches the optional Claude assist, so a
    hundred sessions on one page cost one item rather than a hundred.
    """
    import hashlib

    parts = [
        (activity.exe_name or "").lower(),
        (activity.domain or host_of(activity.url) or "").lower(),
        strip_browser_suffix(activity.title or "").lower(),
    ]
    return hashlib.sha1("|".join(parts).encode("utf-8", "replace")).hexdigest()


def activity_from_row(row: dict) -> Activity:
    """Build an Activity from a sessions-table row."""
    return Activity(
        exe_name=row.get("exe_name") or "",
        title=row.get("title") or "",
        url=row.get("url"),
        domain=row.get("domain"),
    )


def describe_features(feats: list[str]) -> str:
    """Readable version for the dashboard's 'why did it decide that' panel."""
    pretty = []
    for f in feats:
        kind, _, value = f.partition(":")
        label = {
            "exe": "app",
            "dom": "site",
            "dom2": "domain",
            "seg": "section",
            "t": "word",
            "b": "phrase",
        }.get(kind, kind)
        pretty.append(f"{label} {value.replace('_', ' ')}")
    return ", ".join(pretty)
