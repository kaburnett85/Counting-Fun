"""URL parsing, title cleanup, and feature extraction."""

from __future__ import annotations

import pytest

from timesplit.core.features import extract_features, signature, tokenize_title
from timesplit.core.models import Activity
from timesplit.core.urls import (
    domain_matches_suffix,
    first_path_segment,
    normalize_url,
    registrable_domain,
    strip_browser_suffix,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Zillow - 12 Oak St - Google Chrome", "Zillow - 12 Oak St"),
        ("Canvas Gradebook - Chromium", "Canvas Gradebook"),
        ("MLS Search - Comet", "MLS Search"),
        ("Loop - dotloop - Perplexity Comet", "Loop - dotloop"),
        # Edge has shipped a non-breaking space in its own name.
        ("Inbox - Outlook - Microsoft​ Edge", "Inbox - Outlook"),
        ("Canvas and 3 more pages - Personal - Microsoft Edge", "Canvas"),
        ("(3) Inbox - Gmail - Google Chrome", "Inbox - Gmail"),
        ("Budget.xlsx - Excel", "Budget.xlsx - Excel"),  # not a browser suffix
        ("", ""),
    ],
)
def test_browser_suffixes_are_stripped(raw, expected):
    assert strip_browser_suffix(raw) == expected


@pytest.mark.parametrize(
    "host,expected",
    [
        ("https://portal.university.edu/x", "university.edu"),
        ("canvas.instructure.com", "instructure.com"),
        ("https://www.zillow.com/homes", "zillow.com"),
        ("bbc.co.uk", "bbc.co.uk"),
        ("news.bbc.co.uk", "bbc.co.uk"),
        ("zillow.com", "zillow.com"),
        ("192.168.1.10", "192.168.1.10"),
        (None, None),
        ("", None),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


def test_normalize_url_drops_query_and_fragment():
    assert normalize_url("HTTPS://ZILLOW.com/homes/12?utm=x#photos") == "https://zillow.com/homes/12"
    assert normalize_url("zillow.com/") == "https://zillow.com/"
    assert normalize_url(None) is None
    assert normalize_url("   ") is None


def test_first_path_segment_ignores_per_listing_ids():
    assert first_path_segment("https://matrix.mlsmatrix.com/Matrix/Search/998877") == "matrix"
    # A bare numeric first segment carries no signal.
    assert first_path_segment("https://example.com/123456") is None
    assert first_path_segment("https://example.com/") is None


def test_domain_suffix_matching():
    assert domain_matches_suffix("canvas.university.edu", "edu")
    assert domain_matches_suffix("university.edu", "edu")
    assert not domain_matches_suffix("nonedu.com", "edu")
    assert not domain_matches_suffix(None, "edu")


def test_tokenizer_drops_filler_and_numbers():
    tokens = tokenize_title("The Purchase Agreement for 12345 - Google Chrome")
    assert "purchase" in tokens and "agreement" in tokens
    assert "the" not in tokens and "for" not in tokens and "12345" not in tokens


def test_features_are_namespaced_and_stable():
    a = Activity(
        exe_name="chrome.exe",
        title="Purchase Agreement - dotloop - Google Chrome",
        url="https://www.dotloop.com/my/loops/98765",
    )
    feats = extract_features(a)
    assert feats == extract_features(a), "extraction must be deterministic"
    assert "exe:chrome.exe" in feats
    assert "dom:dotloop.com" in feats
    assert "seg:my" in feats
    assert "t:purchase" in feats
    assert "b:purchase_agreement" in feats
    # The per-loop id must not become a feature, or the vocabulary explodes.
    assert not any("98765" in f for f in feats)


def test_subdomain_yields_both_host_and_registrable_domain():
    feats = extract_features(Activity(exe_name="chrome.exe", url="https://canvas.university.edu/c"))
    assert "dom:canvas.university.edu" in feats
    assert "dom2:university.edu" in feats


def test_signature_is_stable_across_urls_of_the_same_page():
    a = Activity("chrome.exe", "Loop - dotloop - Google Chrome", "https://dotloop.com/a", "dotloop.com")
    b = Activity("CHROME.EXE", "Loop - dotloop", "https://dotloop.com/b", "dotloop.com")
    assert signature(a) == signature(b)
    c = Activity("chrome.exe", "Something else", "https://dotloop.com/a", "dotloop.com")
    assert signature(a) != signature(c)
