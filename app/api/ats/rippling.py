from .base import *
from urllib.parse import quote

# ── Rippling ──────────────────────────────────────────────────────────────────

_RIPPLING_DESC_SELECTORS = [
    "main", "article", '[class*="job"]', '[class*="description"]',
]


def _fetch_rippling_detail(jurl, timeout=15):
    try:
        r = requests.get(jurl, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        text = ""
        for sel in _RIPPLING_DESC_SELECTORS:
            el = soup.select_one(sel)
            if el:
                text = norm(el.get_text(" ", strip=True))
                if len(text) > 250:
                    break
        text = text or norm(soup.get_text(" ", strip=True))
        loc = extract_location_from_text(text)
        return {"description": text if len(text) > 150 else "", "location": loc}
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
    list_url = scrape.get("list_url") or "https://www.rippling.com/careers/open-roles"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        return [], str(e)
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
            for loc_data in (j.get("locations") or []):
                if isinstance(loc_data, dict):
                    s = normalize_location(norm(", ".join(
                        x for x in [loc_data.get("city"), loc_data.get("stateCode"), loc_data.get("country")] if x
                    )))
                    if s:
                        loc_parts.append(s)
            loc = "; ".join(loc_parts)
            if jurl and jurl.startswith("/"):
                jurl = urljoin(list_url, jurl)
            if title and jurl:
                jobs.append({"title": title, "url": jurl, "location": loc, "source": "rippling", "posted": ""})
    else:
        jobs, algolia_err = _fetch_rippling_algolia(scrape)
        if algolia_err and not jobs:
            return [], algolia_err
    detail_targets = []
    refresh_experience = bool(scrape.get("refresh_experience"))
    fetch_details = bool(scrape.get("fetch_experience_details") or scrape.get("fetch_details"))
    if fetch_details:
        for job in jobs:
            jid_key = make_id(company, job["url"], job["title"])
            existing = seen.get(jid_key) if isinstance(seen, dict) else None
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
