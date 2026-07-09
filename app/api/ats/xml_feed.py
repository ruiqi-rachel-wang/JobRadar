from .base import *

# ── Generic Indeed-style XML job feed ──────────────────────────────────────────
# Many career sites publish a full job feed (<source><job>...</job></source>)
# alongside their paginated HTML listing page. The feed is a single request and
# returns every open job, so it's preferred over scraping HTML pages one at a
# time when a company exposes one (e.g. Adidas' HTML page shows only the first
# 20 jobs, but https://careers.adidas-group.com/jobs/feed.xml lists all of them).

_FEED_DATE_FORMATS = ("%a, %b %d, %Y %I:%M %p", "%a, %d %b %Y %H:%M:%S %z")


def _parse_feed_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return ""
    for fmt in _FEED_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def fetch_xml_feed(scrape, seen, company):
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    include = scrape.get("include", "")
    headers = dict(HEADERS)
    if scrape.get("user_agent"):
        headers["User-Agent"] = scrape["user_agent"]
    try:
        r = requests.get(list_url, headers=headers, timeout=60,
                          verify=bool(scrape.get("verify_ssl", True)))
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        return [], str(e)

    jobs = {}
    for node in root.iter("job"):
        def field(tag):
            child = node.find(tag)
            return (child.text or "").strip() if child is not None and child.text else ""

        url, title = field("url"), field("title")
        if not url or not title:
            continue
        if include and not has_include(url, include):
            continue
        location = ", ".join(p for p in (field("city"), field("state"), field("country")) if p)
        jobs[url] = {
            "title":       title,
            "url":         url,
            "location":    location,
            "source":      "xml_feed",
            "posted":      _parse_feed_date(field("date")),
            "description": field("description"),
        }
    return list(jobs.values()), None
