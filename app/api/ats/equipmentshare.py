from datetime import datetime, timezone

from .base import *


def _string_values(attributes, key):
    value = (attributes.get(key) or {}).get("stringValues") or []
    return [norm(item) for item in value if norm(item)]


def _google_timestamp(value):
    if not isinstance(value, dict):
        return None
    try:
        return datetime.fromtimestamp(
            int(value.get("seconds")),
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None


def fetch_equipmentshare(scrape, seen, company):
    """EquipmentShare's first-party public careers API.

    The branded careers page initially renders only ten cards even though its
    own API exposes the complete current board. The API also mixes "future
    interest" records into the total, so those must be removed explicitly.
    """
    api_url = (
        scrape.get("api_url")
        or "https://omnichannel-api.equipmentshare.com/jobs"
    )
    page_size = max(1, min(2000, int(scrape.get("page_size") or 1000)))
    max_pages = max(1, int(scrape.get("hard_max_pages") or 100))
    jobs = []
    skip = 0
    reported_total = None

    for _page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
        try:
            response = requests.get(
                api_url,
                params={"skip": skip, "take": page_size},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            if jobs:
                return jobs, f"EquipmentShare page at skip {skip} failed: {exc}"
            return [], str(exc)

        raw_jobs = data.get("jobs") or []
        metadata = data.get("metadata") or {}
        if reported_total is None:
            try:
                reported_total = int(metadata.get("total") or 0)
            except (TypeError, ValueError):
                reported_total = 0
        if not raw_jobs:
            break

        for item in raw_jobs:
            google_job = (item.get("google") or {}).get("job") or {}
            attributes = google_job.get("customAttributes") or {}
            if any(
                value.lower() == "true"
                for value in _string_values(attributes, "is_prospect_job")
            ):
                continue

            title = norm(google_job.get("title") or "")
            ats_id = str(
                item.get("job_id")
                or google_job.get("requisitionId")
                or ""
            ).strip()
            url = norm(item.get("absolute_url") or "")
            if not title or not ats_id or not url:
                continue
            # A few talent-pool records are mislabelled is_prospect_job=false
            # by the source even though the public title still begins Future.
            if re.match(r"^future\b", title, re.I):
                continue

            raw_locations = [
                norm(location)
                for location in (google_job.get("addresses") or [])
                if norm(location)
            ]
            normalized_locations = []
            for raw_location in raw_locations:
                location = normalize_location(raw_location)
                if (
                    location
                    and location.lower()
                    not in {value.lower() for value in normalized_locations}
                ):
                    normalized_locations.append(location)

            description_values = _string_values(
                attributes,
                "job_description",
            )
            description = (
                description_values[0]
                if description_values
                else google_job.get("description")
            )
            posted_at = _google_timestamp(
                google_job.get("postingPublishTime")
                or google_job.get("postingCreateTime")
            )
            work_environment = _string_values(
                attributes,
                "work_environment",
            )
            employment_values = (
                google_job.get("employmentTypes")
                or _string_values(attributes, "employment_type")
            )

            jobs.append({
                "title": title,
                "url": url,
                "apply_url": url,
                "ats_id": ats_id,
                "requisition_id": str(
                    google_job.get("requisitionId") or ats_id
                ),
                "location": "; ".join(normalized_locations) or None,
                "location_raw": "; ".join(raw_locations) or None,
                "locations_raw": raw_locations,
                "description": description or None,
                "description_raw": description or None,
                "source": "equipmentshare",
                "posted": posted_at[:10] if posted_at else None,
                "posted_at": posted_at,
                "posted_raw": posted_at,
                "employment_type_raw": (
                    "; ".join(str(value) for value in employment_values)
                    if employment_values else None
                ),
                "workplace_type": (
                    "; ".join(work_environment)
                    if work_environment else None
                ),
                "department": google_job.get("department") or None,
                "raw_data": {
                    "work_environment": work_environment or None,
                    "employment_types": employment_values or None,
                    "visibility": google_job.get("visibility"),
                },
            })

        skip += len(raw_jobs)
        if (
            len(raw_jobs) < page_size
            or (reported_total and skip >= reported_total)
        ):
            break

    if reported_total and skip < reported_total:
        return jobs, (
            f"EquipmentShare partial listing: fetched {skip} "
            f"of {reported_total} source records"
        )
    return jobs, None
