from .base import *
from urllib.parse import quote

# ── Rippling ──────────────────────────────────────────────────────────────────

_RIPPLING_DESC_SELECTORS = [
    "main", "article", '[class*="job"]', '[class*="description"]',
]


def _rippling_slug(scrape):
    slug = norm(scrape.get("rippling_slug") or "")
    if slug:
        return slug
    match = re.search(
        r"https?://ats\.rippling\.com/([^/?#]+)/jobs",
        scrape.get("list_url") or "",
        re.I,
    )
    return match.group(1) if match else ""


def _rippling_location_values(item):
    values = []
    for source in (
        item.get("workLocations"),
        item.get("locations"),
        [item.get("workLocation")],
        [item.get("location")],
    ):
        for value in source or []:
            if isinstance(value, str):
                label = norm(value)
            elif isinstance(value, dict):
                label = norm(
                    value.get("displayName")
                    or value.get("label")
                    or value.get("name")
                    or ", ".join(
                        str(value.get(key))
                        for key in ("city", "stateCode", "region", "country")
                        if value.get(key)
                    )
                )
            else:
                label = ""
            if label and label not in values:
                values.append(label)
    return values


def _fetch_rippling_api(scrape, seen, company):
    """Use Rippling's public board API before falling back to rendered HTML."""
    slug = _rippling_slug(scrape)
    if not slug:
        return [], "no rippling slug"
    base = f"https://api.rippling.com/platform/api/ats/v1/board/{quote(slug, safe='')}/jobs"
    try:
        response = requests.get(
            base,
            headers={**HEADERS, "Accept": "application/json"},
            timeout=25,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return [], str(exc)
    items = (
        payload.get("items") or payload.get("jobs") or []
        if isinstance(payload, dict) else payload
    )
    if not isinstance(items, list):
        return [], "unexpected Rippling API response"

    jobs_by_key = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        ats_id = str(item.get("uuid") or item.get("id") or "")
        title = norm(item.get("name") or item.get("title") or "")
        jurl = (
            item.get("url")
            or item.get("hostedUrl")
            or (f"https://ats.rippling.com/{slug}/jobs/{ats_id}" if ats_id else "")
        )
        locations_raw = _rippling_location_values(item)
        department = item.get("department")
        if isinstance(department, dict):
            department = department.get("label") or department.get("id")
        job = {
            "title": title,
            "url": jurl,
            "ats_id": ats_id or None,
            "location": locations_raw[0] if locations_raw else None,
            "location_raw": locations_raw[0] if locations_raw else None,
            "locations_raw": locations_raw,
            "source": "rippling",
            "posted_at": item.get("createdAt") or item.get("created_at"),
            "department": department,
            "employment_type": (
                item.get("employmentType")
                if isinstance(item.get("employmentType"), str) else None
            ),
            "employment_type_raw": item.get("employmentType"),
            "workplace_type": item.get("workType") or item.get("workplaceType"),
            "raw_data": {
                key: item.get(key)
                for key in (
                    "department", "team", "employmentType", "workLocation",
                    "workType", "experienceLevel",
                )
                if item.get(key)
            } or None,
        }
        if title and jurl:
            key = ats_id or jurl
            existing_job = jobs_by_key.get(key)
            if existing_job:
                for location in locations_raw:
                    if location not in existing_job["locations_raw"]:
                        existing_job["locations_raw"].append(location)
                existing_job["location_raw"] = (
                    existing_job["locations_raw"][0]
                    if existing_job["locations_raw"] else None
                )
                existing_job["location"] = existing_job["location_raw"]
                existing_raw = existing_job.setdefault("raw_data", {})
                variants = existing_raw.setdefault("workLocationVariants", [])
                raw_variant = item.get("workLocation")
                if raw_variant and raw_variant not in variants:
                    variants.append(raw_variant)
                continue
            if isinstance(job.get("raw_data"), dict) and item.get("workLocation"):
                job["raw_data"]["workLocationVariants"] = [item["workLocation"]]
            jobs_by_key[key] = job

    jobs = list(jobs_by_key.values())
    detail_targets = []
    for job in jobs:
        known = seen.get(
            make_id(
                company,
                job["url"],
                job["title"],
                "rippling",
                job.get("ats_id") or "",
            )
        ) if isinstance(seen, dict) else None
        if not (isinstance(known, dict) and known.get("has_description")):
            detail_targets.append(job)

    def fetch_detail(job):
        try:
            detail_response = requests.get(
                f"{base}/{quote(job['ats_id'], safe='')}",
                headers={**HEADERS, "Accept": "application/json"},
                timeout=20,
            )
            if detail_response.status_code != 200:
                return job, None
            return job, detail_response.json()
        except Exception:
            return job, None

    max_details = int(scrape.get("max_desc_details", 500))
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, min(8, int(scrape.get("desc_workers", 8))))
    ) as executor:
        for job, detail in executor.map(fetch_detail, detail_targets[:max_details]):
            if not isinstance(detail, dict):
                continue
            desc_obj = detail.get("description")
            if isinstance(desc_obj, dict):
                parts = [
                    str(desc_obj.get(key))
                    for key in ("role", "company")
                    if desc_obj.get(key)
                ]
                description = "\n\n".join(parts)
            else:
                description = str(desc_obj or "")
            if description:
                job["description"] = description
                job["description_raw"] = description
            locations_raw = _rippling_location_values(detail)
            if locations_raw:
                merged_locations = list(job.get("locations_raw") or [])
                for location in locations_raw:
                    if location not in merged_locations:
                        merged_locations.append(location)
                job["location"] = merged_locations[0]
                job["location_raw"] = merged_locations[0]
                job["locations_raw"] = merged_locations
            emp = detail.get("employmentType")
            if isinstance(emp, dict):
                job["employment_type"] = emp.get("label") or emp.get("id")
                job["employment_type_raw"] = emp
            elif emp:
                job["employment_type"] = emp
                job["employment_type_raw"] = emp
            job["posted_at"] = detail.get("createdOn") or job.get("posted_at")
            if not isinstance(job.get("raw_data"), dict):
                job["raw_data"] = {}
            job["raw_data"].update({
                key: detail.get(key)
                for key in ("employmentType", "workLocations")
                if detail.get(key)
            })
    return jobs, None


