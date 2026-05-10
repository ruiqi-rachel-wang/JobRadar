from .base import *

# ── SmartRecruiters ───────────────────────────────────────────────────────────

def fetch_smartrecruiters(scrape, seen, company):
    slug = scrape.get("smartrecruiters_company") or scrape.get("company_slug") or scrape.get("slug") or ""
    if not slug:
        return [], "no smartrecruiters_company"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    limit = int(scrape.get("limit", 100))
    jobs = []
    for page in range(max_pages):
        offset = page * limit
        try:
            r = requests.get(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                params={"limit": limit, "offset": offset},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)
        postings = data.get("content") or []
        if not postings:
            break
        for j in postings:
            title = norm(j.get("name") or "")
            jurl = (j.get("ref") or "").strip()
            if not jurl and j.get("id"):
                jurl = f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}"
            loc_data = j.get("location") or {}
            loc = ""
            if isinstance(loc_data, dict):
                loc = normalize_location(norm(", ".join(
                    x for x in [loc_data.get("city"), loc_data.get("region"), loc_data.get("country")] if x
                )))
            posted = _parse_posted(j.get("releasedDate") or j.get("createdOn") or "")
            if title and jurl:
                jobs.append({"title": title, "url": jurl, "location": loc, "source": "smartrecruiters", "posted": posted})
        total = int(data.get("totalFound") or 0)
        if total and offset + limit >= total:
            break
    return jobs, None

