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
            raw_location = norm(", ".join(
                x for x in [
                    loc_data.get("city"), loc_data.get("state"),
                    loc_data.get("country"),
                ] if x
            ))
        else:
            raw_location = norm(loc_data)
        loc = normalize_location(raw_location)
        jurl = f"https://{slug}.bamboohr.com/careers/{jid}" if jid else ""
        if title and jurl:
            jobs.append({
                "title": title,
                "url": jurl,
                "ats_id": str(jid),
                "location": loc,
                "location_raw": raw_location or None,
                "locations_raw": [raw_location] if raw_location else [],
                "source": "bamboohr",
                "posted": None,
                "department": j.get("departmentLabel") or j.get("department"),
                "employment_type_raw": j.get("employmentStatusLabel"),
                "raw_data": {
                    key: j.get(key)
                    for key in (
                        "location", "departmentLabel",
                        "department", "employmentStatusLabel",
                    )
                    if j.get(key) is not None
                },
            })
    return jobs, None
