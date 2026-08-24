"""The precedence chain: which signal wins, and what gets flagged for review."""

from __future__ import annotations

import pytest

from timesplit.config import ClassifierConfig, PrivacyConfig
from timesplit.core.classify import Classifier, ClassifierContext
from timesplit.core.features import extract_features
from timesplit.core.models import (
    RULE_PRIORITY_LLM,
    RULE_PRIORITY_SEED,
    RULE_PRIORITY_USER,
    SOURCE_EXCLUDED,
    SOURCE_LLM_RULE,
    SOURCE_MODEL,
    SOURCE_NONE,
    SOURCE_USER_RULE,
    Activity,
    Rule,
)
from timesplit.core.naive_bayes import NaiveBayes
from timesplit.core.rules import RuleSet

SCHOOL, REAL_ESTATE, UNKNOWN, EXCLUDED = 1, 2, 3, 4


def build(rules=(), model=None, **cfg_kwargs) -> Classifier:
    ctx = ClassifierContext(
        rules=RuleSet(list(rules)),
        model=model or NaiveBayes(),
        job_category_ids=[SCHOOL, REAL_ESTATE],
        unknown_id=UNKNOWN,
        excluded_id=EXCLUDED,
    )
    cfg = ClassifierConfig(**cfg_kwargs)
    return Classifier(ctx, cfg, PrivacyConfig())


def trained_model(assignments) -> NaiveBayes:
    model = NaiveBayes()
    for activity, label in assignments:
        for _ in range(3):
            model.learn(extract_features(activity), label)
    return model


def test_password_manager_is_excluded_but_time_still_counts():
    c = build()
    decision = c.classify(Activity("keepassxc.exe", "My Vault"))
    assert decision.category_id == EXCLUDED
    assert decision.source == SOURCE_EXCLUDED
    assert not decision.needs_review


def test_private_browsing_is_excluded():
    c = build()
    decision = c.classify(
        Activity("chrome.exe", "Zillow - 12 Oak (Incognito)", "https://zillow.com/x", "zillow.com")
    )
    assert decision.source == SOURCE_EXCLUDED


def test_user_rule_beats_seed_rule():
    rules = [
        Rule(id=1, kind="domain", pattern="zillow.com", category_id=REAL_ESTATE,
             priority=RULE_PRIORITY_SEED, source="seed"),
        Rule(id=2, kind="domain", pattern="zillow.com", category_id=SCHOOL,
             priority=RULE_PRIORITY_USER, source="user"),
    ]
    decision = build(rules).classify(Activity("chrome.exe", "x", "https://zillow.com/a", "zillow.com"))
    assert decision.category_id == SCHOOL
    assert decision.source == SOURCE_USER_RULE


def test_llm_rule_sits_between_user_and_seed():
    rules = [
        Rule(id=1, kind="domain", pattern="dotloop.com", category_id=SCHOOL,
             priority=RULE_PRIORITY_SEED, source="seed"),
        Rule(id=2, kind="domain", pattern="dotloop.com", category_id=REAL_ESTATE,
             priority=RULE_PRIORITY_LLM, source="llm"),
    ]
    decision = build(rules).classify(
        Activity("chrome.exe", "loop", "https://dotloop.com/a", "dotloop.com")
    )
    assert decision.category_id == REAL_ESTATE
    assert decision.source == SOURCE_LLM_RULE


def test_more_specific_rule_wins_within_a_priority():
    rules = [
        Rule(id=1, kind="domain_suffix", pattern="edu", category_id=SCHOOL,
             priority=RULE_PRIORITY_SEED, source="seed"),
        Rule(id=2, kind="url_prefix", pattern="https://portal.myschool.edu/realestate",
             category_id=REAL_ESTATE, priority=RULE_PRIORITY_SEED, source="seed"),
    ]
    c = build(rules)
    school = c.classify(Activity("chrome.exe", "x", "https://portal.myschool.edu/courses",
                                 "portal.myschool.edu"))
    estate = c.classify(Activity("chrome.exe", "x", "https://portal.myschool.edu/realestate/1",
                                 "portal.myschool.edu"))
    assert school.category_id == SCHOOL
    assert estate.category_id == REAL_ESTATE


def test_rules_beat_the_model_even_when_the_model_disagrees():
    activity = Activity("chrome.exe", "Zillow", "https://zillow.com/a", "zillow.com")
    model = trained_model([(activity, SCHOOL)] * 10)
    rules = [Rule(id=1, kind="domain", pattern="zillow.com", category_id=REAL_ESTATE,
                  priority=RULE_PRIORITY_USER, source="user")]
    decision = build(rules, model=model, min_docs=1).classify(activity)
    assert decision.category_id == REAL_ESTATE
    assert decision.source == SOURCE_USER_RULE


