from .base import *

# ── SuccessFactors ────────────────────────────────────────────────────────────

_SF_DESC_SELECTORS = [
    '.jobDescription', '#jobDescription', '[class*="job-description"]',
    '[class*="jobDescription"]', '[class*="job_description"]',
    '.job-content', '.jobContent', '.job_content',
    'main', 'article',
]

def _fetch_sf_description(jurl, timeout=15):
    """Fetch description text from a SuccessFactors job detail page."""
    try:
        try:
            r = requests.get(jurl, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
        except requests.exceptions.SSLError:
            r = requests.get(jurl, headers=HEADERS, timeout=timeout, verify=False)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for sel in _SF_DESC_SELECTORS:
            el = soup.select_one(sel)
            if el:
                text = norm(el.get_text(" ", strip=True))
                if len(text) > 100:
                    return text
        return ""
    except Exception:
        return ""


def fetch_successfactors(scrape, seen, company):
    """SAP SuccessFactors/RMK static search pages."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    max_pages = int(scrape.get("hard_max_pages") or 100)
    jobs = {}

    for idx in range(max_pages):
        purl = list_url if idx == 0 else make_paged_url(list_url, {"param": "startrow", "start": idx * 25}, 0)
        try:
            try:
                r = requests.get(purl, headers=HEADERS, timeout=20)
                r.raise_for_status()
            except requests.exceptions.SSLError:
                r = requests.get(purl, headers=HEADERS, timeout=20, verify=False)
                r.raise_for_status()
        except Exception as e:
            return list(jobs.values()), str(e)
        soup = BeautifulSoup(r.text, "lxml")
        before = len(jobs)
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            # Match both /job/ path links AND direct SF host links with career_job_req_id=
            if "/job/" not in href and "/jobs/" not in href and "career_job_req_id=" not in href:
                continue
            title = norm(a.get_text(" ", strip=True))
            _NAV_TITLES = {"view job", "apply", "search jobs", "jobs", "careers",
                           "all jobs", "search", "home", "back", "next", "previous"}
            if not title or title.lower() in _NAV_TITLES or len(title) < 5:
                continue
            jurl = urljoin(r.url, href)
            # Skip generic landing pages (no job ID — just a jobs directory)
            path = urlparse(jurl).path.rstrip("/")
            if path in ("/jobs", "/job", "/careers", "/en/jobs", "/us/en/jobs"):
                continue
            row = a.find_parent("tr") or a.find_parent("li") or a.find_parent("div")
            parent_text = norm(row.get_text(" ", strip=True)) if row else title
            loc_el = row.select_one(".jobLocation") if row else None
            loc = normalize_location(norm(loc_el.get_text(" ", strip=True))) if loc_el else ""
            loc = loc or extract_location_from_text(parent_text)
            if not loc:
                path_bits = [unquote(x) for x in urlparse(jurl).path.split("/") if x]
                loc = extract_location_from_text(" ".join(path_bits)) or ""
            posted = extract_posted_from_text(parent_text)
            if len(title) >= 4:
                jobs[jurl] = {"title": title, "url": jurl, "location": loc, "source": "successfactors", "posted": posted}
        if len(jobs) == before:
            break
    if not jobs and scrape.get("allow_browser_fallback") is not False and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "successfactors")

    # Fetch descriptions for jobs that don't have them yet
    max_desc = int(scrape.get("max_desc_details", 500))
    desc_workers = int(scrape.get("desc_workers", 4))
    desc_targets = []
    for j in jobs.values():
        jid_key = make_id(company, j["url"], j["title"])
        existing = seen.get(jid_key) if isinstance(seen, dict) else None
        if not (isinstance(existing, dict) and existing.get("has_description")):
            desc_targets.append(j)
    if desc_targets[:max_desc]:
        def _get_desc(j):
            return j, _fetch_sf_description(j["url"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, desc_workers)) as ex:
            for job, desc in ex.map(_get_desc, desc_targets[:max_desc]):
                if desc:
                    job["description"] = desc

    return list(jobs.values()), None
