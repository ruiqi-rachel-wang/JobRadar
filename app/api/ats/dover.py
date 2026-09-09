from .base import *


def fetch_dover(scrape, seen, company):
    """Fetch postings from a public Dover careers page."""
    page_id = scrape.get("careers_page_id") or ""
    if not page_id:
        return [], "no careers_page_id"

    api_url = f"https://app.dover.com/api/v1/careers-page/{page_id}/jobs"
    jobs = []
    while api_url:
        try:
            response = requests.get(
                api_url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=25,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return jobs, str(exc)

        for posting in data.get("results") or []:
            if posting.get("is_published") is False or posting.get("is_sample") is True:
                continue
            title = norm(posting.get("title") or "")
            posting_id = str(posting.get("id") or "").strip()
            if not title or not posting_id:
                continue
            locations = posting.get("locations") or []
            raw_locations = [
                norm(location.get("name") or "")
                for location in locations
                if isinstance(location, dict) and norm(location.get("name") or "")
            ]
            workplace_type = norm(posting.get("workplace_type") or "")
            jobs.append({
                "title": title,
                "url": f"https://app.dover.com/{scrape.get('company_slug') or company}/careers/{posting_id}",
                "ats_id": posting_id,
                "location": normalize_location("; ".join(raw_locations)),
                "location_raw": "; ".join(raw_locations) or None,
                "locations_raw": raw_locations,
                "source": "dover",
                "workplace_type": workplace_type or None,
                "remote_mode": "remote" if workplace_type.upper() == "REMOTE" else None,
                "raw_data": {"locations": locations},
            })
        api_url = data.get("next")
    return jobs, None
