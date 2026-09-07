from .base import *
from urllib.parse import quote

_ashby_lock = threading.Lock()
_ashby_last_request = 0.0


# ── Ashby ─────────────────────────────────────────────────────────────────────

def _ashby_location(job):
    loc = normalize_location(norm(job.get("location") or ""))
    if loc:
        return loc

    addr = ((job.get("address") or {}).get("postalAddress")) or {}
    loc = normalize_location(norm(", ".join(
        x for x in [
            addr.get("addressLocality"),
            addr.get("addressRegion"),
            addr.get("addressCountry"),
        ] if x
    )))
    if loc:
        return loc

    secondary = job.get("secondaryLocations") or []
    if secondary:
        first = secondary[0] or {}
        loc = normalize_location(norm(first.get("location") or ""))
        if loc:
            return loc
        addr = first.get("address") or {}
        return normalize_location(norm(", ".join(
            x for x in [
                addr.get("addressLocality"),
                addr.get("addressRegion"),
                addr.get("addressCountry"),
            ] if x
        )))
    return ""

def _fetch_ashby_public_api(slug, company):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug, safe='')}"
    try:
        r = requests.get(url, headers={**HEADERS, "Accept": "application/json"}, timeout=25)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)

    postings = data.get("jobs") or []
    jobs = []
    for j in postings:
        title = norm(j.get("title") or "")
        jurl = j.get("jobUrl") or j.get("applyUrl") or ""
        if not jurl and j.get("id"):
            jurl = f"https://jobs.ashbyhq.com/{slug}/{j.get('id')}"
        posted_raw = j.get("publishedAt") or j.get("publishedDate")
        raw_locations = []
        if j.get("location"):
            raw_locations.append(j.get("location"))
        for secondary in j.get("secondaryLocations") or []:
            if isinstance(secondary, dict) and secondary.get("location"):
                raw_locations.append(secondary["location"])
        loc = raw_locations[0] if raw_locations else _ashby_location(j)
        desc = j.get("descriptionHtml") or j.get("descriptionPlain") or ""
        if title and jurl:
            job = {
                "title": title,
                "url": jurl,
                "ats_id": str(j.get("id") or "") or None,
                "apply_url": j.get("applyUrl"),
                "location": loc,
                "location_raw": loc or None,
                "locations_raw": raw_locations or ([loc] if loc else []),
                "source": "ashby",
                "posted_at": posted_raw,
                "employment_type": j.get("employmentType"),
                "employment_type_raw": j.get("employmentType"),
                "department": j.get("department") if isinstance(j.get("department"), str) else None,
                "team": j.get("team") if isinstance(j.get("team"), str) else None,
                "workplace_type": j.get("workplaceType"),
                "remote_mode": "remote" if j.get("isRemote") is True else None,
                "raw_data": {
                    key: value for key, value in {
                        "address": j.get("address"),
                        "secondary_locations": j.get("secondaryLocations"),
                        "workplace_type": j.get("workplaceType"),
                    }.items() if value
                } or None,
            }
            if desc:
                job["description"] = desc
                job["description_raw"] = desc
            jobs.append(job)
    return jobs, None

def _ashby_post(payload, timeout=20, retries=4):
    global _ashby_last_request
    last_err = None
    for attempt in range(retries):
        try:
            with _ashby_lock:
                elapsed = time.monotonic() - _ashby_last_request
                if elapsed < 0.8:
                    time.sleep(0.8 - elapsed)
                r = requests.post(
                    "https://jobs.ashbyhq.com/api/non-user-graphql",
                    json=payload,
                    headers={**HEADERS, "Content-Type": "application/json"},
                    timeout=timeout,
                )
                _ashby_last_request = time.monotonic()
            if r.status_code != 429:
                r.raise_for_status()
                return r.json(), None
            retry_after = r.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(2 ** attempt, 8)
            last_err = f"429 Too Many Requests from Ashby; retried after {wait:g}s"
            time.sleep(wait)
        except Exception as e:
            last_err = str(e)
            if attempt < retries - 1:
                time.sleep(min(2 ** attempt, 8))
    return None, last_err or "Ashby request failed"

