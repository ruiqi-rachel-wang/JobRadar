from .base import *

# ── Google ────────────────────────────────────────────────────────────────────

def fetch_google(scrape, seen, company):
    """GET careers.google.com/api/v3/search/"""
    stop_on_seen = bool(scrape.get("stop_on_seen", True))
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    jobs, page, ps = [], 1, 20
    for _ in range(max_pages):
        try:
            r = requests.get(
                "https://careers.google.com/api/v3/search/",
                params={"location":"United States","page":page,"pageSize":ps},
                headers={**HEADERS,"Referer":"https://careers.google.com/"},
                timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)
        results = data.get("jobs",[])
        if not results: break
        stop = False
        for j in results:
            title = norm(j.get("title") or "")
            jid_s = j.get("id") or ""
            jurl  = f"https://careers.google.com/jobs/results/{jid_s}/" if jid_s else ""
            loc   = "; ".join(filter(None, [normalize_location(norm(l)) for l in (j.get("locations") or [])]))
            posted = _parse_posted(j.get("publishDate") or j.get("publishedDate") or j.get("datePosted") or j.get("created") or "")
            if title and jurl:
                jid = make_id(company, jurl, title)
                if stop_on_seen and jid in seen: stop=True; break
                jobs.append({"title":title,"url":jurl,"location":loc,"source":"google","posted":posted})
        if stop: break
        if page >= data.get("pageCount",1): break
        page += 1
        time.sleep(0.3)
    return jobs, None

