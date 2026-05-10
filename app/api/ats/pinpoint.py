from .base import *

# ── Pinpoint ─────────────────────────────────────────────────────────────────

def fetch_pinpoint(scrape, seen, company):
    """Pinpoint careers JSON endpoint, e.g. https://careers.example.com/postings.json"""
    list_url = scrape.get("list_url","")
    if not list_url:
        return [], "no list_url"
    try:
        r = requests.get(
            list_url,
            headers={**HEADERS, "Accept":"application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)

    postings = data.get("data") if isinstance(data, dict) else data
    if not isinstance(postings, list):
        return [], "unexpected response"

    host = scrape.get("public_host","").rstrip("/")
    jobs = []
    for j in postings:
        if not isinstance(j, dict):
            continue
        title = norm(j.get("title") or "")
        path  = j.get("path") or ""
        jurl  = j.get("url") or ""
        if host and path:
            jurl = host + path
        loc_data = j.get("location") or {}
        loc = normalize_location(norm(loc_data.get("name") if isinstance(loc_data, dict) else loc_data))
        posted = _parse_posted(j.get("posted_at") or j.get("created_at") or j.get("published_at") or "")
        if title and jurl:
            jobs.append({"title":title,"url":jurl,"location":loc,"source":"pinpoint","posted":posted})
    return jobs, None


