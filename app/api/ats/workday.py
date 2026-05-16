from .base import *

# ── Workday ───────────────────────────────────────────────────────────────────

def _workday_location_parts(raw):
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        for keys in (
            ("city", "region", "country"),
            ("city", "state", "country"),
            ("city", "countryRegion", "country"),
            ("descriptor",),
            ("location",),
            ("name",),
        ):
            vals = []
            for k in keys:
                value = raw.get(k)
                if isinstance(value, dict):
                    value = value.get("descriptor") or value.get("alpha2Code") or value.get("id")
                if value:
                    vals.append(value)
            if vals:
                return [", ".join(str(v) for v in vals)]
        return []
    if isinstance(raw, list):
        parts = []
        for item in raw:
            parts.extend(_workday_location_parts(item))
        return parts
    return []

def _workday_location(posting):
    parts = []
    for key in ("locations", "location", "jobLocations"):
        parts.extend(_workday_location_parts(posting.get(key)))
    if not parts:
        loc_text = posting.get("locationsText") or ""
        if loc_text:
            parts = re.split(r'\s*(?:;|\||/|\band\b)\s*', loc_text)
    if not parts:
        bullets = posting.get("bulletFields") or []
        for bullet in bullets:
            if not bullet:
                continue
            raw_bullet = norm(str(bullet))
            if re.fullmatch(r'(?:ATCI[-\w]*|(?:REQ|JR|WD|EB|R|S)[-_]?\d[\w-]*|\d{5,})', raw_bullet, re.I):
                continue
            loc = normalize_location(norm(str(bullet)))
            if loc:
                parts = [bullet]
                break

    normalized = []
    seen_locs = set()
    for part in parts:
        loc = normalize_location(norm(str(part)))
        if not loc:
            continue
        key = loc.lower()
        if key not in seen_locs:
            normalized.append(loc)
            seen_locs.add(key)

    if not normalized:
        return ""
    if len(normalized) == 1:
        return normalized[0]
    return "; ".join(normalized[:8])

def _workday_detail_location(info):
    if not isinstance(info, dict):
        return ""
    country = ""
    country_obj = info.get("country")
    if isinstance(country_obj, dict):
        country = country_obj.get("descriptor") or ""
    elif isinstance(country_obj, str):
        country = country_obj

    parts = []
    parts.extend(_workday_location_parts(info.get("location")))
    parts.extend(_workday_location_parts(info.get("jobRequisitionLocation")))
    parts.extend(_workday_location_parts(info.get("additionalLocations")))

    normalized = []
    seen_locs = set()
    for part in parts:
        raw = norm(str(part))
        has_country_suffix = re.search(r'\b(?:US|USA|United States(?: of America)?|[A-Z]{2})(?:\s+\d{2,6})?$', raw, re.I)
        if country and "," not in raw and raw.lower() not in ("remote", "multiple locations") and not has_country_suffix:
            raw = f"{raw}, {country}"
        elif country and raw.lower() == "remote":
            raw = f"Remote, {country}"
        loc = normalize_location(raw)
        if len(parts) > 1 and loc in ("US", "UK", "Canada", "India", "Australia", "Germany", "France"):
            continue
        if loc and loc != "Multiple Locations" and loc.lower() not in seen_locs:
            normalized.append(loc)
            seen_locs.add(loc.lower())
    return "; ".join(normalized[:8])

def _fetch_workday_detail(base_url, tenant, board, path):
    if not path:
        return {"posted": "", "location": "", "description": ""}
    detail_url = f"{base_url}/wday/cxs/{tenant}/{board}{path}"
    try:
        r = None
        for attempt in range(3):
            r = requests.get(
                detail_url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=20,
            )
            if r.status_code not in (403, 429):
                break
            time.sleep(1.5 * (attempt + 1))
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {"posted": "", "location": "", "description": ""}

    info = data.get("jobPostingInfo") if isinstance(data, dict) else {}
    loc = _workday_detail_location(info)
    desc = ""
    if isinstance(info, dict):
        desc = norm(BeautifulSoup(info.get("jobDescription") or "", "lxml").get_text(" ", strip=True))
    if isinstance(info, dict):
        posted = _parse_posted(info.get("postedOn") or info.get("postedDate") or info.get("startDate") or "")
        if posted:
            return {"posted": posted, "location": loc, "description": desc}
    return {"posted": _posted_from_json(data), "location": loc, "description": desc}