def _fetch_rippling_detail(jurl, timeout=15):
    try:
        r = requests.get(jurl, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        raw_description = ""
        for sel in _RIPPLING_DESC_SELECTORS:
            el = soup.select_one(sel)
            if el:
                raw_description = str(el)
                if len(normalize_description(raw_description) or "") > 250:
                    break
        raw_description = raw_description or str(soup.body or soup)
        text = normalize_description(raw_description) or ""
        loc = extract_location_from_text(text)
        return {
            "description": text if len(text) > 150 else "",
            "description_raw": raw_description if len(text) > 150 else "",
            "location": loc,
        }
    except Exception:
        return {"description": "", "location": ""}


def _fetch_rippling_algolia(scrape):
    """Read the public search index used by Rippling's official careers UI."""
    app_id = norm(scrape.get("algolia_app_id") or "")
    api_key = norm(scrape.get("algolia_search_key") or "")
    index = norm(scrape.get("algolia_index") or "")
    if not app_id or not api_key or not index:
        return [], "no __NEXT_DATA__ jobs payload"

    endpoint = f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/{quote(index, safe='')}/query"
    headers = {
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": api_key,
        "Content-Type": "application/json",
    }
    per_page = max(1, min(1000, int(scrape.get("algolia_hits_per_page", 1000))))
    hard_max_pages = max(1, int(scrape.get("hard_max_pages", 20)))
    by_url = {}
    first_err = None
    expected_hits = None
    pages_to_fetch = 1
    for page in range(hard_max_pages):
        if page >= pages_to_fetch:
            break
        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json={"query": "", "hitsPerPage": per_page, "page": page},
                timeout=25,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            first_err = str(exc)
            break
        expected_hits = int(data.get("nbHits") or 0)
        pages_to_fetch = max(1, int(data.get("nbPages") or 1))
        for hit in data.get("hits") or []:
            title = norm(hit.get("name") or hit.get("title") or "")
            job_url = norm(hit.get("url") or "")
            if not title or not job_url:
                continue
            locations = []
            for value in hit.get("locationNames") or []:
                location = normalize_location(norm(value))
                if location and location not in locations:
                    locations.append(location)
            existing = by_url.get(job_url)
            if existing:
                for location in locations:
                    if location not in existing["_locations"]:
                        existing["_locations"].append(location)
                continue
            by_url[job_url] = {
                "title": title,
                "url": job_url,
                "location": "",
                "source": "rippling",
                "posted": _parse_posted(hit.get("posted") or hit.get("publishedAt") or ""),
                "_locations": locations,
            }

    jobs = []
    for job in by_url.values():
        locations = job.pop("_locations")
        job["location"] = "; ".join(locations)
        jobs.append(job)
    if expected_hits is not None and pages_to_fetch > hard_max_pages:
        partial = f"partial listing: fetched {min(expected_hits, hard_max_pages * per_page)} of {expected_hits} index rows"
        first_err = f"{first_err}; {partial}" if first_err else partial
    return jobs, first_err


def fetch_rippling(scrape, seen, company):
    api_jobs, api_error = _fetch_rippling_api(scrape, seen, company)
    if api_jobs:
        return api_jobs, None
    list_url = scrape.get("list_url") or "https://www.rippling.com/careers/open-roles"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        return [], api_error or str(e)
    soup = BeautifulSoup(r.text, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    items = []
    if script and script.string:
        try:
            data = json.loads(script.string)
            page_props = (data.get("props") or {}).get("pageProps") or {}
            items = (page_props.get("jobs") or {}).get("items") or []
            if not items:
                # ats.rippling.com embed boards ship jobs in a react-query
                # dehydratedState payload instead of pageProps.jobs.
                for query in (page_props.get("dehydratedState") or {}).get("queries") or []:
                    if "job-posts" in str(query.get("queryKey")):
                        items = ((query.get("state") or {}).get("data") or {}).get("items") or []
                        if items:
                            break
        except Exception:
            items = []
    jobs = []
    if items:
        for j in items:
            title = norm(j.get("name") or "")
            jurl = j.get("url") or ""
            loc_parts = []
            raw_locations = []
            for loc_data in (j.get("locations") or []):
                if isinstance(loc_data, dict):
                    raw_location = norm(", ".join(
                        x for x in [loc_data.get("city"), loc_data.get("stateCode"), loc_data.get("country")] if x
                    ))
                    if raw_location:
                        raw_locations.append(raw_location)
                    s = normalize_location(raw_location)
                    if s:
                        loc_parts.append(s)
            loc = "; ".join(loc_parts)
            if jurl and jurl.startswith("/"):
                jurl = urljoin(list_url, jurl)
            if title and jurl:
                jobs.append({
                    "title": title,
                    "url": jurl,
                    "ats_id": str(j.get("id") or j.get("uuid") or "") or None,
                    "apply_url": j.get("applyUrl") or jurl,
                    "location": loc,
                    "location_raw": raw_locations[0] if raw_locations else None,
                    "locations_raw": raw_locations,
                    "remote_mode": j.get("workplaceType"),
                    "source": "rippling",
                    "posted": None,
                    "posted_at": j.get("createdOn") or j.get("createdAt"),
                    "posted_raw": j.get("createdOn") or j.get("createdAt"),
                    "employment_type_raw": j.get("employmentType"),
                    "department": j.get("department"),
                    "team": j.get("team"),
                    "raw_data": {
                        key: j.get(key)
                        for key in (
                            "locations", "workplaceType", "employmentType",
                            "department", "team",
                        )
                        if j.get(key) is not None
                    },
                })
    else:
        jobs, algolia_err = _fetch_rippling_algolia(scrape)
        if algolia_err and not jobs:
            return [], algolia_err
    detail_targets = []
    refresh_experience = bool(scrape.get("refresh_experience"))
    fetch_details = bool(scrape.get("fetch_experience_details") or scrape.get("fetch_details"))
    if fetch_details:
        for job in jobs:
            jid_key = make_id(
                company, job["url"], job["title"], "rippling", job.get("ats_id", "")
            )
            existing = (
                seen.get(jid_key) or seen.get(make_id(company, job["url"], job["title"]))
                if isinstance(seen, dict) else None
            )
            existing_has_desc = isinstance(existing, dict) and existing.get("has_description")
            if refresh_experience or not existing_has_desc or not job.get("location"):
                detail_targets.append(job)
    max_desc = int(scrape.get("max_desc_details", 250))
    if detail_targets[:max_desc]:
        workers = int(scrape.get("desc_workers", 6))
        def _get_detail(job):
            return job, _fetch_rippling_detail(job["url"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for job, detail in ex.map(_get_detail, detail_targets[:max_desc]):
                if detail.get("location") and not job.get("location"):
                    job["location"] = detail["location"]
                if detail.get("description"):
                    job["description"] = detail["description"]
    return jobs, algolia_err if not items else None
