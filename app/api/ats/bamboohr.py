from .base import *

# ── BambooHR ──────────────────────────────────────────────────────────────────

def fetch_bamboohr(scrape, seen, company):
    """BambooHR careers JSON endpoint: https://{slug}.bamboohr.com/careers/list"""
    slug = scrape.get("bamboohr_slug","")
    if not slug:
        return [], "no bamboohr_slug"
    url = f"https://{slug}.bamboohr.com/careers/list"
    try:
        r = requests.get(
            url,
            headers={**HEADERS, "Accept":"application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)

    postings = data.get("result", []) if isinstance(data, dict) else []
    if not isinstance(postings, list):
        return [], "unexpected response"

    jobs = []
    for j in postings:
        if not isinstance(j, dict):
            continue
        title = norm(j.get("jobOpeningName") or j.get("title") or "")
        jid   = j.get("id") or ""
        loc_data = j.get("location") or {}
        if isinstance(loc_data, dict):
            loc = normalize_location(norm(", ".join(
                x for x in [loc_data.get("city"), loc_data.get("state")] if x
            )))
        else:
            loc = normalize_location(norm(loc_data))
        jurl = f"https://{slug}.bamboohr.com/careers/view/{jid}" if jid else ""
        if title and jurl:
            jobs.append({"title":title,"url":jurl,"location":loc,"source":"bamboohr","posted":""})
    return jobs, None

