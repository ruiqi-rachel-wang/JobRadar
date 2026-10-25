from .base import *

# ── Generic iCIMS-backed branded career site JSON API ─────────────────────────
#
# Pattern used by AMD, Costco, Rivian, and others that host a branded career
# site (often on a custom domain) in front of iCIMS, with a public JSON API at
# /api/jobs.  The API returns all jobs in pages of up to 100 with structured
# location and date fields — much faster than Playwright scraping.
#
# Required config:
#   api_url          — e.g. "https://careers.amd.com/api/jobs"
#   job_url_prefix   — e.g. "https://careers.amd.com/careers-home/jobs/"
# Optional:
#   country_code     — filter to a specific country (e.g. "US"). Default: all.
#   stop_on_seen     — stop when an already-seen job is reached. Default: false.

def fetch_jobsapi(scrape, seen, company):
    api_url        = scrape.get("api_url", "")
    job_prefix     = (scrape.get("job_url_prefix") or "").rstrip("/") + "/"
    country_filter = (scrape.get("country_code") or "").upper()

    if not api_url:
        return [], "no api_url"
    if not job_prefix or job_prefix == "/":
        return [], "no job_url_prefix"

    # Older configs used "sort" for incremental scans. Keep full scans as the
    # default here; these APIs are stable and the app handles dedupe on save.
    stop_on_seen = bool(scrape.get("stop_on_seen"))
    max_pages    = int(scrape.get("hard_max_pages") or 10000)
    page_param   = (scrape.get("page_param") or "").strip()
    page_start   = int(scrape.get("page_start") or 1)
    limit        = 100
    jobs         = []
    seen_page_signatures = set()
    expected_total = 0

    for page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
        offset = page * limit
        params = {"limit": limit}
        if page_param:
            params[page_param] = page_start + page
        else:
            params["offset"] = offset
        if country_filter:
            params["country_code"] = country_filter

        try:
            r = requests.get(api_url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)

        expected_total = int(data.get("totalCount") or data.get("total") or 0)

        raw_jobs = data.get("jobs") or []
        if not raw_jobs:
            break

        # Some branded APIs accept `limit` but silently ignore `offset`.  Stop
        # rather than processing the first page thousands of times.  Sources
        # that paginate by page number declare page_param in their config.
        signature = tuple(
            str((item.get("data", item)).get("req_id")
                or (item.get("data", item)).get("slug")
                or (item.get("data", item)).get("id_icims")
                or (item.get("data", item)).get("id") or "")
            for item in raw_jobs[:10]
        )
        if signature and signature in seen_page_signatures:
            return jobs, "jobs API repeated a page (partial results)"
        if signature:
            seen_page_signatures.add(signature)

        for item in raw_jobs:
            j = item.get("data", item)

            title = norm(j.get("title") or "")
            req_id = str(j.get("req_id") or j.get("slug") or j.get("id_icims") or j.get("id") or "").strip()
            if not title or not req_id:
                continue

            jurl = f"{job_prefix}{req_id}"

            city    = j.get("city") or ""
            state   = j.get("state") or ""
            country = j.get("country") or j.get("country_code") or ""
            location_raw = norm(j.get("full_location") or
                                ", ".join(x for x in [city, state, country] if x))
            locations = [part.strip() for part in location_raw.split(";") if part.strip()]
            for additional in j.get("additional_locations") or []:
                if not isinstance(additional, dict):
                    continue
                value = norm(", ".join(str(additional.get(key)) for key in
                                     ("city", "state", "country") if additional.get(key)))
                if value and value not in locations:
                    locations.append(value)
            loc = normalize_location("; ".join(locations))

            raw_date = j.get("posted_date") or j.get("create_date") or j.get("update_date") or ""
            posted = _parse_posted(raw_date[:10] if raw_date else "")

            desc_parts = []
            description = j.get("description") or j.get("description_short") or ""
            if description:
                desc_parts.append(str(description))
            for extra, heading in (
                ("basic_qualifications", "Basic qualifications"),
                ("preferred_qualifications", "Preferred qualifications"),
            ):
                if j.get(extra):
                    desc_parts.append(f"<h3>{heading}</h3>\n{j[extra]}")
            desc = "\n\n".join(desc_parts).strip()

            jid = make_id(company, jurl, title, "jobsapi", req_id)
            if stop_on_seen and (
                jid in seen or make_id(company, jurl, title) in seen
            ):
                return jobs, None

            jobs.append({
                "title": title,
                "url": jurl,
                "ats_id": req_id,
                "requisition_id": req_id,
                "location": loc,
                "location_raw": location_raw or None,
                "locations_raw": locations,
                "description": desc or None,
                "description_raw": desc or None,
                "source": "jobsapi",
                "posted": posted or None,
                "posted_at": raw_date or None,
                "posted_raw": raw_date or None,
                "employment_type_raw": j.get("employment_type")
                    or j.get("job_type"),
                "department": j.get("department"),
                "raw_data": {
                    key: j.get(key)
                    for key in (
                        "city", "state", "country", "country_code", "full_location",
                        "additional_locations", "multipleLocations",
                        "employment_type", "job_type", "department",
                    )
                    if j.get(key) is not None
                },
            })

        total = int(data.get("totalCount") or data.get("total") or 0)
        if total and offset + limit >= total:
            break
        if len(raw_jobs) < limit:
            break

    if expected_total and len(jobs) < expected_total:
        return jobs, f"partial jobs API listing: fetched {len(jobs)} of {expected_total} jobs"
    return jobs, None
