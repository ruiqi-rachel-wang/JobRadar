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
    limit        = 100
    jobs         = []

    for page in range(max_pages):
        offset = page * limit
        params = {"limit": limit, "offset": offset}
        if country_filter:
            params["country_code"] = country_filter

        try:
            r = requests.get(api_url, params=params, headers=HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            if jobs:
                break
            return [], str(e)

        raw_jobs = data.get("jobs") or []
        if not raw_jobs:
            break

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
            loc = normalize_location(norm(", ".join(x for x in [city, state, country] if x)))

            raw_date = j.get("posted_date") or j.get("create_date") or j.get("update_date") or ""
            posted = _parse_posted(raw_date[:10] if raw_date else "")

            desc_parts = [j.get("description") or j.get("description_short") or ""]
            for extra in ("basic_qualifications", "preferred_qualifications"):
                if j.get(extra):
                    desc_parts.append(str(j[extra]))
            desc = " ".join(p for p in desc_parts if p).strip()

            jid = make_id(company, jurl, title)
            if stop_on_seen and jid in seen:
                return jobs, None

            jobs.append({"title": title, "url": jurl, "location": loc, "description": desc,
                         "source": "jobsapi", "posted": posted})

        total = int(data.get("totalCount") or data.get("total") or 0)
        if total and offset + limit >= total:
            break
        if len(raw_jobs) < limit:
            break

    return jobs, None
