from .base import *


def fetch_mckinsey(scrape, seen, company):
    """McKinsey's public careers search API."""
    api_url = scrape.get("api_url") or (
        "https://gateway.mckinsey.com/apigw-x0cceuow60/v1/api/jobs/search"
    )
    job_prefix = (
        scrape.get("job_url_prefix")
        or "https://www.mckinsey.com/careers/search-jobs/jobs/"
    ).rstrip("/") + "/"
    max_pages = int(scrape.get("hard_max_pages") or 200)
    jobs = []
    signatures = set()

    for page in range(1, max_pages + 1):
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
        try:
            response = requests.get(
                api_url,
                params={"start": page},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return jobs, str(exc)

        rows = payload.get("docs") or []
        if not rows:
            break
        signature = tuple(str(row.get("jobID") or "") for row in rows)
        if signature in signatures:
            break
        signatures.add(signature)

        for row in rows:
            title = norm(row.get("title") or "")
            job_id = str(row.get("jobID") or "").strip()
            friendly = norm(row.get("friendlyURL") or "")
            if not title or not job_id:
                continue
            job_url = job_prefix + (friendly or job_id)
            cities = row.get("cities") or []
            countries = row.get("countries") or []
            raw_locations = []
            for index, city in enumerate(cities):
                country = countries[index] if index < len(countries) else ""
                raw = norm(", ".join(value for value in (city, country) if value))
                if raw and raw not in raw_locations:
                    raw_locations.append(raw)
            location = normalize_location(raw_locations[0]) if raw_locations else ""
            description = "\n\n".join(
                str(row.get(field) or "")
                for field in ("whoYouWillWorkWith", "whatYouWillDo", "yourBackground")
                if row.get(field)
            )
            jobs.append({
                "title": title,
                "url": job_url,
                "apply_url": row.get("jobApplyURL"),
                "ats_id": job_id,
                "requisition_id": job_id,
                "location": location,
                "location_raw": raw_locations[0] if raw_locations else None,
                "locations_raw": raw_locations,
                "source": "mckinsey",
                "posted": _parse_posted(row.get("postedToLinkedInDate") or "") or None,
                "posted_at": row.get("postedToLinkedInDate"),
                "posted_raw": row.get("postedToLinkedInDate"),
                "department": row.get("interest") or None,
                "description": normalize_description(description) or None,
                "description_raw": description or None,
                "raw_data": {
                    "interest_category": row.get("interestCategory"),
                    "functions": row.get("functions"),
                    "industries": row.get("industries"),
                },
            })

        total = int(payload.get("numFound") or 0)
        if total and len(jobs) >= total:
            break

    return jobs, None
