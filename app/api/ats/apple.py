from .base import *

# ── Apple ─────────────────────────────────────────────────────────────────────

def _apple_csrf_session():
    """Fetch CSRF token + session cookies from Apple Jobs using a persistent session."""
    sess = requests.Session()
    try:
        r = sess.get(
            "https://jobs.apple.com/api/v1/CSRFToken",
            headers={**HEADERS, "Referer": "https://jobs.apple.com/en-us/search"},
            timeout=15,
        )
        csrf = r.headers.get("X-Apple-CSRF-Token", "")
        return csrf, sess
    except Exception:
        return "", None

def _apple_loc(loc_obj):
    """Return normalized location string from an Apple location object, or '' if it's employment-type noise."""
    city     = (loc_obj.get("city") or "").strip()
    state    = (loc_obj.get("stateProvince") or "").strip()
    country  = (loc_obj.get("countryName") or "").strip()
    name     = (loc_obj.get("name") or "").strip()
    # Some Apple Retail entries encode employment type in the name rather than a place
    if re.search(r'\bfull.?time\b|\bpart.?time\b|\btemporary\b', name, re.I):
        return ""
    if city:
        parts = [city, state, country]
    elif name and name not in ("United States",):
        parts = [name, country]
    else:
        parts = [country]
    candidate = ", ".join(x for x in parts if x)
    return normalize_location(norm(candidate))

def fetch_apple(scrape, seen, company):
    """POST https://jobs.apple.com/api/v1/search (Apple Jobs v1 API with CSRF session)."""
    stop_on_seen = bool(scrape.get("stop_on_seen", True))
    max_pages    = int(scrape.get("hard_max_pages") or 10000)

    csrf, sess = _apple_csrf_session()
    if sess is None:
        sess = requests.Session()
    req_headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://jobs.apple.com/en-us/search",
    }
    if csrf:
        req_headers["X-Apple-CSRF-Token"] = csrf

    jobs, page = [], 1
    page_size  = 20  # Apple returns 20 per page by default
    for _ in range(max_pages):
        payload = {
            "query":   "",
            "filters": {"locations": ["postLocation-USA"]},
            "page":    page,
            "locale":  "en-us",
            "sort":    "newest",
            "format":  {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
        }
        try:
            r = sess.post(
                "https://jobs.apple.com/api/v1/search",
                json=payload,
                headers=req_headers,
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)

        res     = data.get("res", data)
        results = res.get("searchResults", [])
        if not results:
            break

        stop = False
        for j in results:
            title = norm(j.get("postingTitle") or "")
            pid   = j.get("positionId") or j.get("jobPositionId") or ""
            slug  = j.get("transformedPostingTitle") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            team  = j.get("team") or {}
            tc    = (team.get("teamCode") or "") if isinstance(team, dict) else ""
            tp    = f"?team={tc}" if tc else ""
            jurl  = f"https://jobs.apple.com/en-us/details/{pid}/{slug}{tp}" if pid else ""
            locs  = j.get("locations") or []
            loc   = "; ".join(filter(None, [_apple_loc(l) for l in locs]))
            posted = _parse_posted(j.get("postDateInGMT") or j.get("postingDate") or "")
            if title and pid:
                jid = make_id(company, jurl, title)
                if stop_on_seen and jid in seen:
                    stop = True
                    break
                jobs.append({"title": title, "url": jurl, "location": loc, "source": "apple", "posted": posted})

        if stop:
            break
        total = res.get("totalRecords", 0)
        if total and page * page_size >= total:
            break
        page += 1
        time.sleep(0.3)

    return jobs, None