def fetch_ashby(scrape, seen, company):
    """Ashby GraphQL API — covers companies using jobs.ashbyhq.com"""
    slug = scrape.get("ashby_slug","")
    if not slug:
        return [], "no ashby_slug"

    public_jobs, public_err = _fetch_ashby_public_api(slug, company)
    default_location = normalize_location(scrape.get("default_location") or "")
    if default_location:
        for job in public_jobs or []:
            if (job.get("location") or "") in ("", "Remote", "Multiple Locations", "Austin HQ"):
                job["location"] = default_location
                job["locations_raw"] = [default_location]
                job.setdefault("raw_data", {})
                job["raw_data"]["configured_default_location"] = default_location
    if public_jobs:
        return public_jobs, None

    payload = {
        "operationName": "ApiJobBoardWithTeams",
        "variables": {"organizationHostedJobsPageName": slug},
        "query": (
            "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {"
            "  jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {"
            "    jobPostings {"
            "      id title locationName employmentType"
            "    }"
            "  }"
            "}"
        ),
    }
    data, err = _ashby_post(payload, timeout=20)
    if err:
        return [], public_err or err
    if data.get("errors"):
        return [], "; ".join((err.get("message") or str(err)) for err in data.get("errors", []))
    board    = ((data.get("data") or {}).get("jobBoard")) or {}
    postings = board.get("jobPostings") or []

    dates = {}
    descs = {}
    fetch_details = bool(scrape.get("fetch_posted_details", True))
    if fetch_details:
        # Ashby only exposes publish dates on the per-job detail query. Requests
        # go through _ashby_post, which serializes and backs off on 429s.
        # Also fetch descriptionHtml while we're there.
        def _fetch_ashby_detail(jid):
            detail_payload = {
                "operationName": "ApiJobPosting",
                "variables": {"organizationHostedJobsPageName": slug, "jobPostingId": jid},
                "query": (
                    "query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {"
                    "  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {"
                    "    publishedDate descriptionHtml"
                    "  }"
                    "}"
                ),
            }
            data, _ = _ashby_post(detail_payload, timeout=15, retries=2)
            if data:
                jp = ((data.get("data") or {}).get("jobPosting")) or {}
                return (
                    jid,
                    _parse_posted(jp.get("publishedDate") or jp.get("publishedAt") or ""),
                    jp.get("descriptionHtml") or "",
                )
            return jid, "", ""

        ids = []
        max_details = int(scrape.get("max_posted_details", 1000000))
        for j in postings:
            jid = j.get("id")
            title = norm(j.get("title") or "")
            if not jid:
                continue
            jurl = f"https://jobs.ashbyhq.com/{slug}/{jid}"
            existing = (
                seen.get(make_id(company, jurl, title, "ashby", jid))
                or seen.get(make_id(company, jurl, title))
                if isinstance(seen, dict) and title else None
            )
            existing_posted = _parse_posted((existing or {}).get("posted") or "") if isinstance(existing, dict) else ""
            existing_has_desc = isinstance(existing, dict) and existing.get("has_description")
            if existing_posted:
                dates[jid] = existing_posted
            if not (existing_posted and existing_has_desc):
                ids.append(jid)
        for jid in ids[:max_details]:
            jid, d, desc = _fetch_ashby_detail(jid)
            if d:
                dates[jid] = d
            if desc:
                descs[jid] = desc

    jobs = []
    for j in postings:
        title   = norm(j.get("title") or "")
        jid     = j.get("id") or ""
        jurl    = f"https://jobs.ashbyhq.com/{slug}/{jid}" if jid else ""
        raw_loc = j.get("locationName") or ""
        loc     = normalize_location(norm(raw_loc))
        posted  = dates.get(jid, "")
        emp_type = norm(j.get("employmentType") or "")
        if title:
            job = {
                "title": title,
                "url": jurl,
                "ats_id": jid or None,
                "location": raw_loc,
                "location_raw": raw_loc or None,
                "source": "ashby",
                "posted": posted or None,
                "employment_type": emp_type or None,
                "employment_type_raw": emp_type or None,
            }
            if descs.get(jid):
                job["description"] = descs[jid]
                job["description_raw"] = descs[jid]
            jobs.append(job)
    return jobs, None
