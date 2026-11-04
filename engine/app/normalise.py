"""Employer / title / location cleaning. Pure functions, heavily unit-tested.

The same role posted again ("Management Accountant - Hybrid (Leeds) £45k",
"URGENT: Management Accountant") must land on the same lead key, while
different roles ("Senior Management Accountant", "Accounts Assistant") must
not. Seniority words are always kept.
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
    """Lowercase, strip punctuation and trailing legal suffixes. Keeps 'group', 'holdings'."""
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
_MONEY = (
    r"£\s?\d[\d,.]*\s?k?(?:\s?(?:-|–|to)\s?£?\s?\d[\d,.]*\s?k?)?"   # £45k, £40,000 - £45,000, £14.50
    r"|\b\d{2,3}(?:,\d{3})?\s?k\b(?:\s?(?:-|–|to)\s?\d{2,3}\s?k\b)?"  # 45k, 40k-45k
    r"|\b\d{2,3},\d{3}\b"                                            # 45,000
)
_SALARY_WORDS = (r"\bup to\b|\bper annum\b|\bp\.?a\.?(?=\s|$)|\bper hour\b|\bp/?h\b|\bph\b|\bdoe\b|"
                 r"\bplus (?:benefits|bonus|car)\b|\+\s?(?:benefits|bonus|car)\b|\bbonus\b|\bbenefits\b|\bsalary\b|"
                 r"\bneg(?:otiable)?\b|\bcompetitive\b|\bota\b")
_MONEY_RE = re.compile(_MONEY + "|" + _SALARY_WORDS, re.IGNORECASE)
_NOISE_PHRASES = (
    "immediate start", "maternity cover", "maternity leave cover", "fixed term contract", "fully remote",
    "remote working", "hybrid working", "flexible working", "part time", "full time", "permanent",
    "temp to perm", "urgent", "urgently required", "hybrid", "remote", "ftc", "x2", "x3", "x4", "2x", "3x",
    "new role", "new opportunity", "immediate",
)
_NOISE = re.compile(r"\b(" + "|".join(re.escape(p) for p in sorted(_NOISE_PHRASES, key=len, reverse=True)) + r")\b",
                    re.IGNORECASE)
_MONTH_CONTRACT = re.compile(r"\b\d+\s?(?:-\s?)?months?\b(?:\s(?:contract|ftc))?", re.IGNORECASE)
_LOCATION_JOINERS = re.compile(r"\b(?:based in|located in|in|near|nr|across|covering)\s*$", re.IGNORECASE)
_TRAILING_JUNK = re.compile(r"(?:\s*\b(?:and|or|with|to|for|the|a|role|job|vacancy)\b)+\s*$", re.IGNORECASE)


def title_norm(title: str, location: str | None = None) -> str:
    t = (title or "").lower()
    t = _BRACKETS.sub(" ", t)
    t = _MONEY_RE.sub(" ", t)
    t = _MONTH_CONTRACT.sub(" ", t)
    t = _NOISE.sub(" ", t)
    if location:
        for town in {location_norm(location), _clean(location.split(",")[0])}:
            if town:
                t = re.sub(r"\b" + re.escape(town) + r"\b(?:\s*(?:city centre|centre|based|area))?", " ", t)
    t = t.replace("&", " and ")
    # Cut location joiners left dangling after the town was removed: "accountant in" -> "accountant"
    parts = [p.strip() for p in re.split(r"\s[-–—|/:]\s|\s[-–—|:]|[-–—|:]\s", t)]
    parts = [p for p in parts if _PUNCT.sub(" ", p).strip()]
    t = " ".join(parts)
    t = _PUNCT.sub(" ", t)
    t = _SPACES.sub(" ", t).strip()
    t = _LOCATION_JOINERS.sub("", t).strip()
    t = _TRAILING_JUNK.sub("", t).strip()
    return t


_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?(?:\s*\d[A-Z]{2})?\b", re.IGNORECASE)


def location_norm(location_name: str | None) -> str:
    """Lowercase main town or city: 'Leeds, West Yorkshire' -> 'leeds', 'Leeds LS1' -> 'leeds'."""
    if not location_name:
        return ""
    main = location_name.split(",")[0]
    main = _POSTCODE.sub(" ", main)
    main = re.sub(r"\b(?:city centre|town centre|centre)\b", " ", main, flags=re.IGNORECASE)
    return _clean(main)


def lead_key(employer: str, title: str, location: str) -> str:
    return hashlib.sha1(f"{employer}|{title}|{location}".encode("utf-8")).hexdigest()
