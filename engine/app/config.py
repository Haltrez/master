"""Settings from environment variables (and an optional .env file).

Secrets are never hardcoded. A `.env` file at the repo root or in the
current directory is read if present; real environment variables win.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_AGENCY_NAME_WORDS: tuple[str, ...] = (
    "recruit",
    "recruitment",
    "staffing",
    "resourcing",
    "personnel",
    "talent",
    "search partners",
    "appointments",
    "selection",
    "careers",
)


# Big UK agency brands whose names don't contain an agency word. Used to keep
# agency ads out of hot leads on sources (Adzuna) that don't flag agencies.
KNOWN_AGENCY_BRANDS: tuple[str, ...] = (
    "hays", "michael page", "page personnel", "robert half", "reed", "reed specialist recruitment", "randstad",
    "adecco", "manpower", "manpowergroup", "harvey nash", "robert walters", "goodman masson", "morgan mckinley",
    "badenoch and clark", "office angels", "brook street", "pertemps", "search consultancy", "gi group",
    "kelly services", "blue arrow", "matchtech", "gold group", "sanderson", "hunter selection", "jac recruitment",
    "huntress", "nigel frank", "jefferson frank", "hydrogen", "cpl", "rullion", "anderselite",
    "fawkes and reece", "hays construction", "sthree", "computer futures", "real staffing",
    "jonathan lee", "lorien", "akkodis", "modis", "kforce", "allegis", "aston carter",
    "acorn by synergie", "nrl", "fircroft", "ernest gordon", "ernest gordon recruitment",
)


def load_dotenv(path: Path) -> None:
    """Minimal KEY=VALUE .env reader. Does not override existing env vars."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip inline comments ("VALUE # note") and surrounding quotes.
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _words(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return tuple(w.strip().lower() for w in raw.split(",") if w.strip())


@dataclass(frozen=True)
class Settings:
    reed_api_key: str = ""
    reed_base_url: str = "https://www.reed.co.uk/api/1.0"   # overridable for local end-to-end tests
    database_url: str = ""
    companies_house_api_key: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    theirstack_api_key: str = ""
    careerjet_api_key: str = ""
    careerjet_user_ip: str = ""
    jooble_api_key: str = ""
    jooble_max_requests_per_run: int = 4      # free keys: 500 requests for life
    apify_token: str = ""
    apify_indeed_actor: str = "valig~indeed-jobs-scraper"
    apify_indeed_max_items: int = 100         # per keyword per run (double on a territory's first run)
    apify_monthly_items: int = 45000          # results a month (~$4.50: inside Apify's free $5)
    apify_max_keywords: int = 3               # Indeed searches only a territory's first 3 keywords
    brave_api_key: str = ""
    hunter_api_key: str = ""
    smartlead_api_key: str = ""
    smartlead_base_url: str = "https://server.smartlead.ai/api/v1"
    owner_test_email: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    anthropic_api_key: str = ""
    assistant_model: str = "claude-opus-5-5"
    engine_api_secret: str = ""
    enable_docs: bool = False
    owner_email: str = ""
    reed_max_calls_per_run: int = 300
    reed_requests_per_second: float = 1.0
    reed_max_tries: int = 5
    reed_backoff_base_seconds: float = 1.0
    agency_name_words: tuple[str, ...] = field(default=DEFAULT_AGENCY_NAME_WORDS)
    repost_threshold: int = 3
    lookback_days: int = 90
    adzuna_max_calls_per_run: int = 40
    theirstack_max_jobs_per_run: int = 100     # 1 credit per job
    theirstack_backfill_days: int = 90         # first run of a search pulls this much history
    known_agency_brands: tuple[str, ...] = field(default=KNOWN_AGENCY_BRANDS)

    def secret_values(self) -> list[str]:
        return [s for s in (self.reed_api_key, self.companies_house_api_key, self.adzuna_app_key, self.theirstack_api_key, self.careerjet_api_key,
                            self.jooble_api_key, self.apify_token, self.brave_api_key,
                            self.hunter_api_key, self.google_client_secret, self.smartlead_api_key, self.anthropic_api_key,
                            self.engine_api_secret, _db_password(self.database_url)) if s]


def _db_password(url: str) -> str:
    """The password part of a postgres:// URL, so logs can mask it."""
    from urllib.parse import urlsplit
    try:
        return urlsplit(url).password or ""
    except ValueError:
        return ""


def get_settings() -> Settings:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(REPO_ROOT / ".env")
    return Settings(
        reed_api_key=os.environ.get("REED_API_KEY", "").strip(),
        reed_base_url=os.environ.get("REED_BASE_URL", "").strip() or "https://www.reed.co.uk/api/1.0",
        database_url=os.environ.get("DATABASE_URL", "").strip(),
        companies_house_api_key=os.environ.get("COMPANIES_HOUSE_API_KEY", "").strip(),
        adzuna_app_id=os.environ.get("ADZUNA_APP_ID", "").strip(),
        adzuna_app_key=os.environ.get("ADZUNA_APP_KEY", "").strip(),
        theirstack_api_key=os.environ.get("THEIRSTACK_API_KEY", "").strip(),
        careerjet_api_key=os.environ.get("CAREERJET_API_KEY", "").strip(),
        careerjet_user_ip=os.environ.get("CAREERJET_USER_IP", "").strip(),
        jooble_api_key=os.environ.get("JOOBLE_API_KEY", "").strip(),
        jooble_max_requests_per_run=_int("JOOBLE_MAX_REQUESTS_PER_RUN", 4),
        apify_token=os.environ.get("APIFY_TOKEN", "").strip(),
        apify_indeed_actor=os.environ.get("APIFY_INDEED_ACTOR", "").strip() or "valig~indeed-jobs-scraper",
        apify_indeed_max_items=_int("APIFY_INDEED_MAX_ITEMS", 100),
        apify_monthly_items=_int("APIFY_MONTHLY_ITEMS", 45000),
        apify_max_keywords=_int("APIFY_MAX_KEYWORDS", 3),
        theirstack_max_jobs_per_run=_int("THEIRSTACK_MAX_JOBS_PER_RUN", 100),
        theirstack_backfill_days=_int("THEIRSTACK_BACKFILL_DAYS", 90),
        brave_api_key=os.environ.get("BRAVE_API_KEY", "").strip(),
        hunter_api_key=os.environ.get("HUNTER_API_KEY", "").strip(),
        smartlead_api_key=os.environ.get("SMARTLEAD_API_KEY", "").strip(),
        smartlead_base_url=os.environ.get("SMARTLEAD_BASE_URL", "").strip() or "https://server.smartlead.ai/api/v1",
        owner_test_email=os.environ.get("OWNER_TEST_EMAIL", "").strip(),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", "").strip(),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        assistant_model=os.environ.get("ASSISTANT_MODEL", "").strip() or "claude-opus-5-5",
        engine_api_secret=os.environ.get("ENGINE_API_SECRET", "").strip(),
        enable_docs=os.environ.get("ENABLE_DOCS", "").strip().lower() in ("1", "true", "yes"),
        owner_email=os.environ.get("OWNER_EMAIL", "").strip(),
        reed_max_calls_per_run=_int("REED_MAX_CALLS_PER_RUN", 300),
        reed_requests_per_second=_float("REED_REQUESTS_PER_SECOND", 1.0),
        reed_max_tries=_int("REED_MAX_TRIES", 5),
        reed_backoff_base_seconds=_float("REED_BACKOFF_BASE_SECONDS", 1.0),
        agency_name_words=_words("AGENCY_NAME_WORDS", DEFAULT_AGENCY_NAME_WORDS),
        repost_threshold=_int("REPOST_THRESHOLD", 3),
        lookback_days=_int("LOOKBACK_DAYS", 90),
        adzuna_max_calls_per_run=_int("ADZUNA_MAX_CALLS_PER_RUN", 40),
    )


class RedactSecrets(logging.Filter):
    """Belt and braces: replace any configured secret value in log output."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            msg = record.getMessage()
            for s in self._secrets:
                msg = msg.replace(s, "***")
            record.msg, record.args = msg, None
        return True


def setup_logging(settings: Settings, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(RedactSecrets(settings.secret_values()))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request lines at INFO; we log our own redacted lines.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
