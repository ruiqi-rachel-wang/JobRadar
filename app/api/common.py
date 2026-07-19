import os
import re
import threading
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


def _parse_robots_groups(text):
    """Parse robots.txt groups without urllib.robotparser's first-rule bug.

    RFC 9309 resolves overlapping Allow/Disallow rules using the most specific
    matching path.  Python's stdlib parser instead returns the first matching
    rule, which incorrectly blocks common policies such as `Disallow: /`
    followed by `Allow: /careers`.
    """
    groups = []
    agents = []
    rules = []
    saw_rule = False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if saw_rule:
                groups.append((tuple(agents), tuple(rules)))
                agents, rules, saw_rule = [], [], False
            agents.append(value.lower())
        elif key in {"allow", "disallow"} and agents:
            # An empty Disallow means no restriction and cannot match a path.
            if value or key == "allow":
                rules.append((key == "allow", value))
            saw_rule = True
    if agents:
        groups.append((tuple(agents), tuple(rules)))
    return tuple(groups)


def _robots_rule_match(pattern, target):
    if not pattern:
        return False
    end_anchored = pattern.endswith("$")
    if end_anchored:
        pattern = pattern[:-1]
    expression = "^" + re.escape(pattern).replace(r"\*", ".*")
    if end_anchored:
        expression += "$"
    return re.search(expression, target) is not None


def _robots_can_fetch(groups, user_agent, url):
    """Apply RFC 9309 user-agent and longest-path rule selection."""
    product = (user_agent or "*").split(None, 1)[0].split("/", 1)[0].lower()
    specific = []
    best_agent_length = 0
    wildcard = []
    for agents, rules in groups:
        matches = [agent for agent in agents if agent != "*" and agent in product]
        if matches:
            length = max(len(agent) for agent in matches)
            if length > best_agent_length:
                specific = [rules]
                best_agent_length = length
            elif length == best_agent_length:
                specific.append(rules)
        elif "*" in agents:
            wildcard.append(rules)
    selected = specific if specific else wildcard
    if not selected:
        return True

    parsed = urlparse(url)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    matches = []
    for rules in selected:
        for is_allow, pattern in rules:
            if _robots_rule_match(pattern, target):
                specificity = len(pattern.rstrip("$").replace("*", ""))
                matches.append((specificity, is_allow))
    if not matches:
        return True
    longest = max(specificity for specificity, _ in matches)
    # Allow wins a tie, as required by RFC 9309.
    return any(is_allow for specificity, is_allow in matches if specificity == longest)

def robots_allowed(url):
    """Return False when robots.txt disallows fetching url for our user agent."""
    if not RESPECT_ROBOTS or not url:
        return True
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return True
    root = f"{parsed.scheme}://{parsed.netloc}"
    groups = _ROBOTS_CACHE.get(root)
    if groups is None:
        robots_url = urljoin(root, "/robots.txt")
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": HEADERS["User-Agent"]})
            with urllib.request.urlopen(req, timeout=ROBOTS_TIMEOUT_SECONDS) as resp:
                text = resp.read(512_000).decode("utf-8", "ignore")
            groups = _parse_robots_groups(text)
        except Exception:
            return True
        _ROBOTS_CACHE[root] = groups
    return _robots_can_fetch(groups, HEADERS["User-Agent"], url)
