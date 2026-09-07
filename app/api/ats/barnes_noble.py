from .base import *


def _barnes_description(job):
    sections = []
    for heading, key in (
        ("Job summary", "description"),
        ("What you do", "essentialFunctions"),
        ("Qualifications", "qualifications"),
        ("Requirements", "reqNotes"),
    ):
        value = job.get(key)
        if value:
            sections.append(f"<h2>{heading}</h2>\n{value}")
    return "\n\n".join(sections) or None


def fetch_barnes_noble(scrape, seen, company):
    """Barnes & Noble's first-party careers API.

    The public site exposes complete JSON feeds by job family. The corporate
    feed contains postings such as 92614BR; retail and distribution are read
    too so the company entry is complete rather than limited to one category.
    """
    api_base = (
        scrape.get("api_base")
        or "https://careers.barnesandnoble.com/careers-api/v1/jobs"
    ).rstrip("/")
    detail_base = (
        scrape.get("detail_base")
        or "https://careers.barnesandnoble.com/job"
    ).rstrip("/")
    sections = scrape.get("sections") or ["corporate", "retail", "distribution"]
    jobs_by_id = {}
    errors = []

    for section in sections:
        if scan_deadline_exceeded(scrape):
            return list(jobs_by_id.values()), "company scan deadline reached (partial results)"
        try:
            response = requests.get(
                f"{api_base}/{section}",
                headers={**HEADERS, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload.get("content") or []
            expected = int(payload.get("totalElements") or 0)
            if expected and len(content) < expected:
                errors.append(
                    f"{section} returned {len(content)} of {expected} jobs"
                )
        except Exception as exc:
            errors.append(f"{section}: {exc}")
            continue

        for item in content:
            if not isinstance(item, dict):
                continue
            ats_id = str(item.get("id") or "").strip()
            title = norm(item.get("title") or "")
            if not ats_id or not title:
                continue
            raw_location = norm(item.get("location") or "")
            locations_raw = [raw_location] if raw_location else []
            location = normalize_location(raw_location)
            url = f"{detail_base}/{ats_id}"
            description = _barnes_description(item)
            jobs_by_id[ats_id] = {
                "title": title,
                "url": url,
                "apply_url": item.get("jobDetailLink") or url,
                "ats_id": ats_id,
                "requisition_id": ats_id,
                "location": location or None,
                "location_raw": raw_location or None,
                "locations_raw": locations_raw,
                "description": description,
                "description_raw": description,
                "source": "barnes_noble",
                "posted_at": item.get("publishedDate") or None,
                "posted_raw": item.get("publishedDate") or None,
                "employment_type_raw": item.get("employmentType") or None,
                "department": item.get("category") or None,
                "raw_data": {
                    key: item.get(key)
                    for key in (
                        "location", "city", "state", "zip", "category",
                        "locationType", "hotJob",
                    )
                    if item.get(key) is not None
                } or None,
            }

    jobs = list(jobs_by_id.values())
    return jobs, "; ".join(errors) if errors else None
