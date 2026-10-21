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
    jobs = {}
    for j in data.get("jobs") or []:
        title = norm(j.get("title") or "")
        shortcode = j.get("shortcode") or ""
        jurl = j.get("url") or (f"https://apply.workable.com/{slug}/j/{shortcode}/" if shortcode else "")
        locations = []
        location_keys = set()
        primary = j.get("location") or {
            "city": j.get("city"), "region": j.get("state"), "country": j.get("country"),
        }
        if isinstance(primary, dict):
            primary = {**primary,
                       "city": primary.get("city") or j.get("city"),
                       "region": primary.get("region") or j.get("state"),
                       "country": primary.get("country") or j.get("country")}
        for value in [primary, *(j.get("locations") or [])]:
            if isinstance(value, dict):
                label = norm(", ".join(str(value[key]) for key in ("city", "region", "country") if value.get(key)))
            else:
                label = norm(value)
            key = normalize_location(label).casefold() if label else ""
            if key and key not in location_keys:
                location_keys.add(key)
                locations.append(label)
        loc_raw = "; ".join(locations)
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
            job = {
                "title": title,
                "url": jurl,
                "ats_id": str(shortcode) or None,
                "apply_url": j.get("application_url"),
                "location": loc_raw,
                "location_raw": loc_raw or None,
                "locations_raw": locations,
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
            }
            # The widget may emit one row per office with the same shortcode.
            # Keep one posting and union its offices before the database upsert.
            key = str(shortcode).casefold() if shortcode else jurl
            existing = jobs.get(key)
            if existing:
                merged = existing["locations_raw"] + locations
                unique = {}
                for value in merged:
                    unique.setdefault(normalize_location(value).casefold(), value)
                existing["locations_raw"] = list(unique.values())
                existing["location_raw"] = "; ".join(unique.values()) or None
                existing["location"] = existing["location_raw"] or ""
                if not existing.get("description") and desc:
                    existing["description"] = existing["description_raw"] = desc
                raw = existing.get("raw_data") or {}
                all_raw_locations = (raw.get("locations") or []) + (j.get("locations") or [])
                if all_raw_locations:
                    raw["locations"] = list({json.dumps(value, sort_keys=True): value for value in all_raw_locations}.values())
                    existing["raw_data"] = raw
            else:
                jobs[key] = job
    return list(jobs.values()), None
