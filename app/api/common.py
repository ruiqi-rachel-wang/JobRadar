import os
import threading
import urllib.robotparser
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests as _requests
from requests.adapters import HTTPAdapter

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("JOBRADAR_DATA_DIR", BASE_DIR / "data")).expanduser()
DEFAULT_COMPANIES_FILE = BASE_DIR / "companies.json"
COMPANIES_FILE = Path(os.environ.get("JOBRADAR_COMPANIES_FILE", DEFAULT_COMPANIES_FILE)).expanduser()
SUMMARY_FILE = DATA_DIR / "scan_summary.json"
DB_FILE = DATA_DIR / "jobs.db"
AUTH_DB_FILE = Path(os.environ.get("JOBDROP_AUTH_DB_FILE", DATA_DIR / "users.db")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)
COMPANIES_FILE.parent.mkdir(parents=True, exist_ok=True)
AUTH_DB_FILE.parent.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        os.environ.get("JOBRADAR_USER_AGENT")
        or "JobDropBot/0.1 (+https://github.com/ruiqi-rachel-wang/JobRadar)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUESTS_VERIFY_SSL = os.environ.get("JOBRADAR_VERIFY_SSL", "true").lower() not in {"0", "false", "no"}

# Paginated fetchers make dozens of requests to the same host; a Session reuses
# the TCP+TLS connection (keep-alive) instead of paying a handshake per request.
# Sessions are not thread-safe, so each scan thread gets its own.
_THREAD_HTTP = threading.local()

def http_session():
    session = getattr(_THREAD_HTTP, "session", None)
    if session is None:
        session = _requests.Session()
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _THREAD_HTTP.session = session
    return session
RESPECT_ROBOTS = os.environ.get("JOBRADAR_RESPECT_ROBOTS", "true").lower() not in {"0", "false", "no"}
ROBOTS_TIMEOUT_SECONDS = float(os.environ.get("JOBRADAR_ROBOTS_TIMEOUT_SECONDS", "5"))

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
        robots_url = urljoin(root, "/robots.txt")
        rp.set_url(robots_url)
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=ROBOTS_TIMEOUT_SECONDS) as resp:
                text = resp.read(512_000).decode("utf-8", "ignore")
            rp.parse(text.splitlines())
        except Exception:
            return True
        _ROBOTS_CACHE[root] = rp
    return rp.can_fetch(HEADERS["User-Agent"], url)
