from .base import *


def fetch_comeet(scrape, seen, company):
    """Fetch public positions from a Spark Hire Recruit (Comeet) careers API."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"

    try:
        response = requests.get(
            list_url,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=25,
        )
        response.raise_for_status()
        positions = response.json()
    except Exception as exc:
        return [], str(exc)

    if not isinstance(positions, list):
        return [], "unexpected Comeet response"

    jobs = []
    for position in positions:
        title = norm(position.get("name") or "")
        url = (
            position.get("url_active_page")
            or position.get("url_recruit_hosted_page")
            or position.get("url_comeet_hosted_page")
            or ""
        )
        if not title or not url:
            continue

        location_data = position.get("location") or {}
        raw_location = norm(
            location_data.get("name")
            or ", ".join(
                value
                for value in (
                    location_data.get("city"),
                    location_data.get("state"),
                    location_data.get("country"),
                )
                if value
            )
        )
        workplace_type = norm(position.get("workplace_type") or "")
        jobs.append({
            "title": title,
            "url": url,
            "ats_id": str(position.get("uid") or "") or None,
            "location": normalize_location(raw_location),
            "location_raw": raw_location or None,
            "locations_raw": [raw_location] if raw_location else [],
            "source": "comeet",
            "employment_type": position.get("employment_type"),
            "employment_type_raw": position.get("employment_type"),
            "department": position.get("department"),
            "workplace_type": workplace_type or None,
            "remote_mode": "remote" if location_data.get("is_remote") is True else None,
        })

    return jobs, None
