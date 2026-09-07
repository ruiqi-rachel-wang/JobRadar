from .base import *

# ── Eightfold ─────────────────────────────────────────────────────────────────

def _fetch_eightfold_pcsx(scrape, company, host, domain):
    """Use Eightfold's CSRF-protected public PCSX search when v2 is disabled."""
    list_url = scrape.get("list_url", "")
    locale = scrape.get("locale") or "en-US"
    try:
        bootstrap = requests.get(list_url, headers=HEADERS, timeout=25)
        bootstrap.raise_for_status()
        soup = BeautifulSoup(bootstrap.text, "lxml")
        token_node = soup.select_one('meta[name="_csrf"]')
        csrf_token = token_node.get("content") if token_node else ""
        if not csrf_token:
            return [], "Eightfold PCSX bootstrap did not expose a CSRF token"
    except Exception as exc:
        return [], str(exc)

    jobs = []
    start = 0
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    seen_page_signatures = set()
    expected_total = None
    for _page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
        try:
            response = requests.get(
                f"https://{host}/api/pcsx/search",
                params={
                    "domain": domain,
                    "query": scrape.get("query", ""),
                    "location": scrape.get("location", ""),
                    "start": start,
                    "hl": locale,
                },
                headers={
                    **HEADERS,
                    "Accept": "application/json",
                    "Referer": list_url,
                    "X-CSRF-Token": csrf_token,
                },
                timeout=25,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return jobs, str(exc)

        data = payload.get("data") or {}
        positions = data.get("positions") or []
        if expected_total is None:
            expected_total = int(data.get("count") or 0)
        if not positions:
            break
        signature = tuple(str(item.get("id") or "") for item in positions)
        if signature in seen_page_signatures:
            break
        seen_page_signatures.add(signature)

        for item in positions:
            title = norm(item.get("name") or "")
            position_id = str(item.get("id") or "").strip()
            position_path = item.get("positionUrl") or ""
            job_url = urljoin(f"https://{host}/", position_path)
            raw_locations = [
                norm(str(value))
                for value in (item.get("locations") or [])
                if value
            ]
            standardized_locations = [
                norm(str(value))
                for value in (item.get("standardizedLocations") or [])
                if value
            ]
            normalized_locations = [
                normalize_location(value)
                for value in (standardized_locations or raw_locations)
                if value
            ]
            if not title or not position_id or not job_url:
                continue
            posted_ts = item.get("postedTs") or item.get("creationTs")
            jobs.append({
                "title": title,
                "url": job_url,
                "ats_id": position_id,
                "requisition_id": item.get("displayJobId")
                    or item.get("atsJobId"),
                "location": "; ".join(normalized_locations),
                "location_raw": raw_locations[0] if raw_locations else None,
                "locations_raw": raw_locations,
                "remote_mode": item.get("workLocationOption"),
                "source": "eightfold",
                "posted": None,
                "posted_at": posted_ts,
                "posted_raw": posted_ts,
                "department": item.get("department"),
                "raw_data": {
                    key: item.get(key)
                    for key in (
                        "locations", "standardizedLocations", "department",
                        "workLocationOption", "locationFlexibility",
                    )
                    if item.get(key) is not None
                },
            })

        start += len(positions)
        if expected_total and start >= expected_total:
            break

    if expected_total and len(jobs) != expected_total:
        return jobs, (
            f"{company} partial listing: fetched {len(jobs)} of "
            f"{expected_total} source records"
        )
    return jobs, None

def fetch_eightfold(scrape, seen, company):
    """Eightfold careers API, e.g. https://albemarle.eightfold.ai/careers."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    host = urlparse(list_url).netloc
    if not host:
        return [], f"cannot parse Eightfold URL: {list_url}"
    domain = scrape.get("eightfold_domain") or scrape.get("domain") or ""
    if not domain:
        prefix = host.split(".eightfold.ai")[0]
        domain = f"{prefix}.com"
    if scrape.get("pcsx_only"):
        return _fetch_eightfold_pcsx(scrape, company, host, domain)
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    limit = int(scrape.get("limit", 100))
    jobs = []
    seen_page_signatures = set()
    start = 0
    for page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
        try:
            r = requests.get(
                f"https://{host}/api/apply/v2/jobs",
                params={"domain": domain, "start": start, "num": limit},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            if status_code in (401, 403) and scrape.get("pcsx_fallback", True):
                pcsx_jobs, pcsx_error = _fetch_eightfold_pcsx(
                    scrape, company, host, domain
                )
                if pcsx_jobs:
                    jobs.extend(pcsx_jobs)
                    if pcsx_error:
                        return jobs, pcsx_error
                    break
            if scrape.get("fallback") == "playwright" and scrape.get("allow_browser_fallback") is not False:
                from .playwright import _fetch_playwright_with_source
                return _fetch_playwright_with_source(scrape, seen, company, "eightfold")
            return jobs, str(e)
        positions = data.get("positions") or []
        if not positions:
            break
        signature = tuple(
            str(item.get("id") or item.get("canonicalPositionUrl") or "")
            for item in positions[:10]
        )
        if signature and signature in seen_page_signatures:
            break
        if signature:
            seen_page_signatures.add(signature)
        for j in positions:
            title = norm(j.get("name") or j.get("posting_name") or "")
            jurl = j.get("canonicalPositionUrl") or ""
            if not jurl and j.get("id"):
                jurl = f"https://{host}/careers/job/{j.get('id')}"
            raw_locations = j.get("locations") or []
            if isinstance(raw_locations, str):
                raw_locations = [raw_locations]
            raw_locations = [
                norm(str(value)) for value in raw_locations if value
            ]
            if not raw_locations and j.get("location"):
                raw_locations = [norm(str(j.get("location")))]
            loc = "; ".join(
                value for value in (
                    normalize_location(raw) for raw in raw_locations
                ) if value
            )
            ts = j.get("t_create") or j.get("t_update")
            if title and jurl:
                jobs.append({
                    "title": title,
                    "url": jurl,
                    "ats_id": str(j.get("id") or j.get("ats_job_id") or "") or None,
                    "requisition_id": (
                        j.get("req_id")
                        or j.get("requisition_id")
                        or j.get("ats_job_id")
                    ),
                    "apply_url": j.get("applyUrl"),
                    "location": loc,
                    "location_raw": raw_locations[0] if raw_locations else None,
                    "locations_raw": raw_locations,
                    "remote_mode": j.get("work_location_option")
                        or j.get("workplaceType"),
                    "source": "eightfold",
                    "posted": None,
                    "posted_at": ts,
                    "posted_raw": ts,
                    "description": j.get("description") or j.get("job_description"),
                    "description_raw": j.get("description") or j.get("job_description"),
                    "employment_type_raw": j.get("type")
                        or j.get("employment_type"),
                    "department": j.get("department"),
                    "team": j.get("team"),
                    "raw_data": {
                        key: j.get(key)
                        for key in (
                            "locations", "location", "work_location_option",
                            "type", "department", "team",
                        )
                        if j.get(key) is not None
                    },
                })
        count = int(data.get("count") or 0)
        # Eightfold commonly caps responses at ten even when `num=100` is
        # requested. Advance by what was actually returned, not by the
        # requested page size, or every intermediate page is skipped.
        next_start = start + len(positions)
        if next_start <= start:
            break
        start = next_start
        if count and start >= count:
            break
    if jobs and scrape.get("fetch_experience_details"):
        limit_details = int(scrape.get("max_experience_details") or scrape.get("max_desc_details") or 0)
        targets = [j for j in jobs if not j.get("description")]
        if limit_details > 0:
            targets = targets[:limit_details]
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(targets)))) as executor:
            for job, detail in zip(targets, executor.map(lambda j: fetch_detail_description(j.get("url") or ""), targets)):
                if detail.get("description"):
                    job.update(detail)
    return jobs, None
