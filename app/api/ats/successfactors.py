from .base import *

# ── SuccessFactors ────────────────────────────────────────────────────────────

_SF_DESC_SELECTORS = [
    '.jobDescription', '#jobDescription', '[class*="job-description"]',
    '[class*="jobDescription"]', '[class*="job_description"]',
    '.job-content', '.jobContent', '.job_content',
    'main', 'article',
]

def _sf_detail_location(text):
    if not text:
        return ""

    def _location_list(raw):
        locs = []
        patterns = [
            r'\b([A-Z][A-Za-z .\'-]+,\s*[A-Z]{2},\s*(?:US|USA|United States(?: of America)?))\b',
            r'\b(Remote,\s*(?:US|USA|United States(?: of America)?|Canada|India|United Kingdom|UK))\b',
            r'\b([A-Z][A-Za-z .\'-]+,\s*[A-Z][A-Za-z .\'-]+,\s*(?:Canada|India|United Kingdom|UK|Germany|France|Spain))\b',
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, raw):
                candidate = normalize_location(m.group(1))
                if candidate and candidate != "Multiple Locations" and candidate.lower() not in {x.lower() for x in locs}:
                    locs.append(candidate)
        return "; ".join(locs)

    m = re.search(r'\bSame job available in\s+\d+\s+locations?\s+(.+?)(?=\s+(?:Apply|Back to search|Share this job|Similar jobs|$))', text, re.I)
    if m:
        raw = m.group(1)
        listed = _location_list(raw)
        if listed:
            return listed
        parts = [p.strip() for p in re.split(r'\s*[;|•]\s*|\s{2,}', raw) if p.strip()]
        locs = []
        for part in parts:
            candidate = normalize_location(part)
            if candidate and candidate != "Multiple Locations" and candidate.lower() not in {x.lower() for x in locs}:
                locs.append(candidate)
        if locs:
            return "; ".join(locs)
    m = re.search(r'\bLocation(?:\(s\))?:\s*(.+?)(?=\s+(?:Job Description|Responsibilities|Qualifications|Apply|$))', text, re.I)
    if m:
        listed = _location_list(m.group(1))
        if listed:
            return listed
        return normalize_location(m.group(1))
    return ""


def _fetch_sf_detail(jurl, timeout=15):
    """Fetch description text and expanded location from a SuccessFactors detail page."""
    try:
        try:
            r = requests.get(jurl, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
        except requests.exceptions.SSLError:
            r = requests.get(jurl, headers=HEADERS, timeout=timeout, verify=REQUESTS_VERIFY_SSL)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for sel in _SF_DESC_SELECTORS:
            el = soup.select_one(sel)
            if el:
                text = norm(el.get_text(" ", strip=True))
                if len(text) > 100:
                    return {
                        "description": str(el),
                        "location": _sf_detail_location(text),
                    }
        text = norm(soup.get_text(" ", strip=True))
        return {
            "description": str(soup.body or soup) if len(text) > 100 else "",
            "location": _sf_detail_location(text),
        }
    except Exception:
        return {"description": "", "location": ""}


def _fetch_sf_description(jurl, timeout=15):
    return _fetch_sf_detail(jurl, timeout).get("description", "")


def fetch_successfactors(scrape, seen, company):
    """SAP SuccessFactors/RMK static search pages."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    max_pages = int(scrape.get("hard_max_pages") or 100)
    jobs = {}
    stop_on_seen = bool(scrape.get("stop_on_seen"))
    consecutive_seen_pages = 0

    for idx in range(max_pages):
        purl = list_url if idx == 0 else make_paged_url(list_url, {"param": "startrow", "start": idx * 25}, 0)
        try:
            try:
                r = requests.get(purl, headers=HEADERS, timeout=20)
                r.raise_for_status()
            except requests.exceptions.SSLError:
                r = requests.get(purl, headers=HEADERS, timeout=20, verify=REQUESTS_VERIFY_SSL)
                r.raise_for_status()
        except Exception as e:
            return list(jobs.values()), str(e)
        soup = BeautifulSoup(r.text, "lxml")
        before = len(jobs)
        page_seen = []
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
            loc_raw = norm(loc_el.get_text(" ", strip=True)) if loc_el else ""
            loc = normalize_location(loc_raw) if loc_raw else ""
            loc = loc or extract_location_from_text(parent_text)
            if not loc:
                path_bits = [unquote(x) for x in urlparse(jurl).path.split("/") if x]
                loc = extract_location_from_text(" ".join(path_bits)) or ""
            posted = extract_posted_from_text(parent_text)
            if len(title) >= 4:
                query_id = (parse_qs(urlparse(jurl).query).get("career_job_req_id") or [None])[0]
                path_id = re.search(r"/(?:job|jobs)/[^/?#]*/?(\d+)(?:/|$)", urlparse(jurl).path)
                ats_id = query_id or (path_id.group(1) if path_id else None)
                stable_id = make_id(
                    company,
                    jurl,
                    title,
                    "successfactors",
                    ats_id,
                )
                legacy_id = make_id(company, jurl, title)
                page_seen.append(
                    stable_id in seen or legacy_id in seen
                )
                jobs[jurl] = {
                    "title": title,
                    "url": jurl,
                    "ats_id": ats_id,
                    "location": loc,
                    "location_raw": loc_raw or loc or None,
                    "locations_raw": [loc] if loc else [],
                    "source": "successfactors",
                    "posted": posted or None,
                }
        if len(jobs) == before:
            break
        if stop_on_seen and page_seen:
            consecutive_seen_pages = (
                consecutive_seen_pages + 1
                if all(page_seen)
                else 0
            )
            if consecutive_seen_pages >= 2:
                break
    if not jobs and scrape.get("allow_browser_fallback") is not False and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "successfactors")

    # Fetch descriptions for jobs that don't have them yet
    max_desc = (
        int(scrape.get("max_fresh_desc_details") or 0)
        if stop_on_seen
        else int(scrape.get("max_desc_details", 500))
    )
    desc_workers = int(scrape.get("desc_workers", 4))
    desc_targets = []
    for j in jobs.values():
        jid_key = make_id(
            company, j["url"], j["title"], "successfactors", j.get("ats_id")
        )
        existing = (
            seen.get(jid_key)
            or seen.get(make_id(company, j["url"], j["title"]))
            if isinstance(seen, dict) else None
        )
        incomplete_location = (not j.get("location")) or re.search(r'\+\s*\d+\s+more', j.get("location") or "", re.I)
        if incomplete_location or not (isinstance(existing, dict) and existing.get("has_description")):
            desc_targets.append(j)
    if desc_targets[:max_desc]:
        def _get_desc(j):
            return j, _fetch_sf_detail(j["url"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, desc_workers)) as ex:
            for job, detail in ex.map(_get_desc, desc_targets[:max_desc]):
                if detail.get("location"):
                    job["location"] = detail["location"]
                    job["location_raw"] = detail["location"]
                    job["locations_raw"] = [
                        part.strip()
                        for part in detail["location"].split(";")
                        if part.strip()
                    ]
                if detail.get("description"):
                    job["description"] = detail["description"]
                    job["description_raw"] = detail["description"]

    return list(jobs.values()), None
