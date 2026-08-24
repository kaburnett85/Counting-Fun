"""Matching an activity against explicit rules.

Rules are compiled once into per-kind lookup structures, because this runs on
every classification. Within a priority band, the more specific kind wins: a
rule about one URL beats a rule about the whole site, which beats a rule about
the program.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Rule
from .urls import domain_matches_suffix, host_of, normalize_url, strip_browser_suffix

#: Most specific first. Index in this list is the tie-breaker within a priority.
KIND_SPECIFICITY = {
    "url_prefix": 0,
    "domain": 1,
    "domain_suffix": 2,
    "title_regex": 3,
    "title_contains": 4,
    "exe": 5,
}


@dataclass(frozen=True)
class RuleMatch:
    rule: Rule
    specificity: int


class RuleSet:
    def __init__(self, rules: list[Rule]):
        self._by_kind: dict[str, list[Rule]] = {}
        self._regex: dict[int, re.Pattern[str]] = {}
        for rule in rules:
            if not rule.enabled:
                continue
            self._by_kind.setdefault(rule.kind, []).append(rule)
            if rule.kind == "title_regex":
                compiled = _safe_compile(rule.pattern)
                if compiled is not None and rule.id is not None:
                    self._regex[rule.id] = compiled
        # Longest pattern first: 'canvas.university.edu' should be tried before 'edu'.
        for kind in self._by_kind:
            self._by_kind[kind].sort(
                key=lambda r: (r.priority, len(r.pattern)), reverse=True
            )

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_kind.values())

    def match(self, activity) -> RuleMatch | None:
        """The winning rule: highest priority, then most specific kind."""
        best: RuleMatch | None = None
        for kind, rules in self._by_kind.items():
            specificity = KIND_SPECIFICITY.get(kind, 99)
            for rule in rules:
                if not self._matches(rule, kind, activity):
                    continue
                candidate = RuleMatch(rule, specificity)
                if best is None or _better(candidate, best):
                    best = candidate
                break  # rules within a kind are sorted; the first hit is the best
        return best

    def _matches(self, rule: Rule, kind: str, activity) -> bool:
        if kind == "exe":
            return (activity.exe_name or "").lower() == rule.pattern

        if kind == "url_prefix":
            url = normalize_url(activity.url)
            if not url:
                return False
            target = normalize_url(rule.pattern) or rule.pattern.lower()
            return url.lower().startswith(target.lower())

        if kind == "domain":
            host = (activity.domain or host_of(activity.url) or "").lower()
            return bool(host) and host == rule.pattern

        if kind == "domain_suffix":
            host = (activity.domain or host_of(activity.url) or "").lower()
            return domain_matches_suffix(host, rule.pattern)

        if kind == "title_contains":
            title = strip_browser_suffix(activity.title or "").lower()
            return rule.pattern.lower() in title

        if kind == "title_regex":
            compiled = self._regex.get(rule.id or -1)
            if compiled is None:
                return False
            return bool(compiled.search(strip_browser_suffix(activity.title or "")))

        return False


def _better(a: RuleMatch, b: RuleMatch) -> bool:
    if a.rule.priority != b.rule.priority:
        return a.rule.priority > b.rule.priority
    if a.specificity != b.specificity:
        return a.specificity < b.specificity
    return len(a.rule.pattern) > len(b.rule.pattern)


def _safe_compile(pattern: str) -> re.Pattern[str] | None:
    """A bad regex in one rule must not break classification for everything else."""
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def infer_rule(activity, scope: str) -> tuple[str, str] | None:
    """The rule a correction of this scope implies, as (kind, pattern)."""
    if scope == "same_domain":
        host = (activity.domain or host_of(activity.url) or "").lower()
        return ("domain", host) if host else None
    if scope == "same_exe":
        exe = (activity.exe_name or "").lower()
        return ("exe", exe) if exe else None
    if scope == "same_title":
        title = strip_browser_suffix(activity.title or "")
        return ("title_contains", title) if title else None
    return None
