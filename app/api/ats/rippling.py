from .base import *

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


def fetch_rippling(scrape, seen, company):
    list_url = scrape.get("list_url") or "https://www.rippling.com/careers/open-roles"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        return [], str(e)
    soup = BeautifulSoup(r.text, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return [], "no __NEXT_DATA__ jobs payload"
    try:
        data = json.loads(script.string)
    except Exception as e:
        return [], str(e)
    items = (((data.get("props") or {}).get("pageProps") or {}).get("jobs") or {}).get("items") or []
    jobs = []
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
    return jobs, None

