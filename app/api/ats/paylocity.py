from .base import *


def _page_data(html):
    marker = "window.pageData"
    start = html.find(marker)
    if start < 0:
        return {}
    start = html.find("=", start + len(marker))
    if start < 0:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(html[start + 1:].lstrip())
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError):
        return {}


def _jsonld_job(html):
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", {"type": re.compile(r"ld\+json", re.I)}):
        try:
            data = json.loads(script.get_text(strip=True))
        except (TypeError, ValueError):
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                return candidate
    return {}


def _location(job):
    if job.get("IsRemote"):
        return "Remote"
    location = job.get("JobLocation") or {}
    parts = [
        location.get("City"),
        location.get("State"),
        location.get("Country"),
    ]
    return normalize_location(", ".join(str(part) for part in parts if part))


def _raw_location(job):
    if job.get("IsRemote"):
        return "Remote"
    location = job.get("JobLocation") or {}
    return ", ".join(
        str(location.get(key))
        for key in ("City", "State", "Country")
        if isinstance(location, dict) and location.get(key)
    )


def _jsonld_location(value):
    values = value if isinstance(value, list) else [value]
    locations = []
    for item in values:
        if not isinstance(item, dict):
            continue
        address = item.get("address") or {}
        if not isinstance(address, dict):
            continue
        raw = ", ".join(
            str(address.get(key))
            for key in ("addressLocality", "addressRegion", "addressCountry")
            if address.get(key)
        )
        location = normalize_location(raw)
        if location and location.lower() not in {existing.lower() for existing in locations}:
            locations.append(location)
    return "; ".join(locations)


def _jsonld_raw_locations(value):
    values = value if isinstance(value, list) else [value]
    locations = []
    for item in values:
        if not isinstance(item, dict):
            continue
        address = item.get("address") or {}
        if not isinstance(address, dict):
            continue
        raw = ", ".join(
            str(address.get(key))
            for key in ("addressLocality", "addressRegion", "addressCountry")
            if address.get(key)
        )
        if raw and raw not in locations:
            locations.append(raw)
    return locations


def fetch_paylocity(scrape, seen, company):
    """Fetch a Paylocity public board and enrich new jobs from JobPosting JSON-LD."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"

    verify_ssl = bool(scrape.get("verify_ssl", True))
    headers = {**HEADERS, "Accept": "text/html,application/xhtml+xml"}
    try:
        response = requests.get(list_url, headers=headers, timeout=25, verify=verify_ssl)
        response.raise_for_status()
    except Exception as exc:
        return [], str(exc)

    raw_jobs = _page_data(response.text).get("Jobs") or []
    jobs = []
    for raw in raw_jobs:
        job_id = raw.get("JobId")
        title = norm(raw.get("JobTitle") or "")
        if not job_id or not title:
            continue
        url = urljoin(response.url, f"/Recruiting/Jobs/Details/{job_id}")
        raw_location = _raw_location(raw)
        published = raw.get("PublishedDate")
        jobs.append({
            "title": title,
            "url": url,
            "ats_id": str(job_id),
            "requisition_id": raw.get("JobCode") or raw.get("RequisitionNumber"),
            "location": _location(raw),
            "location_raw": raw_location,
            "locations_raw": [raw_location] if raw_location else [],
            "remote_mode": "remote" if raw.get("IsRemote") else None,
            "source": "paylocity",
            "posted": _parse_posted(published or ""),
            "posted_at": published,
            "posted_raw": published,
            "employment_type_raw": raw.get("JobType") or raw.get("EmploymentType"),
            "department": raw.get("DepartmentName"),
            "raw_data": {
                key: raw.get(key)
                for key in (
                    "JobLocation", "IsRemote", "JobType",
                    "EmploymentType", "DepartmentName",
                )
                if raw.get(key) is not None
            },
        })

    fetch_details = bool(scrape.get("fetch_experience_details") or scrape.get("fetch_details"))
    if not fetch_details or not jobs:
        return jobs, None

    refresh = bool(scrape.get("refresh_experience"))
    max_details = int(scrape.get("max_desc_details", 100))
    targets = []
    for job in jobs:
        jid = make_id(
            company, job["url"], job["title"], "paylocity", job.get("ats_id", "")
        )
        existing = (
            seen.get(jid) or seen.get(make_id(company, job["url"], job["title"]))
            if isinstance(seen, dict) else None
        )
        if refresh or not (isinstance(existing, dict) and existing.get("has_description")):
            targets.append(job)

    def fetch_detail(job):
        try:
            detail = requests.get(job["url"], headers=headers, timeout=20, verify=verify_ssl)
            detail.raise_for_status()
            posting = _jsonld_job(detail.text)
            description_raw = posting.get("description") or ""
            raw_locations = _jsonld_raw_locations(posting.get("jobLocation"))
            return job, {
                "description": normalize_description(description_raw),
                "description_raw": description_raw,
                "location": _jsonld_location(posting.get("jobLocation")),
                "locations_raw": raw_locations,
                "posted": _parse_posted(posting.get("datePosted") or ""),
                "posted_at": posting.get("datePosted"),
                "posted_raw": posting.get("datePosted"),
                "employment_type_raw": posting.get("employmentType"),
            }
        except Exception:
            return job, {}

    if targets[:max_details]:
        workers = min(max(1, int(scrape.get("detail_workers", 4))), len(targets))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for job, detail in executor.map(fetch_detail, targets[:max_details]):
                if detail.get("description"):
                    job["description"] = detail["description"]
                    job["description_raw"] = detail.get("description_raw")
                if detail.get("location"):
                    job["location"] = detail["location"]
                if detail.get("locations_raw"):
                    job["locations_raw"] = detail["locations_raw"]
                    job["location_raw"] = detail["locations_raw"][0]
                if detail.get("posted"):
                    job["posted"] = detail["posted"]
                if detail.get("posted_at"):
                    job["posted_at"] = detail["posted_at"]
                    job["posted_raw"] = detail.get("posted_raw")
                if detail.get("employment_type_raw"):
                    job["employment_type_raw"] = detail["employment_type_raw"]

    return jobs, None
