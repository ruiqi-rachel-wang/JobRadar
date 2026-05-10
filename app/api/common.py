import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JOBRADAR_DATA_DIR", BASE_DIR / "data")).expanduser()
DEFAULT_COMPANIES_FILE = BASE_DIR / "companies.json"
COMPANIES_FILE = Path(os.environ.get("JOBRADAR_COMPANIES_FILE", DEFAULT_COMPANIES_FILE)).expanduser()
SEEN_FILE = DATA_DIR / "seen_jobs.json"
SUMMARY_FILE = DATA_DIR / "scan_summary.json"
DB_FILE = DATA_DIR / "jobs.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
COMPANIES_FILE.parent.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
