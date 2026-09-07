from .base import *


DEFAULT_API_URL = "https://prod-search-api.jobsyn.org/api/v1/solr/search"


def _job_url(site_url, job):
    """Rebuild the canonical DirectEmployers detail URL from Solr fields."""
    location_path = str(job.get("city_slab_exact") or "").split("::", 1)[0]
    parts = [part for part in location_path.split("/") if part]
    if len(parts) >= 3:
        location_slug = f"{parts[0]}-{parts[2]}"
    else:
        location_slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            norm(job.get("location_exact") or "").lower(),
        ).strip("-")
    title_slug = str(job.get("title_slug") or "").strip("/")
    guid = str(job.get("guid") or "").strip()
    if not location_slug or not title_slug or not guid:
        return ""
    parsed_site = urlparse(site_url)
    site_root = f"{parsed_site.scheme}://{parsed_site.netloc}/"
    return urljoin(
        site_root,
        f"{location_slug}/{title_slug}/{guid}/job/",
    )


def fetch_jobsyn(scrape, seen, company):
    """Fetch a DirectEmployers/JobSync public Solr-backed company board."""
    site_url = (scrape.get("list_url") or "").strip()
    origin = (scrape.get("origin") or urlparse(site_url).netloc).strip()
    api_url = (scrape.get("api_url") or DEFAULT_API_URL).strip()
    if not site_url:
        return [], "no list_url"
    if not origin:
        return [], "no origin"

    hard_max_pages = int(scrape.get("hard_max_pages") or 500)
    jobs = []
    expected_total = None
    for page in range(1, hard_max_pages + 1):
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
        try:
            response = requests.get(
                api_url,
                params={"page": page},
                headers={
                    **HEADERS,
                    "Accept": "application/json",
                    "x-origin": origin,
                },
                timeout=25,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return jobs, str(exc)

        pagination = data.get("pagination") or {}
        if expected_total is None:
            expected_total = int(pagination.get("total") or 0)
        source_jobs = data.get("jobs") or []
        if not source_jobs:
            break

        for item in source_jobs:
            title = norm(item.get("title_exact") or "")
            job_url = _job_url(site_url, item)
            ats_id = str(item.get("guid") or item.get("reqid") or "").strip()
            if not title or not job_url or not ats_id:
                continue
            location_raw = norm(item.get("location_exact") or "")
            raw_date = item.get("date_new") or item.get("date_added") or ""
            description = item.get("description") or ""
            jobs.append({
                "title": title,
                "url": job_url,
                "ats_id": ats_id,
                "requisition_id": str(item.get("reqid") or "").strip() or None,
                "location": normalize_location(location_raw),
                "location_raw": location_raw or None,
                "locations_raw": [location_raw] if location_raw else [],
                "description": description or None,
                "description_raw": description or None,
                "source": "jobsyn",
                "posted": _parse_posted(raw_date[:10]) or None,
                "posted_at": raw_date or None,
                "posted_raw": raw_date or None,
                "employment_type_raw": item.get("job_type"),
                "department": item.get("job_category"),
                "raw_data": {
                    key: item.get(key)
                    for key in (
                        "company_exact", "country_exact", "job_category",
                        "job_shift", "job_type", "reqid",
                    )
                    if item.get(key) is not None
                },
            })

        if not pagination.get("has_more_pages"):
            break

    if expected_total and len(jobs) != expected_total:
        return jobs, (
            f"{company} partial listing: fetched {len(jobs)} of "
            f"{expected_total} source records"
        )
    return jobs, None
