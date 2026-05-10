from .base import *

# ── Workable ──────────────────────────────────────────────────────────────────

def fetch_workable(scrape, seen, company):
    slug = scrape.get("workable_slug") or scrape.get("slug") or ""
    if not slug:
        return [], "no workable_slug"
    try:
        r = requests.get(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
            params={"details": "true"},
            headers={**HEADERS, "Accept": "application/json"},
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)
    jobs = []
    for j in data.get("jobs") or []:
        title = norm(j.get("title") or "")
        shortcode = j.get("shortcode") or ""
        jurl = j.get("url") or (f"https://apply.workable.com/{slug}/j/{shortcode}/" if shortcode else "")
        loc_data = j.get("location") or {}
        if not loc_data and j.get("locations"):
            loc_data = (j.get("locations") or [{}])[0]
        if isinstance(loc_data, dict):
            loc = normalize_location(norm(", ".join(
                x for x in [
                    loc_data.get("city") or j.get("city"),
                    loc_data.get("region") or j.get("state"),
                    loc_data.get("country") or j.get("country"),
                ] if x
            )))
        else:
            loc = normalize_location(norm(loc_data))
        posted = _parse_posted(j.get("published") or j.get("published_on") or j.get("created_at") or "")
        if title and jurl:
            jobs.append({"title": title, "url": jurl, "location": loc, "source": "workable", "posted": posted})
    return jobs, None


