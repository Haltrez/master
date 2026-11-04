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


# Bracketed words are dropped ("(Leeds)", "(Hybrid)", "(Python)") except a
# discipline, which makes it a different role: "Estimator (Mechanical)".
_KEEP_IN_BRACKETS = re.compile(r"\b(electrical|mechanical|m\s?&\s?e|mep|hvac|civil|structural|building services|"
                               r"fire|plumbing|gas|commercial|residential|infrastructure)\b", re.IGNORECASE)


def _bracket(m: re.Match) -> str:
    kept = [k.group(0) for k in _KEEP_IN_BRACKETS.finditer(m.group(0))]
    return " " + " ".join(kept) + " "


def title_norm(title: str, location: str | None = None) -> str:
    t = (title or "").lower()
    t = _BRACKETS.sub(_bracket, t)
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


_POSTCODE = re.compile(r"\b([A-Z]{1,2})\d[A-Z\d]?(?:\s*\d[A-Z]{2})?\b", re.IGNORECASE)

# Reed often gives only a postcode ("M53EB", "WA2 0YL"). Map the postcode area
# to its post town so "M53EB" and "Manchester" are the same place.
POSTCODE_AREA_TOWN = {
    "AB": "aberdeen", "AL": "st albans", "B": "birmingham", "BA": "bath", "BB": "blackburn", "BD": "bradford",
    "BH": "bournemouth", "BL": "bolton", "BN": "brighton", "BR": "bromley", "BS": "bristol", "BT": "belfast",
    "CA": "carlisle", "CB": "cambridge", "CF": "cardiff", "CH": "chester", "CM": "chelmsford", "CO": "colchester",
    "CR": "croydon", "CT": "canterbury", "CV": "coventry", "CW": "crewe", "DA": "dartford", "DD": "dundee",
    "DE": "derby", "DG": "dumfries", "DH": "durham", "DL": "darlington", "DN": "doncaster", "DT": "dorchester",
    "DY": "dudley", "E": "london", "EC": "london", "EH": "edinburgh", "EN": "enfield", "EX": "exeter",
    "FK": "falkirk", "FY": "blackpool", "G": "glasgow", "GL": "gloucester", "GU": "guildford", "GY": "guernsey",
    "HA": "harrow", "HD": "huddersfield", "HG": "harrogate", "HP": "hemel hempstead", "HR": "hereford",
    "HS": "outer hebrides", "HU": "hull", "HX": "halifax", "IG": "ilford", "IM": "isle of man", "IP": "ipswich",
    "IV": "inverness", "JE": "jersey", "KA": "kilmarnock", "KT": "kingston upon thames", "KW": "kirkwall",
    "KY": "kirkcaldy", "L": "liverpool", "LA": "lancaster", "LD": "llandrindod wells", "LE": "leicester",
    "LL": "llandudno", "LN": "lincoln", "LS": "leeds", "LU": "luton", "M": "manchester", "ME": "rochester",
    "MK": "milton keynes", "ML": "motherwell", "N": "london", "NE": "newcastle upon tyne", "NG": "nottingham",
    "NN": "northampton", "NP": "newport", "NR": "norwich", "NW": "london", "OL": "oldham", "OX": "oxford",
    "PA": "paisley", "PE": "peterborough", "PH": "perth", "PL": "plymouth", "PO": "portsmouth", "PR": "preston",
    "RG": "reading", "RH": "redhill", "RM": "romford", "S": "sheffield", "SA": "swansea", "SE": "london",
    "SG": "stevenage", "SK": "stockport", "SL": "slough", "SM": "sutton", "SN": "swindon", "SO": "southampton",
    "SP": "salisbury", "SR": "sunderland", "SS": "southend on sea", "ST": "stoke on trent", "SW": "london",
    "SY": "shrewsbury", "TA": "taunton", "TD": "galashiels", "TF": "telford", "TN": "tonbridge", "TQ": "torquay",
    "TR": "truro", "TS": "middlesbrough", "TW": "twickenham", "UB": "southall", "W": "london", "WA": "warrington",
    "WC": "london", "WD": "watford", "WF": "wakefield", "WN": "wigan", "WR": "worcester", "WS": "walsall",
    "WV": "wolverhampton", "YO": "york", "ZE": "shetland",
}
_POSTCODE_ONLY = re.compile(r"^\s*([A-Z]{1,2})(\d[A-Z\d]?)\s*(\d[A-Z]{2})?\s*$", re.IGNORECASE)


def location_norm(location_name: str | None) -> str:
    """Lowercase main town or city: 'Leeds, West Yorkshire' -> 'leeds', 'Leeds LS1' -> 'leeds',
    'M53EB' -> 'manchester' (postcode area's post town)."""
    if not location_name:
        return ""
    main = location_name.split(",")[0]
    pc = _POSTCODE_ONLY.match(main)
    if pc and pc.group(1).upper() in POSTCODE_AREA_TOWN:
        return POSTCODE_AREA_TOWN[pc.group(1).upper()]
    main = _POSTCODE.sub(" ", main)
    main = re.sub(r"\b(?:city centre|town centre|centre)\b", " ", main, flags=re.IGNORECASE)
    return _clean(main)


def lead_key(employer: str, title: str, location: str) -> str:
    return hashlib.sha1(f"{employer}|{title}|{location}".encode("utf-8")).hexdigest()
