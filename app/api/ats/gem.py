from .base import *


_JOB_BOARD_QUERY = """
query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings {
      id
      extId
      title
      locations { id name city isoCountry isRemote extId }
      job {
        id
        department { id name extId }
        locationType
        employmentType
      }
    }
  }
  jobBoardExternal(vanityUrlPath: $boardId) {
    id
    teamDisplayName
    pageTitle
  }
}
"""


def fetch_gem(scrape, seen, company):
    """Fetch a public Gem job board through Gem's first-party GraphQL API."""
    slug = scrape.get("gem_board") or scrape.get("slug") or ""
    if not slug:
        return [], "no gem_board"

    try:
        response = requests.post(
            "https://jobs.gem.com/api/public/graphql",
            json={"query": _JOB_BOARD_QUERY, "variables": {"boardId": slug}},
            headers={**HEADERS, "Accept": "application/json", "Content-Type": "application/json"},
            timeout=25,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return [], str(exc)

    if data.get("errors"):
        return [], "; ".join(
            error.get("message") or str(error) for error in data.get("errors") or []
        )

    postings = (
        ((data.get("data") or {}).get("oatsExternalJobPostings") or {})
        .get("jobPostings")
        or []
    )
    jobs = []
    for posting in postings:
        title = norm(posting.get("title") or "")
        external_id = str(posting.get("extId") or "").strip()
        if not title or not external_id:
            continue

        locations = posting.get("locations") or []
        raw_locations = [
            norm(location.get("name") or "")
            for location in locations
            if isinstance(location, dict) and norm(location.get("name") or "")
        ]
        details = posting.get("job") or {}
        department = details.get("department") or {}
        location_type = norm(details.get("locationType") or "")
        is_remote = location_type.upper() == "REMOTE" or any(
            location.get("isRemote") is True
            for location in locations
            if isinstance(location, dict)
        )
        jobs.append({
            "title": title,
            "url": f"https://jobs.gem.com/{slug}/{external_id}",
            "ats_id": external_id,
            "location": normalize_location("; ".join(raw_locations)),
            "location_raw": "; ".join(raw_locations) or None,
            "locations_raw": raw_locations,
            "source": "gem",
            "department": department.get("name") if isinstance(department, dict) else None,
            "employment_type": details.get("employmentType"),
            "employment_type_raw": details.get("employmentType"),
            "workplace_type": location_type or None,
            "remote_mode": "remote" if is_remote else None,
            "raw_data": {
                "gem_posting_id": posting.get("id"),
                "gem_job_id": details.get("id"),
                "locations": locations,
            },
        })
    return jobs, None
