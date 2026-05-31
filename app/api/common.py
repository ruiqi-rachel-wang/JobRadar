import os
import urllib.robotparser
from pathlib import Path
from urllib.parse import urljoin, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JOBRADAR_DATA_DIR", BASE_DIR / "data")).expanduser()
DEFAULT_COMPANIES_FILE = BASE_DIR / "companies.json"
COMPANIES_FILE = Path(os.environ.get("JOBRADAR_COMPANIES_FILE", DEFAULT_COMPANIES_FILE)).expanduser()
SUMMARY_FILE = DATA_DIR / "scan_summary.json"
DB_FILE = DATA_DIR / "jobs.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
COMPANIES_FILE.parent.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        os.environ.get("JOBRADAR_USER_AGENT")
        or "JobDropBot/0.1 (+https://github.com/ruiqi-rachel-wang/JobRadar)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUESTS_VERIFY_SSL = os.environ.get("JOBRADAR_VERIFY_SSL", "true").lower() not in {"0", "false", "no"}
RESPECT_ROBOTS = os.environ.get("JOBRADAR_RESPECT_ROBOTS", "true").lower() not in {"0", "false", "no"}

_ROBOTS_CACHE = {}

def robots_allowed(url):
    """Return False when robots.txt disallows fetching url for our user agent."""
    if not RESPECT_ROBOTS or not url:
        return True
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return True
    root = f"{parsed.scheme}://{parsed.netloc}"
    rp = _ROBOTS_CACHE.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(urljoin(root, "/robots.txt"))
        try:
            rp.read()
        except Exception:
            return True
        _ROBOTS_CACHE[root] = rp
    return rp.can_fetch(HEADERS["User-Agent"], url)
