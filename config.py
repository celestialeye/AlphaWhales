import os
from pathlib import Path

from roster_store import load_roster


ROSTER_PATH = Path(
    os.environ.get(
        "ALPHA_WHALES_ROSTER_PATH",
        Path(__file__).with_name("roster.json"),
    )
)
FUND_MANAGERS = load_roster(ROSTER_PATH)

SEC_IDENTITY = os.environ.get(
    "EDGAR_IDENTITY",
    "Sec13F Dashboard admin@sec13f.local",
)
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
CACHE_TTL_HOURS = 6