def fetch_workday(scrape, seen, company):
    """
    Workday JSON API — much faster and more reliable than Playwright for Workday sites.
    Parses tenant/board from list_url: https://{tenant}.wd{n}.myworkdayjobs.com[/en-US]/{board}
    """
    list_url = scrape.get("list_url","")
    if not list_url:
        return [], "no list_url"

    m = re.match(
        r'https://([^.]+)\.(wd\d+\.myworkdayjobs\.com)(?:/[a-z]{2}-[A-Z]{2})?/?([^/?#]*)',
        list_url,
    )
    public_base = None
    if not m:
        mysite = re.match(
            r'https://(wd\d+\.myworkdaysite\.com)/recruiting/([^/]+)/([^/?#]+)',
            list_url,
        )
        if mysite:
            wd_domain = mysite.group(1)
            tenant = mysite.group(2)
            board = mysite.group(3)
            base_url = f"https://{wd_domain}"
            api_url = f"{base_url}/wday/cxs/{tenant}/{board}/jobs"
            public_base = f"{base_url}/recruiting/{tenant}/{board}"
            m = True
    if not m:
        # Some companies use Workday for apply but expose public listings on a
        # custom careers domain. Keep the source as workday while scraping the
        # public listing page.
        if scrape.get("fallback") == "bankofamerica":
            from .bankofamerica import fetch_bankofamerica
            return fetch_bankofamerica(scrape, seen, company)
        if scrape.get("fallback") == "html":
            from .html import fetch_html
            jobs, err = fetch_html({**scrape, "mode": "html", "include": scrape.get("include") or scrape.get("prefix") or list_url}, seen, company)
            for j in jobs or []:
                j["source"] = "workday"
            return jobs, err
        if scrape.get("fallback") == "playwright":
            from .playwright import _fetch_playwright_with_source
            return _fetch_playwright_with_source(scrape, seen, company, "workday")
        return [], f"cannot parse Workday URL: {list_url}"

    if public_base is None:
        tenant    = m.group(1)
        wd_domain = m.group(2)
        board     = m.group(3) or tenant

        api_url  = f"https://{tenant}.{wd_domain}/wday/cxs/{tenant}/{board}/jobs"
        base_url = f"https://{tenant}.{wd_domain}"
        public_base = f"{base_url}/{board}"

    # Workday's public API paginates with a hard 20-row page size. Older
    # configs often set max_pages=30 as a safety cap, which silently limited
    # large companies. By default, scan until Workday's total says we are done.
    max_pages       = int(scrape.get("hard_max_pages") or 10000)
    limit           = 20
    jobs            = []
    total           = None
    consec_all_seen = 0
    fetch_details   = bool(scrape.get("fetch_posted_details", False))
    fetch_exp_detail = bool(scrape.get("fetch_experience_details", True))
    detail_limit    = int(scrape.get("max_posted_details", 1000000))
    detail_delay    = float(scrape.get("posted_detail_delay", 0.05))
    detail_workers  = int(scrape.get("detail_workers", 3))
    detail_count    = 0

    for idx in range(max_pages):
        offset = idx * limit
        try:
            r = None
            for attempt in range(3):
                r = requests.post(
                    api_url,
                    json={"appliedFacets":{},"limit":limit,"offset":offset,"searchText":""},
                    headers={**HEADERS, "Content-Type":"application/json", "Accept":"application/json"},
                    timeout=25,
                )
                if r.status_code not in (403, 429):
                    break
                time.sleep(2.0 * (attempt + 1))
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            if jobs: break
            return [], str(e)

        postings = data.get("jobPostings",[])
        if not postings:
            break
        if total is None:
            total = data.get("total", 0)

        page_jobs = []
        page_had_missing_posted = False
        for j in postings:
            title  = norm(j.get("title") or "")
            path   = j.get("externalPath") or ""
            jurl   = f"{public_base}{path}" if path else ""
            loc    = _workday_location(j)
            posted = _parse_posted(j.get("postedOn") or j.get("postedDate") or "")
            if title:
                jid = make_id(company, jurl, title)
                existing = seen.get(jid) if isinstance(seen, dict) else None
                if not posted and isinstance(existing, dict):
                    posted = _parse_posted(existing.get("posted") or "")
                existing_exp = isinstance(existing, dict) and bool(existing.get("exp_label"))
                needs_detail = (fetch_exp_detail and not existing_exp) or (not posted) or loc == "Multiple Locations"
                if needs_detail and fetch_details and detail_count < detail_limit:
                    page_had_missing_posted = True
                elif not posted:
                    page_had_missing_posted = True
                page_jobs.append({
                    "title": title,
                    "url": jurl,
                    "location": loc,
                    "source": "workday",
                    "posted": posted,
                    "description": "",
                    "_detail_path": path if needs_detail and fetch_details else "",
                })

        detail_targets = [
            (pos, job) for pos, job in enumerate(page_jobs)
            if job.get("_detail_path") and detail_count + pos < detail_limit
        ]
        if detail_targets:
            def _fetch_target(item):
                pos, job = item
                detail = _fetch_workday_detail(base_url, tenant, board, job["_detail_path"])
                return pos, detail

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
                for pos, detail in ex.map(_fetch_target, detail_targets):
                    job = page_jobs[pos]
                    job["posted"] = job.get("posted") or detail.get("posted", "")
                    if detail.get("location"):
                        job["location"] = detail["location"]
                    job["description"] = detail.get("description", "")
            detail_count += len(detail_targets)
            if detail_delay:
                time.sleep(detail_delay)
        for job in page_jobs:
            job.pop("_detail_path", None)
        jobs.extend(page_jobs)

        if bool(scrape.get("stop_on_seen", False) or scrape.get("sort", False)) and seen and page_jobs:
            all_seen = all(make_id(company, j["url"], j["title"]) in seen for j in page_jobs)
            consec_all_seen = consec_all_seen + 1 if all_seen else 0
            backfilling_dates = fetch_details and page_had_missing_posted and detail_count < detail_limit
            if consec_all_seen >= 2 and not backfilling_dates:
                break

        if total and offset + limit >= total:
            break
        time.sleep(0.2)

    return jobs, None
