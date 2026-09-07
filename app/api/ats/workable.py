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
            loc_raw = norm(", ".join(
                x for x in [
                    loc_data.get("city") or j.get("city"),
                    loc_data.get("region") or j.get("state"),
                    loc_data.get("country") or j.get("country"),
                ] if x
            ))
        else:
            loc_raw = norm(loc_data)
        posted_raw = j.get("published") or j.get("published_on") or j.get("created_at")
        desc_parts = []
        for key, heading in (
            ("description", "Description"),
            ("requirements", "Requirements"),
            ("benefits", "Benefits"),
        ):
            if j.get(key):
                desc_parts.append(f"<h3>{heading}</h3>\n{j[key]}")
        desc = "\n\n".join(desc_parts)
        if title and jurl:
            jobs.append({
                "title": title,
                "url": jurl,
                "ats_id": str(shortcode) or None,
                "apply_url": j.get("application_url"),
                "location": loc_raw,
                "location_raw": loc_raw or None,
                "locations_raw": j.get("locations") or ([loc_raw] if loc_raw else []),
                "source": "workable",
                "posted_at": posted_raw,
                "description": desc or None,
                "description_raw": desc or None,
                "employment_type": j.get("employment_type"),
                "employment_type_raw": j.get("employment_type"),
                "department": j.get("department"),
                "remote_mode": "remote" if j.get("remote") is True else None,
                "raw_data": {
                    key: j.get(key)
                    for key in ("locations", "industry", "function")
                    if j.get(key)
                } or None,
            })
    return jobs, None
