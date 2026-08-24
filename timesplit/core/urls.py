"""URL and window-title normalisation.

Two jobs here:

* Reduce a URL to the parts that actually say something about which job it is
  -- the host, its registrable domain, and the first path segment. Deeper path
  segments are per-listing noise (a different MLS listing id every time) and
  would blow up the model's vocabulary for nothing.
* Strip the browser's own suffix from a window title, so "Zillow - Google
  Chrome" and "Zillow" are the same thing. Chromium forks each append their
  own, and Edge has historically used a non-breaking space, so the match is
  done on a normalised copy.
"""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlsplit

from .. import paths

#: Suffixes appended by browsers to the page title. Matched case-insensitively
#: after whitespace normalisation, longest first.
BROWSER_SUFFIXES = [
    "google chrome",
    "chromium",
    "microsoft edge",
    "microsoft​ edge",
    "mozilla firefox",
    "firefox",
    "brave",
    "vivaldi",
    "opera",
    "comet",
    "perplexity comet",
]

#: "and 3 more pages" style prefixes Edge and Chrome add for tab groups.
_MORE_PAGES = re.compile(r"\s+and\s+\d+\s+more\s+pages?\b", re.IGNORECASE)
_PERSONAL = re.compile(r"\s+[-–—]\s+(personal|work|profile \d+)\s*$", re.IGNORECASE)
_WS = re.compile(r"[\s ​  ]+")
_NOTIFICATION_COUNT = re.compile(r"^\(\d+\)\s*")


@lru_cache(maxsize=1)
def _public_suffixes() -> frozenset[str]:
    path = paths.package_data_dir() / "psl_min.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:  # pragma: no cover - packaging accident
        return frozenset()
    return frozenset(
        line.strip().lower() for line in lines if line.strip() and not line.startswith("#")
    )


def normalize_ws(text: str) -> str:
    """Collapse every flavour of whitespace, including the non-breaking kinds."""
    return _WS.sub(" ", text or "").strip()


def strip_browser_suffix(title: str) -> str:
    """'Zillow - 12 Oak St - Google Chrome' -> 'Zillow - 12 Oak St'."""
    text = normalize_ws(title)
    if not text:
        return ""
    text = _NOTIFICATION_COUNT.sub("", text)
    text = _MORE_PAGES.sub("", text)
    lowered = text.lower()
    for suffix in sorted(BROWSER_SUFFIXES, key=len, reverse=True):
        for dash in (" - ", " – ", " — ", " | "):
            tail = f"{dash}{suffix}"
            if lowered.endswith(tail):
                text = text[: -len(tail)]
                lowered = text.lower()
                break
    text = _PERSONAL.sub("", text)
    return text.strip(" -–—|")


def normalize_url(url: str | None) -> str | None:
    """Drop the fragment, query, credentials, and any trailing slash."""
    if not url:
        return None
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    path = parts.path or ""
    if path.endswith("/") and len(path) > 1:
        path = path[:-1]
    scheme = parts.scheme if parts.scheme in ("http", "https") else "https"
    return f"{scheme}://{host}{path}"


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    raw = url if "://" in url else "https://" + url
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


def registrable_domain(host_or_url: str | None) -> str | None:
    """'portal.university.edu' -> 'university.edu'; 'bbc.co.uk' -> 'bbc.co.uk'."""
    host = host_of(host_or_url)
    if not host:
        return None
    if _is_ip(host):
        return host
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    suffixes = _public_suffixes()
    last_two = ".".join(labels[-2:])
    if last_two in suffixes and len(labels) >= 3:
        return ".".join(labels[-3:])
    return last_two


def first_path_segment(url: str | None) -> str | None:
    """The first path segment only -- deeper ones are per-item noise."""
    if not url:
        return None
    raw = url if "://" in url else "https://" + url
    try:
        path = urlsplit(raw).path or ""
    except ValueError:
        return None
    for segment in path.split("/"):
        seg = segment.strip().lower()
        if not seg:
            continue
        if seg.isdigit() or len(seg) > 40:
            return None
        return seg
    return None


def _is_ip(host: str) -> bool:
    parts = host.split(".")
    return len(parts) == 4 and all(p.isdigit() and len(p) <= 3 for p in parts)


def domain_matches_suffix(domain: str | None, suffix: str) -> bool:
    """True for an exact match or a subdomain of the suffix ('.edu', 'k12.ca.us')."""
    if not domain:
        return False
    d, s = domain.lower(), suffix.lower().lstrip(".")
    return d == s or d.endswith("." + s)
