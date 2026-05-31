from .base import *

# ── Avature ───────────────────────────────────────────────────────────────────

def fetch_avature(scrape, seen, company):
    """Avature public search pages, usually /careers/SearchJobs."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    page_size = int(scrape.get("page_size", 12))
    fetch_details = bool(scrape.get("fetch_posted_details", False))
    detail_limit = int(scrape.get("max_posted_details", 1000000))
    offset_param = scrape.get("offset_param", "jobOffset")
    size_param   = scrape.get("size_param",   "jobRecordsPerPage")
    jobs = {}

    for page in range(max_pages):
        purl = list_url
        if page:
            purl = make_paged_url(list_url, {"param": size_param, "start": page_size}, 0)
            purl = make_paged_url(purl, {"param": offset_param, "start": page * page_size}, 0)
        try:
            r = requests.get(purl, headers=HEADERS, timeout=25)
            r.raise_for_status()
        except Exception as e:
            return list(jobs.values()), str(e)

        soup = BeautifulSoup(r.text, "lxml")
        page_count = len(jobs)
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            if "/JobDetail/" not in href:
                continue
            jurl = urljoin(r.url, href)
            article = a.find_parent("article") or a.find_parent("li") or a.find_parent("div")
            title = norm(a.get_text(" ", strip=True))
            parent_text = norm(article.get_text(" ", strip=True)) if article else title
            generic_link = title.lower() in (
                "apply", "save", "view job", "view role", "learn more", "more information",
            )
            if generic_link and jurl in jobs:
                continue
            if not title or generic_link:
                heading = article.find(["h2", "h3", "h4"]) if article else None
                title = norm(heading.get_text(" ", strip=True)) if heading else ""
                if not title:
                    m = re.search(r"/JobDetail/([^/?#]+)/?", jurl)
                    if m:
                        title = norm(re.sub(r"[-_]+", " ", unquote(m.group(1))).strip().title())
            parent_text = re.sub(r"\b(Apply|Save|Bookmark|Share|View Job)\b", " ", parent_text, flags=re.I)
            loc_text = parent_text.replace(title, "", 1).strip()
            # Avature listing format: "City, Region, Country • Job ID: 123 • Field" — strip metadata
            loc_text = re.sub(r'\s*•\s*Job\s+ID\b.*', '', loc_text, flags=re.I).strip()
            loc = normalize_location(loc_text) if loc_text.count(",") >= 2 else ""
            loc = loc or extract_location_from_text(loc_text) or normalize_location(loc_text)
            # Strip any title-word fragments that leaked into the location
            # (e.g. "Credit Karma Bangalore, India" when "Credit Karma" is a title suffix)
            if loc and title:
                title_words = {w.lower() for w in re.findall(r'\b[A-Z][a-z]+\b', title) if len(w) > 3}
                loc_words = loc.split(",")
                if loc_words and all(w.strip().lower() in title_words for w in loc_words[0].split()):
                    loc = ",".join(loc_words[1:]).strip()
            posted = extract_posted_from_text(parent_text)
            if title and len(title) >= 4:
                jobs[jurl] = {"title": title, "url": jurl, "location": loc, "source": "avature", "posted": posted}
        if len(jobs) == page_count:
            break
    if not jobs and scrape.get("allow_browser_fallback") is not False and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "avature")
    # Fetch detail pages for jobs missing posted date or description
    max_desc = int(scrape.get("max_desc_details", 500))
    detail_workers = int(scrape.get("detail_workers", 6))
    targets = []
    for job in jobs.values():
        jid = make_id(company, job["url"], job["title"])
        existing = seen.get(jid) if isinstance(seen, dict) else None
        if not job.get("posted") and isinstance(existing, dict):
            job["posted"] = _parse_posted(existing.get("posted") or "")
        existing_has_desc = isinstance(existing, dict) and existing.get("has_description")
        needs_posted = fetch_details and not job.get("posted") and len(targets) < detail_limit
        needs_desc = not existing_has_desc and len(targets) < max_desc
        if needs_posted or needs_desc:
            targets.append(job)

    _AVATURE_DESC_SELECTORS = [
        '.jobDescription', '#jobDescription', '[class*="job-description"]',
        '[class*="jobDescription"]', '.job-content', '.jobContent',
        'main', 'article',
    ]

    def _location_from_detail_text(text):
        text = norm(text)
        m = re.search(
            r'\bLocation\(s\)\s+(.+?)(?=\s+(?:Not Your Average|About|Position Summary|'
            r'Job Description|What you|Responsibilities|Qualifications|Minimum|$))',
            text,
            re.I,
        )
        if m:
            raw = m.group(1)
            siemens_loc = re.search(r'([A-Z][A-Za-z .\'-]+?)\s+-\s+[^-]+?\s+-\s+([A-Z][A-Za-z ]+)', raw)
            loc = normalize_location(f"{siemens_loc.group(1)}, {siemens_loc.group(2)}") if siemens_loc else ""
            loc = loc or extract_location_from_text(raw) or normalize_location(raw)
            if loc and loc != "Multiple Locations":
                return loc
        m = re.search(r'\bSame job available in \d+ locations?\s+(.+?)(?=\s+(?:Back to search|Apply now|Share this job|$))', text, re.I)
        if m:
            loc = extract_location_from_text(m.group(1)) or normalize_location(m.group(1))
            if loc and loc != "Multiple Locations":
                return loc
        m = re.search(r'\bLocation:\s*([^|]+?)(?=\s+(?:Working Model|Schedule|Duration|$))', text, re.I)
        if m:
            loc = extract_location_from_text(m.group(1)) or normalize_location(m.group(1))
            if loc and loc != "Multiple Locations":
                return loc
        return ""

    def _bad_avature_location(loc):
        loc_l = (loc or "").lower()
        return (
            not loc_l
            or loc_l == "multiple locations"
            or "deloitte" in loc_l
            or "llp" in loc_l
        )

    def _fetch_avature_detail(job):
        try:
            rr = requests.get(job["url"], headers=HEADERS, timeout=20)
            rr.raise_for_status()
            soup = BeautifulSoup(rr.text, "lxml")
            posted = extract_posted_from_html(rr.text)
            full_text = norm(soup.get_text(" ", strip=True))
            loc = _location_from_detail_text(full_text)
            desc = ""
            for sel in _AVATURE_DESC_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    text = norm(el.get_text(" ", strip=True))
                    if len(text) > 100:
                        desc = text
                        break
            return job, posted, desc, loc
        except Exception:
            return job, "", "", ""

    if targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
            for job, posted, desc, loc in ex.map(_fetch_avature_detail, targets):
                if posted and not job.get("posted"):
                    job["posted"] = posted
                if loc and _bad_avature_location(job.get("location")):
                    job["location"] = loc
                if desc:
                    job["description"] = desc
    return list(jobs.values()), None
