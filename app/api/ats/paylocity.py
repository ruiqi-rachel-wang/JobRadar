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
        jobs.append({
            "title": title,
            "url": url,
            "location": _location(raw),
            "source": "paylocity",
            "posted": _parse_posted(raw.get("PublishedDate") or ""),
        })

    fetch_details = bool(scrape.get("fetch_experience_details") or scrape.get("fetch_details"))
    if not fetch_details or not jobs:
        return jobs, None

    refresh = bool(scrape.get("refresh_experience"))
    max_details = int(scrape.get("max_desc_details", 100))
    targets = []
    for job in jobs:
        jid = make_id(company, job["url"], job["title"])
        existing = seen.get(jid) if isinstance(seen, dict) else None
        if refresh or not (isinstance(existing, dict) and existing.get("has_description")):
            targets.append(job)

    def fetch_detail(job):
        try:
            detail = requests.get(job["url"], headers=headers, timeout=20, verify=verify_ssl)
            detail.raise_for_status()
            posting = _jsonld_job(detail.text)
            description = norm(
                BeautifulSoup(posting.get("description") or "", "lxml").get_text(" ", strip=True)
            )
            return job, {
                "description": description,
                "location": _jsonld_location(posting.get("jobLocation")),
                "posted": _parse_posted(posting.get("datePosted") or ""),
            }
        except Exception:
            return job, {}

    if targets[:max_details]:
        workers = min(max(1, int(scrape.get("detail_workers", 4))), len(targets))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for job, detail in executor.map(fetch_detail, targets[:max_details]):
                if detail.get("description"):
                    job["description"] = detail["description"]
                if detail.get("location"):
                    job["location"] = detail["location"]
                if detail.get("posted"):
                    job["posted"] = detail["posted"]

    return jobs, None
