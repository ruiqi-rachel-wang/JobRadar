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
    purl = list_url
    visited_pages = set()

    for page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return list(jobs.values()), "company scan deadline reached (partial results)"
        if purl in visited_pages:
            break
        visited_pages.add(purl)
        try:
            r = requests.get(purl, headers=HEADERS, timeout=25)
            r.raise_for_status()
        except Exception as e:
            return list(jobs.values()), str(e)

        soup = BeautifulSoup(r.text, "lxml")
        page_count = len(jobs)
        page_job_urls = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href") or ""
            if "/JobDetail/" not in href:
                continue
            jurl = urljoin(r.url, href)
            page_job_urls.add(jurl)
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
            # Avature cards commonly put employer, legal entity, and location
            # in separate subtitle spans. The final span is the authoritative
            # location; parsing the whole subtitle leaks "Deloitte ... LLP"
            # into the displayed city.
            subtitle = article.select_one(
                ".article__header__text__subtitle"
            ) if article else None
            subtitle_parts = [
                norm(span.get_text(" ", strip=True))
                for span in (subtitle.find_all("span") if subtitle else [])
                if norm(span.get_text(" ", strip=True))
            ]
            location_element = (
                subtitle.select_one(
                    ".list-item-location, [class*='location']"
                )
                if subtitle else None
            )
            if location_element:
                loc_text = norm(location_element.get_text(" ", strip=True))
                loc = normalize_location(loc_text)
            elif subtitle_parts:
                # Avature boards without a location-specific class (notably
                # Deloitte) put the location in the last subtitle span.
                loc_text = subtitle_parts[-1]
                loc = normalize_location(loc_text)
            else:
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
                detail_path = re.search(r"/JobDetail/([^?#]+)", jurl)
                detail_parts = [
                    part for part in (detail_path.group(1).split("/") if detail_path else [])
                    if part
                ]
                ats_id = unquote(detail_parts[-1]) if detail_parts else None
                jobs[jurl] = {
                    "title": title,
                    "url": jurl,
                    "ats_id": ats_id,
                    "location": loc,
                    "location_raw": loc_text or None,
                    "locations_raw": [loc] if loc else [],
                    "source": "avature",
                    "posted": posted or None,
                }
        if len(jobs) == page_count:
            break

        # Prefer the board's own next link. Siemens accepts a requested page
        # size of 18 but returns 6 rows and points Next to offset=6; synthesizing
        # offset=18 silently skipped two thirds of its jobs.
        next_link = None
        for candidate in soup.find_all("a", href=True):
            rel = " ".join(candidate.get("rel") or []).lower()
            label = norm(
                candidate.get("aria-label")
                or candidate.get_text(" ", strip=True)
            ).lower()
            if "next" in rel or re.fullmatch(r"next(?:\s*>>?)?", label, re.I):
                next_link = candidate
                break
        if next_link:
            purl = urljoin(r.url, next_link.get("href") or "")
            continue

        # Generic Avature fallback: advance by the rows actually returned,
        # never by an optimistic configured page size.
        actual_page_size = len(page_job_urls)
        if not actual_page_size:
            break
        query = parse_qs(urlparse(r.url).query)
        try:
            current_offset = int((query.get(offset_param) or [0])[0])
        except (TypeError, ValueError):
            current_offset = page * actual_page_size
        purl = make_paged_url(
            r.url,
            {"param": size_param, "start": actual_page_size},
            0,
        )
        purl = make_paged_url(
            purl,
            {"param": offset_param, "start": current_offset + actual_page_size},
            0,
        )
    if not jobs and scrape.get("allow_browser_fallback") is not False and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "avature")
    def _bad_avature_location(loc):
        loc_l = (loc or "").lower()
        return (
            not loc_l
            or loc_l == "multiple locations"
            or "deloitte" in loc_l
            or "llp" in loc_l
        )

    # Fetch detail pages for jobs missing posted date, description, or a
    # concrete location. Cached descriptions must not freeze a bad location.
    max_desc = int(scrape.get("max_desc_details", 500))
    detail_workers = int(scrape.get("detail_workers", 6))
    targets = []
    for job in jobs.values():
        jid = make_id(
            company, job["url"], job["title"], "avature", job.get("ats_id")
        )
        existing = (
            seen.get(jid)
            or seen.get(make_id(company, job["url"], job["title"]))
            if isinstance(seen, dict) else None
        )
        if not job.get("posted") and isinstance(existing, dict):
            job["posted"] = _parse_posted(existing.get("posted") or "")
        existing_has_desc = isinstance(existing, dict) and existing.get("has_description")
        needs_posted = fetch_details and not job.get("posted") and len(targets) < detail_limit
        needs_desc = not existing_has_desc and len(targets) < max_desc
        needs_location = _bad_avature_location(job.get("location")) and len(targets) < max_desc
        if needs_posted or needs_desc or needs_location:
            targets.append(job)

    _AVATURE_DESC_SELECTORS = [
        '.jobDescription', '#jobDescription', '[class*="job-description"]',
        '[class*="jobDescription"]', '.job-content', '.jobContent',
        'main', 'article',
    ]

    def _location_from_detail_text(text):
        text = norm(text)
        m = re.search(
            r'\bAny Siemens location in\s+(.+?)(?=\s+Here at Siemens\b|$)',
            text,
            re.I,
        )
        if m:
            raw = m.group(1).strip(" ,;•")
            pair = re.fullmatch(
                r"(.+?)\s+-\s+(United States(?: of America)?|USA|US|"
                r"United Kingdom|UK|Canada|India|Australia|Germany|France)",
                raw,
                re.I,
            )
            loc = (
                normalize_location(f"{pair.group(1)}, {pair.group(2)}")
                if pair else normalize_location(raw)
            )
            if loc:
                return loc, raw
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
                return loc, raw
        m = re.search(r'\bSame job available in \d+ locations?\s+(.+?)(?=\s+(?:Back to search|Apply now|Share this job|$))', text, re.I)
        if m:
            loc = extract_location_from_text(m.group(1)) or normalize_location(m.group(1))
            if loc and loc != "Multiple Locations":
                return loc, m.group(1).strip()
        m = re.search(r'\bLocation:\s*([^|]+?)(?=\s+(?:Working Model|Schedule|Duration|$))', text, re.I)
        if m:
            loc = extract_location_from_text(m.group(1)) or normalize_location(m.group(1))
            if loc and loc != "Multiple Locations":
                return loc, m.group(1).strip()
        return "", ""

    def _fetch_avature_detail(job):
        try:
            rr = requests.get(job["url"], headers=HEADERS, timeout=20)
            rr.raise_for_status()
            soup = BeautifulSoup(rr.text, "lxml")
            full_text = norm(soup.get_text(" ", strip=True))
            posted_raw = None
            posted_match = re.search(
                r"\bPosted since\s+(\d{1,2}-[A-Za-z]{3,9}-\d{4})\b",
                full_text,
                re.I,
            )
            if posted_match:
                posted_raw = posted_match.group(1)
            posted = (
                _parse_posted(posted_raw)
                or extract_posted_from_html(rr.text)
            )
            loc, loc_raw = _location_from_detail_text(full_text)
            work_mode = None
            work_mode_match = re.search(
                r"\bWork mode\s+(.+?)(?=\s+Employment type\b|$)",
                full_text,
                re.I,
            )
            if work_mode_match:
                work_mode = work_mode_match.group(1).strip()
            desc = ""
            for sel in _AVATURE_DESC_SELECTORS:
                el = soup.select_one(sel)
                if el:
                    text = norm(el.get_text(" ", strip=True))
                    if len(text) > 100:
                        desc = str(el)
                        break
            return job, posted, posted_raw, desc, loc, loc_raw, work_mode
        except Exception:
            return job, "", None, "", "", "", None

    if targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
            for job, posted, posted_raw, desc, loc, loc_raw, work_mode in ex.map(
                _fetch_avature_detail, targets
            ):
                if posted and not job.get("posted"):
                    job["posted"] = posted
                if posted_raw:
                    job["posted_raw"] = posted_raw
                    job["posted_at"] = posted_raw
                if loc and _bad_avature_location(job.get("location")):
                    job["location"] = loc
                    job["location_raw"] = loc_raw or job.get("location_raw")
                    job["locations_raw"] = [loc_raw] if loc_raw else []
                if work_mode:
                    job["remote_mode"] = work_mode
                if desc:
                    job["description"] = desc
                    job["description_raw"] = desc
    return list(jobs.values()), None
