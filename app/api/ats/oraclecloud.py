from .base import *
from ..utils import extract_experience

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
    parts = []
    for key, heading in (
        ("ExternalQualificationsStr", "Qualifications"),
        ("ExternalResponsibilitiesStr", "Responsibilities"),
        ("ExternalDescriptionStr", "Description"),
        ("ShortDescriptionStr", "Summary"),
    ):
        if data.get(key):
            parts.append(f"<h3>{heading}</h3>\n{data[key]}")
    return "\n\n".join(parts)

def _oraclecloud_list_text(job):
    """Text Oracle already embeds in the search response."""
    parts = []
    for key, heading in (
        ("ExternalQualificationsStr", "Qualifications"),
        ("ExternalResponsibilitiesStr", "Responsibilities"),
        ("ExternalDescriptionStr", "Description"),
        ("ShortDescriptionStr", "Summary"),
    ):
        if job.get(key):
            parts.append(f"<h3>{heading}</h3>\n{job[key]}")
    return "\n\n".join(parts)


def _oraclecloud_text_has_numeric_experience(title, text):
    if not text:
        return False
    exp = extract_experience(title, text, allow_title_fallback=False)
    return exp.get("min") is not None or exp.get("max") is not None


def _oraclecloud_cached_detail_is_sufficient(existing, refresh_experience=False):
    if not isinstance(existing, dict):
        return False
    source = existing.get("exp_source") or ""
    if source == "oracle_detail":
        return True
    has_numeric_exp = (
        existing.get("exp_min") is not None
        or existing.get("exp_max") is not None
    )
    return (
        has_numeric_exp
        and not refresh_experience
        and source in {"text"}
    )

def fetch_oraclecloud(scrape, seen, company):
    """Oracle Cloud Candidate Experience public API."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    parsed = urlparse(list_url)
    host = parsed.netloc
    site = scrape.get("site") or scrape.get("site_number") or _oraclecloud_site_from_url(list_url)
    if not host or not site:
        if scrape.get("allow_browser_fallback") is not False and (scrape.get("prefix") or scrape.get("include")):
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
    total = 0
    api_host     = scrape.get("api_host") or host  # may be replaced by real backend if custom domain blocks REST API
    vanity_domain = scrape.get("vanity_domain") or ""
    page = 0
    while page < max_pages:
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
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
        page_total = 0
        for item in search_items:
            page_total = max(page_total, int(item.get("TotalJobsCount") or 0))
            requisitions.extend(item.get("requisitionList") or [])
        total = max(total, page_total)
        if not requisitions:
            break

        job_url_prefix = scrape.get("job_url_prefix", "").rstrip("/")
        descriptions = {}
        detail_ids = set()
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
                list_desc = _oraclecloud_list_text(j)
                if list_desc:
                    descriptions[jid] = list_desc
                    # Search responses can contain a long responsibilities
                    # summary while omitting the qualifications section. Only
                    # skip the detail request when the list text itself states
                    # a numeric experience requirement.
                    if _oraclecloud_text_has_numeric_experience(title, list_desc):
                        continue
                existing = (
                    seen.get(make_id(company, jurl, title, "oraclecloud", jid))
                    or seen.get(make_id(company, jurl, title))
                    if isinstance(seen, dict) else None
                )
                if (
                    fetch_new_exp_only
                    and isinstance(existing, dict)
                    and not refresh_experience
                ):
                    continue
                # A short Oracle search-card summary is not a full description.
                # Numeric cached results and the oracle_detail marker avoid
                # refetching full descriptions on later scans.
                if not _oraclecloud_cached_detail_is_sufficient(existing, refresh_experience):
                    ids.append(jid)

            detail_limit = int(scrape.get("max_experience_details") or 1000000)
            ids = ids[:detail_limit]

            def _fetch_desc(req_id):
                return req_id, _oraclecloud_detail_text(api_host, req_id, vanity_domain)

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
                for req_id, desc in ex.map(_fetch_desc, ids):
                    descriptions[req_id] = desc
                    if desc:
                        detail_ids.add(req_id)

        for j in requisitions:
            title = norm(j.get("Title") or "")
            jid = str(j.get("Id") or "").strip()
            if job_url_prefix:
                jurl = f"{job_url_prefix}/{jid}" if jid else ""
            else:
                jurl = f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}" if jid else ""
            loc_raw = norm(j.get("PrimaryLocation") or j.get("PrimaryLocationCountry") or "")
            posted_raw = j.get("PostedDate")
            if title and jurl:
                desc = descriptions.get(jid) or _oraclecloud_list_text(j)
                jobs.append({
                    "title": title,
                    "url": jurl,
                    "ats_id": jid or None,
                    "requisition_id": (
                        str(j.get("RequisitionNumber") or j.get("JobCode") or "")
                        or None
                    ),
                    "location": loc_raw,
                    "location_raw": loc_raw or None,
                    "source": "oraclecloud",
                    "posted_at": posted_raw,
                    "description": desc,
                    "description_raw": desc,
                    "description_is_detail": jid in detail_ids,
                    "employment_type": j.get("WorkerType") or j.get("JobType"),
                    "employment_type_raw": j.get("WorkerType") or j.get("JobType"),
                    "department": j.get("JobFamily"),
                    "raw_data": {
                        key: j.get(key)
                        for key in (
                            "PrimaryLocation", "PrimaryLocationCountry",
                            "RequisitionNumber", "JobCode", "WorkerType",
                            "JobType", "JobFamily",
                        )
                        if j.get(key)
                    } or None,
                })

        if total and offset + limit >= total:
            break
        if len(requisitions) < limit:
            break
        page += 1
    unique_jobs = {}
    for job in jobs:
        unique_jobs[str(job.get("ats_id") or job.get("url") or "")] = job

    # Oracle caps a single sorted result window at 10,000 even when
    # TotalJobsCount is larger. Query the opposite date ordering and merge the
    # two windows so very large boards can still be collected completely.
    if scrape.get("complete_over_10000") and total > len(unique_jobs):
        opposite_sort = (
            "POSTING_DATES_ASC"
            if sort_by == "POSTING_DATES_DESC"
            else "POSTING_DATES_DESC"
        )
        supplement_cfg = dict(scrape)
        supplement_cfg["sort_by"] = opposite_sort
        supplement_cfg["complete_over_10000"] = False
        supplement_cfg["hard_max_pages"] = max_pages
        supplement, supplement_err = fetch_oraclecloud(supplement_cfg, seen, company)
        for job in supplement:
            key = str(job.get("ats_id") or job.get("url") or "")
            unique_jobs.setdefault(key, job)
        merged = list(unique_jobs.values())
        if len(merged) < total:
            detail = f"partial Oracle listing: fetched {len(merged)} of {total} jobs"
            if supplement_err:
                detail += f"; {supplement_err}"
            return merged, detail
        return merged[:total], None

    return list(unique_jobs.values()), None

def fetch_oracle(scrape, seen, company):
    list_url = scrape.get("list_url", "")
    if "/hcmUI/CandidateExperience/" in list_url or "/hcmRestApi/" in list_url:
        return fetch_oraclecloud(scrape, seen, company)
    if scrape.get("allow_browser_fallback") is not False:
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "oracle")
    return [], f"unsupported Oracle URL without browser fallback: {list_url}"
