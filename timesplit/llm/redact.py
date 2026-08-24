"""Stripping identifying detail out of window titles before they leave the machine.

The Claude assist only ever needs enough to tell School from Real Estate --
"Purchase agreement on dotloop" is sufficient, and the client's name, the case
number and the file path are not. Everything below is removed before the
payload is built, and the test suite asserts none of it survives.
"""

from __future__ import annotations

import re

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
#: Five or more digits: account, case, MLS, invoice and phone numbers.
LONG_DIGITS = re.compile(r"\b\d[\d\-.]{3,}\d\b")
WINDOWS_PATH = re.compile(r"\b[A-Za-z]:\\[^\s\"'<>|]*")
UNC_PATH = re.compile(r"\\\\[^\s\"'<>|]+")
POSIX_PATH = re.compile(r"(?:^|\s)(/(?:home|Users|var|etc)/[^\s\"'<>|]*)")
URL_IN_TITLE = re.compile(r"https?://\S+")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")

MAX_TITLE_LEN = 120


def redact_title(title: str, extra_patterns: list[str] | None = None) -> str:
    """Remove personal detail, keeping the words that indicate which job it is."""
    if not title:
        return ""
    text = title
    for pattern in extra_patterns or []:
        if pattern:
            text = re.sub(re.escape(pattern), "[hidden]", text, flags=re.IGNORECASE)
    text = SSN.sub("[id]", text)
    text = CARD.sub("[number]", text)
    text = EMAIL.sub("[email]", text)
    text = URL_IN_TITLE.sub("[link]", text)
    text = WINDOWS_PATH.sub("[path]", text)
    text = UNC_PATH.sub("[path]", text)
    text = POSIX_PATH.sub(" [path]", text)
    text = LONG_DIGITS.sub("[number]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_TITLE_LEN:
        text = text[:MAX_TITLE_LEN].rsplit(" ", 1)[0] + "…"
    return text


def redact_domain(domain: str | None) -> str:
    """Domains are sent as-is; an IP address is not."""
    if not domain:
        return ""
    d = domain.strip().lower()
    if re.fullmatch(r"[\d.]+", d) or ":" in d:
        return "[local]"
    return d


def contains_sensitive(text: str) -> bool:
    """Used by the tests to prove a built payload is clean."""
    return bool(
        EMAIL.search(text)
        or WINDOWS_PATH.search(text)
        or UNC_PATH.search(text)
        or SSN.search(text)
        or LONG_DIGITS.search(text)
    )
