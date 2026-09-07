from .base import *


def fetch_yum(scrape, seen, company):
    """Fetch Yum's public grouped jobs endpoint."""
    api_url = scrape.get("api_url") or "https://jobs.yum.com/api/jobs"
    group_id = str(scrape.get("group_id") or "1583")
    try:
        response = requests.get(
            api_url,
            params={"groupId": group_id, "groupBy": "city", "includeJobs": 1},
            headers={**HEADERS, "Accept": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return [], str(exc)

    site_url = (scrape.get("list_url") or "https://jobs.yum.com/jobs").rstrip("/") + "/"
    jobs = []
    for group in data.get("jobs") or []:
        for item in group.get("jobs") or []:
            title = norm(item.get("title") or "")
            path = str(item.get("url") or "").lstrip("/")
            ats_id = str(
                item.get("unique_identifier") or item.get("id") or ""
            ).strip()
            if not title or not path or not ats_id or not item.get("live"):
                continue
            job_url = urljoin(site_url, path)
            location_raw = norm(item.get("address") or "")
            raw_date = item.get("date") or item.get("created_at") or ""
            description = item.get("content") or ""
            requirements = item.get("requirements") or ""
            if requirements and requirements not in description:
                description = (
                    f"{description}\n\n<h3>Requirements</h3>\n{requirements}"
                ).strip()
            jobs.append({
                "title": title,
                "url": job_url,
                "apply_url": item.get("application"),
                "ats_id": ats_id,
                "requisition_id": ats_id,
                "location": normalize_location(location_raw),
                "location_raw": location_raw or None,
                "locations_raw": [location_raw] if location_raw else [],
                "description": description or None,
                "description_raw": description or None,
                "source": "yum",
                "posted": _parse_posted(raw_date[:10]) or None,
                "posted_at": raw_date or None,
                "posted_raw": raw_date or None,
                "employment_type_raw": item.get("job_type"),
                "department": item.get("category"),
                "team": item.get("subdivision"),
                "raw_data": {
                    key: item.get(key)
                    for key in (
                        "category", "subdivision", "job_type", "remote",
                        "hybrid_locations", "remote_locations",
                    )
                    if item.get(key) is not None
                },
            })
    return jobs, None