def test_model_assigns_above_the_confident_threshold():
    activity = Activity("chrome.exe", "Escrow ledger", "https://dotloop.com/a", "dotloop.com")
    other = Activity("chrome.exe", "Gradebook", "https://canvas.edu/a", "canvas.edu")
    model = trained_model([(activity, REAL_ESTATE), (other, SCHOOL)])
    decision = build(model=model, min_docs=1).classify(activity)
    assert decision.category_id == REAL_ESTATE
    assert decision.source == SOURCE_MODEL
    assert not decision.needs_review


def test_middling_confidence_is_assigned_but_flagged():
    activity = Activity("chrome.exe", "Escrow ledger", "https://dotloop.com/a", "dotloop.com")
    other = Activity("chrome.exe", "Gradebook", "https://canvas.edu/a", "canvas.edu")
    model = trained_model([(activity, REAL_ESTATE), (other, SCHOOL)])
    # Force the middle band by demanding near-certainty to assign outright.
    c = build(model=model, min_docs=1, tau_assign=0.999999, tau_review=0.2)
    decision = c.classify(activity)
    assert decision.category_id == REAL_ESTATE
    assert decision.needs_review, "a guess the app is unsure of must reach the review queue"


def test_cold_model_stays_out_of_the_way():
    activity = Activity("chrome.exe", "Escrow", "https://dotloop.com/a", "dotloop.com")
    model = trained_model([(activity, REAL_ESTATE)])
    decision = build(model=model, min_docs=100).classify(activity)
    assert decision.category_id == UNKNOWN
    assert decision.source == SOURCE_NONE
    assert decision.needs_review


def test_nothing_matches_falls_through_to_review():
    decision = build().classify(Activity("steam.exe", "Library"))
    assert decision.category_id == UNKNOWN
    assert decision.needs_review
    assert decision.confidence == 0.0


def test_disabled_classifier_never_predicts():
    activity = Activity("chrome.exe", "Escrow", "https://dotloop.com/a", "dotloop.com")
    model = trained_model([(activity, REAL_ESTATE)] * 30)
    decision = build(model=model, min_docs=1, enabled=False).classify(activity)
    assert decision.source == SOURCE_NONE


def test_a_broken_regex_rule_does_not_break_classification():
    rules = [
        Rule(id=1, kind="title_regex", pattern="([unclosed", category_id=SCHOOL,
             priority=RULE_PRIORITY_USER, source="user"),
        Rule(id=2, kind="exe", pattern="word.exe", category_id=REAL_ESTATE,
             priority=RULE_PRIORITY_USER, source="user"),
    ]
    decision = build(rules).classify(Activity("word.exe", "Listing agreement"))
    assert decision.category_id == REAL_ESTATE


def test_explain_gives_a_reason_for_every_outcome():
    rules = [Rule(id=1, kind="exe", pattern="word.exe", category_id=SCHOOL,
                  priority=RULE_PRIORITY_USER, source="user")]
    c = build(rules)
    assert "Rule:" in c.explain(Activity("word.exe", "Doc"))["reason"]
    assert c.explain(Activity("steam.exe", "Library"))["reason"]
    assert c.explain(Activity("keepassxc.exe", "Vault"))["reason"]


@pytest.mark.parametrize("kind,pattern,activity", [
    ("exe", "outlook.exe", Activity("OUTLOOK.EXE", "Inbox")),
    ("domain", "zillow.com", Activity("chrome.exe", "x", "https://zillow.com/a", "zillow.com")),
    ("domain_suffix", "edu", Activity("chrome.exe", "x", "https://a.b.edu/c", "a.b.edu")),
    ("title_contains", "escrow", Activity("word.exe", "Escrow instructions")),
    ("title_regex", r"lot\s+\d+", Activity("word.exe", "Lot 42 survey")),
    ("url_prefix", "https://dotloop.com/my", Activity("chrome.exe", "x", "https://dotloop.com/my/loops/1")),
])
def test_every_rule_kind_matches(kind, pattern, activity):
    rules = [Rule(id=1, kind=kind, pattern=pattern.lower() if kind in
                  ("exe", "domain", "domain_suffix") else pattern,
                  category_id=SCHOOL, priority=RULE_PRIORITY_USER, source="user")]
    assert build(rules).classify(activity).category_id == SCHOOL
