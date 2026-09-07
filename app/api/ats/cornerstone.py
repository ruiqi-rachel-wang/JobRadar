from .base import *

# ── Cornerstone OnDemand ─────────────────────────────────────────────────────

_CSOD_TOKEN_RE = re.compile(r'csod\.context\.token\s*=\s*[\'"]([^\'"]+)[\'"]')
_CSOD_TOKEN_FALLBACK_RE = re.compile(r'"token"\s*:\s*"([^"]+)"')
_CSOD_API_HOST_RE = re.compile(r'(https?://[a-z0-9-]+\.api\.csod\.com)', re.I)


def _cornerstone_site(scrape):
    value = (scrape.get("list_url") or scrape.get("cornerstone_slug") or "").strip()
    site_id = int(scrape.get("site_id") or 1)
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        slug_match = re.search(r"[?&]c=([^&#]+)", value)
        slug = unquote(slug_match.group(1)) if slug_match else (parsed.hostname or "").split(".")[0]
        site_match = re.search(r"/careersite/(\d+)/", parsed.path, re.I)
        if site_match:
            site_id = int(site_match.group(1))
        return value, slug, site_id
    slug = value
    return (
        f"https://{slug}.csod.com/ux/ats/careersite/{site_id}/home?c={slug}",
        slug,
        site_id,
    )


def _cornerstone_location(value):
    """Return both the source location value and a display-safe string."""
    raw_parts = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                candidate = norm(item)
            elif isinstance(item, dict):
                fields = [
                    item.get(key)
                    for key in ("city", "state", "country")
                    if isinstance(item.get(key), str) and item.get(key).strip()
                ]
                candidate = norm(", ".join(fields))
                if not candidate:
                    candidate = norm(item.get("name") or item.get("displayName") or "")
            else:
                candidate = ""
            if candidate and candidate.lower() not in {part.lower() for part in raw_parts}:
                raw_parts.append(candidate)
    raw = "; ".join(raw_parts)
    return raw, normalize_location(raw)


def _cornerstone_job(item, career_url, slug, site_id):
    ats_id = str(item.get("requisitionId") or "").strip()
    title = norm(item.get("displayJobTitle") or "")
    if not ats_id or not title:
        return None
    origin = f"{urlparse(career_url).scheme or 'https'}://{urlparse(career_url).netloc}"
    job_url = f"{origin}/ux/ats/careersite/{site_id}/job/{ats_id}?c={slug}"
    location_raw, location = _cornerstone_location(item.get("locations"))
    posted_raw = item.get("postingEffectiveDate")
    description_raw = item.get("externalDescription")
    employment_raw = item.get("schedule") if isinstance(item.get("schedule"), str) else None
    raw_data = {
        key: item.get(key)
        for key in (
            "jobType", "schedule", "shift", "department", "industry",
            "category", "experienceLevel",
        )
        if item.get(key) not in (None, "")
    }
    return {
        "title": title,
        "url": job_url,
        "ats_id": ats_id,
        "requisition_id": ats_id,
        "location": location or None,
        "location_raw": location_raw or None,
        "source": "cornerstone",
        "posted": _parse_posted(posted_raw or "") or None,
        "posted_at": posted_raw or None,
        "posted_raw": posted_raw or None,
        "description": description_raw or None,
        "description_raw": description_raw or None,
        "employment_type_raw": employment_raw,
        "department": item.get("department") or item.get("category") or None,
        "raw_data": raw_data or None,
    }


def fetch_cornerstone(scrape, seen, company):
    """Fetch every public requisition from a Cornerstone careers site."""
    career_url, slug, site_id = _cornerstone_site(scrape)
    if not slug:
        return [], "no cornerstone_slug or list_url"
    try:
        init = requests.get(career_url, headers=HEADERS, timeout=25)
        init.raise_for_status()
    except Exception as exc:
        return [], str(exc)

    token_match = _CSOD_TOKEN_RE.search(init.text) or _CSOD_TOKEN_FALLBACK_RE.search(init.text)
    if not token_match:
        return [], f"Cornerstone token not found at {career_url}"
    api_match = _CSOD_API_HOST_RE.search(init.text)
    api_host = api_match.group(1) if api_match else "https://na.api.csod.com"
    api_url = api_host.rstrip("/") + "/rec-job-search/external/jobs"
    origin = f"{urlparse(career_url).scheme or 'https'}://{urlparse(career_url).netloc}"
    headers = {
        **HEADERS,
        "Authorization": f"Bearer {token_match.group(1)}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Origin": origin,
        "Referer": origin + "/",
    }
    page_size = max(1, min(100, int(scrape.get("page_size") or 25)))

    def fetch_page(page):
        if scan_deadline_exceeded(scrape):
            raise TimeoutError("scan deadline exceeded")
        response = requests.post(
            api_url,
            headers=headers,
            json={
                "careerSiteId": site_id,
                "careerSitePageId": site_id,
                "pageNumber": page,
                "pageSize": page_size,
                "cultureId": 1,
                "cultureName": "en-US",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    try:
        first = fetch_page(1)
        data = first.get("data") or {}
        total = int(data.get("totalCount") or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        responses = [first]
        if pages > 1:
            workers = min(4, pages - 1)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                responses.extend(executor.map(fetch_page, range(2, pages + 1)))
    except Exception as exc:
        return [], str(exc)

    jobs = {}
    for payload in responses:
        for item in (payload.get("data") or {}).get("requisitions") or []:
            job = _cornerstone_job(item, career_url, slug, site_id)
            if job:
                jobs[job["ats_id"]] = job
    if total and len(jobs) < total:
        return list(jobs.values()), f"partial listing: fetched {len(jobs)} of {total} jobs"
    return list(jobs.values()), None
