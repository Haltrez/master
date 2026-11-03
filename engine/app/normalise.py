"""Employer / title / location cleaning. Pure functions.

Phase 1 needs these to fill the not-null *_norm columns and to match
employer_overrides. Phase 2 hardens them with a large test suite built
from real fixture titles.
"""

from __future__ import annotations

import hashlib
import re

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACES = re.compile(r"\s+")

# Token sequences stripped from the end of employer names, longest first.
_LEGAL_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("uk", "limited"),
    ("uk", "ltd"),
    ("limited",),
    ("ltd",),
    ("plc",),
    ("llp",),
)


def _clean(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def employer_norm(name: str) -> str:
    tokens = _clean(name or "").split()
    changed = True
    while changed and tokens:
        changed = False
        for suffix in _LEGAL_SUFFIXES:
            n = len(suffix)
            if len(tokens) > n and tuple(tokens[-n:]) == suffix:
                tokens = tokens[:-n]
                changed = True
                break
    return " ".join(tokens)


_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")
_SALARY = re.compile(
    r"£\s?\d[\d,.]*\s?k?(\s?-\s?£?\s?\d[\d,.]*\s?k?)?"
    r"|\b\d{2,3}(,\d{3})?\s?k\b(\s?-\s?\d{2,3}\s?k\b)?"
    r"|\bup to\b|\bper annum\b|\bpa\b|\bp/?h\b|\bper hour\b|\bdoe\b|\bplus benefits\b|\b\+ benefits\b",
    re.IGNORECASE,
)
_NOISE_PHRASES = (
    "immediate start",
    "maternity cover",
    "fully remote",
    "urgent",
    "hybrid",
    "remote",
    "x2",
    "x3",
    "ftc",
)
_NOISE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _NOISE_PHRASES) + r")\b", re.IGNORECASE)


def title_norm(title: str, location: str | None = None) -> str:
    t = (title or "").lower()
    t = _BRACKETS.sub(" ", t)
    t = _SALARY.sub(" ", t)
    t = _NOISE.sub(" ", t)
    if location:
        town = location_norm(location)
        if town:
            t = re.sub(r"\b" + re.escape(town) + r"\b", " ", t)
    t = t.replace("&", " and ")
    t = _PUNCT.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


def location_norm(location_name: str | None) -> str:
    if not location_name:
        return ""
    main = location_name.split(",")[0]
    return _clean(main)


def lead_key(employer: str, title: str, location: str) -> str:
    return hashlib.sha1(f"{employer}|{title}|{location}".encode("utf-8")).hexdigest()
