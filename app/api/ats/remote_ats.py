"""The public employer feed used by Remote's own careers site."""
from .base import *
from uuid import UUID


def fetch_remote_ats(scrape, seen, company):
    base = (scrape.get("list_url") or "https://apply.remote.com").rstrip("/")
    try:
        response = requests.get(base + "/api/public/jobs", headers=HEADERS, timeout=25)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return [], str(exc)
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        return [], "invalid Remote ATS jobs response"

    jobs, invalid = [], 0
    for posting in payload["jobs"]:
        if not isinstance(posting, dict):
            invalid += 1
            continue
        if str(posting.get("status") or "").lower() != "open" or posting.get("accepting_applications") is False:
            continue
        try:
            posting_id = str(UUID(str(posting.get("public_id") or "")))
        except ValueError:
            invalid += 1
            continue
        title = norm(posting.get("title") or "")
        if not title:
            invalid += 1
            continue
        location = posting.get("location") or ""
        parts = [posting.get("description"), posting.get("requirements")]
        description = "\n\n".join(
            part.strip() for part in parts
            if isinstance(part, str) and part.strip() not in {"", "-"}
        )
        job_url = f"{base}/jobs/{posting_id}"
        jobs.append({
            "title": title, "url": job_url, "apply_url": job_url,
            "ats_id": posting_id, "source": "remote_ats",
            "location": location, "location_raw": location or None,
            "locations_raw": [location] if location else [],
            "posted_at": posting.get("opened_at"), "posted_raw": posting.get("opened_at"),
            "description": description or None, "description_raw": description or None,
            "employment_type": posting.get("employment_type"),
            "employment_type_raw": posting.get("employment_type"),
            "department": posting.get("department"), "workplace_type": posting.get("workplace"),
        })
    error = f"partial Remote ATS feed: {invalid} invalid records" if invalid else None
    return jobs, error
