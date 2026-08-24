"""Deciding which job a window belongs to.

Precedence, first match wins:

  1. locked      you set this session by hand -- never re-decided
  2. excluded    a private window or a password manager
  3. user rules      (priority 1000)
  4. LLM rules       (priority  500, only if you enabled the assist)
  5. seed rules      (priority  100, shipped with the app)
  6. learned model   assigns above tau_assign; assigns-and-flags between
                     tau_review and tau_assign; otherwise falls through
  7. unknown     flagged for review, and queued for the assist if enabled

Time that lands in 6-with-a-flag or 7 still counts -- a day has to add up to
reality -- but it is rendered distinctly and surfaced in the review queue,
sorted longest first so a handful of clicks fixes most of the minutes.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ClassifierConfig, PrivacyConfig
from .features import extract_features
from .models import (
    RULE_PRIORITY_LLM,
    RULE_PRIORITY_USER,
    SOURCE_EXCLUDED,
    SOURCE_LLM_RULE,
    SOURCE_MODEL,
    SOURCE_NONE,
    SOURCE_SEED_RULE,
    SOURCE_USER_RULE,
    Activity,
    Decision,
)
from .naive_bayes import NaiveBayes
from .rules import RuleSet
from .urls import strip_browser_suffix

_SOURCE_BY_PRIORITY = {
    RULE_PRIORITY_USER: SOURCE_USER_RULE,
    RULE_PRIORITY_LLM: SOURCE_LLM_RULE,
}


@dataclass
class ClassifierContext:
    rules: RuleSet
    model: NaiveBayes
    job_category_ids: list[int]
    unknown_id: int | None
    excluded_id: int | None


class Classifier:
    def __init__(
        self,
        ctx: ClassifierContext,
        cfg: ClassifierConfig,
        privacy: PrivacyConfig,
    ):
        self.ctx = ctx
        self.cfg = cfg
        self.privacy = privacy

    def is_excluded(self, activity: Activity) -> bool:
        """Windows we deliberately do not record.

        The time still counts toward the day; only the content is dropped.
        """
        exe = (activity.exe_name or "").lower()
        if exe and exe in self.privacy.exclude_exes:
            return True
        title = strip_browser_suffix(activity.title or "").lower()
        return any(p.lower() in title for p in self.privacy.exclude_title_patterns if p)

    def classify(self, activity: Activity) -> Decision:
        if self.is_excluded(activity):
            return Decision(
                category_id=self.ctx.excluded_id,
                confidence=1.0,
                source=SOURCE_EXCLUDED,
            )

        match = self.ctx.rules.match(activity)
        if match is not None:
            source = _SOURCE_BY_PRIORITY.get(match.rule.priority, SOURCE_SEED_RULE)
            return Decision(
                category_id=match.rule.category_id,
                confidence=1.0,
                source=source,
                rule_id=match.rule.id,
            )

        if self.cfg.enabled and self.ctx.model.is_warm(self.cfg.min_docs):
            features = extract_features(activity)
            prediction = self.ctx.model.predict(features, allowed=self.ctx.job_category_ids)
            if prediction.category_id is not None:
                if prediction.confidence >= self.cfg.tau_assign:
                    return Decision(
                        category_id=prediction.category_id,
                        confidence=prediction.confidence,
                        source=SOURCE_MODEL,
                    )
                if prediction.confidence >= self.cfg.tau_review:
                    # Good enough to guess, not good enough to trust silently.
                    return Decision(
                        category_id=prediction.category_id,
                        confidence=prediction.confidence,
                        source=SOURCE_MODEL,
                        needs_review=True,
                    )

        return Decision(
            category_id=self.ctx.unknown_id,
            confidence=0.0,
            source=SOURCE_NONE,
            needs_review=True,
        )

    def explain(self, activity: Activity) -> dict:
        """Why the classifier decided what it did -- shown on the dashboard."""
        decision = self.classify(activity)
        out: dict = {"decision": decision, "reason": "", "evidence": []}
        if decision.source == SOURCE_EXCLUDED:
            out["reason"] = "This window is on your do-not-record list."
            return out
        if decision.rule_id is not None:
            match = self.ctx.rules.match(activity)
            if match:
                out["reason"] = f"Rule: {match.rule.kind.replace('_', ' ')} = {match.rule.pattern}"
            return out
        if decision.source == SOURCE_MODEL:
            features = extract_features(activity)
            prediction = self.ctx.model.predict(features, allowed=self.ctx.job_category_ids)
            out["reason"] = f"Learned from your corrections ({prediction.confidence:.0%} confident)"
            out["evidence"] = prediction.evidence
            return out
        out["reason"] = "Nothing matched yet -- tell it which job this is."
        return out
