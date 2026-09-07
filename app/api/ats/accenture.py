from .base import *


def _raw_locations(item):
    """Return source locations without collapsing multi-location records."""
    candidates = (
        item.get("locations")
        or item.get("jobLocations")
        or item.get("location")
        or []
    )
    if isinstance(candidates, str):
        candidates = [candidates]
    elif isinstance(candidates, dict):
        candidates = [
            candidates.get("name")
            or candidates.get("descriptor")
            or candidates.get("location")
        ]

    output = []
    seen = set()
    for value in candidates if isinstance(candidates, list) else []:
        if isinstance(value, dict):
            value = (
                value.get("name")
                or value.get("descriptor")
                or value.get("location")
            )
        value = norm(str(value or ""))
        key = value.lower()
        if value and key not in seen:
            output.append(value)
            seen.add(key)
    return output


def _description_raw(item):
    """Keep the source sections distinct so requirements remain parseable."""
    sections = []
    for heading, keys in (
        ("Job description", ("jobDescription", "jobDescriptionClean", "jobOverview")),
        ("Qualifications", ("qualification", "qualificationClean", "jobProfile")),
    ):
        value = next((item.get(key) for key in keys if item.get(key)), None)
        if value:
            sections.append(f"## {heading}\n\n{value}")
    return "\n\n".join(sections) or None


def _label(value):
    if isinstance(value, str):
        return norm(value) or None
    if isinstance(value, dict):
        return _label(
            value.get("name")
            or value.get("label")
            or value.get("title")
            or value.get("descriptor")
        )
    if isinstance(value, list):
        labels = [_label(part) for part in value]
        labels = [part for part in labels if part]
        return "; ".join(labels) or None
    return None


def fetch_accenture(scrape, seen, company):
    """Accenture's first-party careers search API.

    Accenture's Workday board reports a global count far above Workday's
    2,000-result retrieval ceiling. The company's own careers search exposes
    the complete country catalog and is the source used by its public UI.
    """
    api_url = (
        scrape.get("api_url")
        or "https://www.accenture.com/api/accenture/elastic/findjobs"
    )
    country = scrape.get("country") or "USA"
    language = scrape.get("language") or "en"
    country_site = scrape.get("country_site") or "us-en"
    page_size = max(1, min(100, int(scrape.get("page_size") or 100)))
    max_pages = max(1, int(scrape.get("hard_max_pages") or 100))
    jobs = []
    total = None

    for page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
        start = page * page_size
        form = {
            "startIndex": start,
            "maxResultSize": page_size,
            "jobKeyword": "",
            "jobCountry": country,
            "jobLanguage": language,
            "countrySite": country_site,
            "sortBy": 2,
            "searchType": "vectorSearch",
            "enableQueryBoost": "true",
            "minScore": scrape.get("min_score") or "0.6",
            "getFeedbackJudgmentEnabled": "true",
            "useCleanEmbedding": "true",
            "score": "true",
            "totalHits": "true",
            "debugQuery": "false",
            "jobFilters": "[]",
        }
        try:
            response = requests.post(
                api_url,
                data=form,
                headers={
                    **HEADERS,
                    "Accept": "application/json",
                    "Referer": (
                        f"https://www.accenture.com/"
                        f"{country_site}/careers/jobsearch"
                    ),
                },
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Accenture returned a non-object response")
        except Exception as exc:
            if jobs:
                return jobs, f"Accenture page at startIndex {start} failed: {exc}"
            return [], str(exc)

        raw_jobs = payload.get("data") or []
        total_info = payload.get("totalHits") or {}
        if total is None:
            try:
                total = int(
                    total_info.get("total")
                    if isinstance(total_info, dict)
                    else total_info
                )
            except (TypeError, ValueError):
                total = 0
        if not raw_jobs:
            break

        for item in raw_jobs:
            title = norm(item.get("title") or "")
            ats_id = str(
                item.get("guid")
                or item.get("requisitionId")
                or item.get("jobId")
                or ""
            ).strip()
            url = norm(item.get("jobDetailUrl") or "")
            if "{0}" in url:
                url = url.replace("{0}", country_site)
            if not title or not ats_id or not url:
                continue

            locations_raw = _raw_locations(item)
            description_raw = _description_raw(item)
            posted_raw = (
                item.get("postedDate")
                or item.get("postingDate")
                or item.get("postedDateText")
                or None
            )
            posted_at = (
                posted_raw
                if posted_raw and re.match(r"^\d{4}-\d{2}-\d{2}", str(posted_raw))
                else None
            )
            normalized_locations = [
                normalized
                for value in locations_raw
                if (normalized := normalize_location(value))
            ]
            jobs.append({
                "title": title,
                "url": url,
                "apply_url": item.get("applyUrl") or url,
                "ats_id": ats_id,
                "requisition_id": str(
                    item.get("requisitionId") or ats_id
                ),
                "location": "; ".join(normalized_locations) or None,
                "location_raw": "; ".join(locations_raw) or None,
                "locations_raw": locations_raw,
                "country_iso": "US" if country.upper() in {"USA", "US"} else None,
                "workplace_type": item.get("remoteType") or None,
                "description": description_raw,
                "description_raw": description_raw,
                "source": "accenture",
                "posted": _parse_posted(posted_at or posted_raw) or None,
                "posted_at": posted_at,
                "posted_raw": posted_raw,
                "employment_type_raw": (
                    item.get("jobScheduleDescription")
                    or item.get("employeeType")
                    or None
                ),
                "department": _label(
                    item.get("jobFamilyGroup")
                    or item.get("businessArea")
                ),
                "team": _label(item.get("areaOfInterestTitle")),
                "raw_data": {
                    key: item.get(key)
                    for key in (
                        "country", "updateDate", "jobTypeDescription",
                        "yearsOfExperience", "remoteType", "skill",
                        "mustHaveSkills", "goodToHaveSkills",
                    )
                    if item.get(key) is not None
                },
            })

        if total and start + len(raw_jobs) >= total:
            break
        if len(raw_jobs) < page_size:
            break

    if total and len(jobs) < total:
        return jobs, (
            f"Accenture partial listing: parsed {len(jobs)} "
            f"of {total} {country} jobs"
        )
    return jobs, None
