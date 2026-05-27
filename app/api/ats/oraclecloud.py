from .base import *

# ── Oracle Cloud ──────────────────────────────────────────────────────────────

def _oraclecloud_site_from_url(list_url):
    m = re.search(r"/sites/([^/?#]+)/jobs", list_url)
    return m.group(1) if m else ""

def _oraclecloud_discover_backend(custom_host, site, timeout=12):
    """
    Some companies host Oracle Cloud on a custom domain (e.g. jobs.akamai.com)
    where the REST API is blocked. Fetch the careers page HTML and look for the
    real fa-*.oraclecloud.com backend hostname embedded in the page source.
    """
    for path in [
        f"/hcmUI/CandidateExperience/en/sites/{site}/jobs",
        "/hcmUI/CandidateExperience/en/sites/CX_1/jobs",
        "/",
    ]:
        try:
            r = requests.get(
                f"https://{custom_host}{path}",
                headers=HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            m = re.search(r'(fa-[^/"\']+\.fa\.[^/"\']*\.oraclecloud\.com)', r.text)
            if m:
                return m.group(1)
        except Exception:
            pass
    return None

def _oraclecloud_detail_text(api_host, req_id, vanity_domain="", timeout=20):
    if not req_id:
        return ""
    hdrs = {**HEADERS, "Accept": "application/json"}
    if vanity_domain:
        hdrs["ora-irc-vanity-domain"] = vanity_domain
    try:
        r = None
        for attempt in range(3):
            r = requests.get(
                f"https://{api_host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails/{req_id}",
                headers=hdrs,
                timeout=timeout,
            )
            if r.status_code not in (403, 429):
                break
            time.sleep(1.5 * (attempt + 1))
        r.raise_for_status()
        data = r.json()
    except Exception:
        return ""
    parts = [
        data.get("ExternalQualificationsStr") or "",
        data.get("ExternalResponsibilitiesStr") or "",
        data.get("ExternalDescriptionStr") or "",
        data.get("ShortDescriptionStr") or "",
    ]
    return norm(BeautifulSoup(" ".join(parts), "lxml").get_text(" ", strip=True))

def fetch_oraclecloud(scrape, seen, company):
    """Oracle Cloud Candidate Experience public API."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    parsed = urlparse(list_url)
    host = parsed.netloc
    site = scrape.get("site") or scrape.get("site_number") or _oraclecloud_site_from_url(list_url)
    if not host or not site:
        if scrape.get("allow_browser_fallback") and (scrape.get("prefix") or scrape.get("include")):
            from .playwright import _fetch_playwright_with_source
            return _fetch_playwright_with_source(scrape, seen, company, "oraclecloud")
        return [], f"cannot parse Oracle Cloud URL: {list_url}"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    limit = int(scrape.get("limit", 100))
    sort_by = scrape.get("sort_by", "POSTING_DATES_DESC")
    fetch_exp_detail = bool(scrape.get("fetch_experience_details", True))
    fetch_new_exp_only = bool(scrape.get("fetch_new_experience_only", False))
    refresh_experience = bool(scrape.get("refresh_experience", False))
    detail_workers = int(scrape.get("detail_workers", 3))
    jobs = []
    api_host     = host  # may be replaced by real backend if custom domain blocks REST API
    vanity_domain = scrape.get("vanity_domain") or ""
    page = 0
    while page < max_pages:
        offset = page * limit
        finder = f"findReqs;siteNumber={site},limit={limit},offset={offset},sortBy={sort_by}"
        # Pass finder inline to avoid requests URL-encoding the semicolons (Oracle rejects %3B)
        api_url = (f"https://{api_host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
                   f"?finder={finder}&expand=requisitionList")
        hdrs = {**HEADERS, "Accept": "application/json"}
        if vanity_domain:
            hdrs["ora-irc-vanity-domain"] = vanity_domain
        try:
            r = None
            for attempt in range(3):
                r = requests.get(api_url, headers=hdrs, timeout=25)
                if r.status_code not in (403, 429):
                    break
                time.sleep(2.0 * (attempt + 1))
            # Custom domains often redirect the REST API to a 404 page.
            # Detect this and fall back to discovering the real backend hostname.
            # Use continue WITHOUT incrementing page so we retry page 0 with the real host.
            if r.status_code in (404, 405) or "json" not in r.headers.get("Content-Type", ""):
                if api_host == host:
                    backend = _oraclecloud_discover_backend(host, site)
                    if backend:
                        api_host = backend
                        continue  # retry this page with the real host (page not incremented)
                break
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)

        search_items = data.get("items") or []
        requisitions = []
        total = 0
        for item in search_items:
            total = max(total, int(item.get("TotalJobsCount") or 0))
            requisitions.extend(item.get("requisitionList") or [])
        if not requisitions:
            break

        job_url_prefix = scrape.get("job_url_prefix", "").rstrip("/")
        descriptions = {}
        if fetch_exp_detail:
            ids = []
            for j in requisitions:
                jid = str(j.get("Id") or "").strip()
                title = norm(j.get("Title") or "")
                if not jid or not title:
                    continue
                if job_url_prefix:
                    jurl = f"{job_url_prefix}/{jid}"
                else:
                    jurl = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}"
                existing = seen.get(make_id(company, jurl, title)) if isinstance(seen, dict) else None
                if fetch_new_exp_only and isinstance(existing, dict):
                    continue
                has_good_existing_exp = (
                    isinstance(existing, dict)
                    and existing.get("exp_label")
                    and not refresh_experience
                )
                if not has_good_existing_exp:
                    ids.append(jid)

            def _fetch_desc(req_id):
                return req_id, _oraclecloud_detail_text(api_host, req_id, vanity_domain)

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
                for req_id, desc in ex.map(_fetch_desc, ids):
                    descriptions[req_id] = desc

        for j in requisitions:
            title = norm(j.get("Title") or "")
            jid = str(j.get("Id") or "").strip()
            if job_url_prefix:
                jurl = f"{job_url_prefix}/{jid}" if jid else ""
            else:
                jurl = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}" if jid else ""
            loc = normalize_location(norm(j.get("PrimaryLocation") or j.get("PrimaryLocationCountry") or ""))
            posted = _parse_posted(j.get("PostedDate") or "")
            if title and jurl:
                desc = descriptions.get(jid, "")
                jobs.append({"title": title, "url": jurl, "location": loc, "source": "oraclecloud", "posted": posted, "description": desc})

        if total and offset + limit >= total:
            break
        if len(requisitions) < limit:
            break
        page += 1
    return jobs, None

def fetch_oracle(scrape, seen, company):
    list_url = scrape.get("list_url", "")
    if "/hcmUI/CandidateExperience/" in list_url or "/hcmRestApi/" in list_url:
        return fetch_oraclecloud(scrape, seen, company)
    if scrape.get("allow_browser_fallback"):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "oracle")
    return [], f"unsupported Oracle URL without browser fallback: {list_url}"
