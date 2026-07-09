from .base import *
import concurrent.futures

# ── Browser-backed ATS fallbacks ──────────────────────────────────────────────

def _fetch_playwright_with_source(scrape, seen, company, source):
    cfg = dict(scrape)
    cfg["mode"] = "playwright"
    jobs, err = fetch_playwright(cfg, seen, company)
    reader_jobs = []
    reader_err = None
    if scrape.get("list_url"):
        reader_jobs, reader_err = _fetch_reader_markdown(scrape, seen, company, source)
        if not jobs and reader_jobs:
            return reader_jobs, None
        if not jobs and err is None:
            err = reader_err

    # Browser cards are often visually truncated while the public reader output
    # contains the complete table row. Merge by URL so location/date metadata is
    # retained even when Playwright found the link first.
    reader_by_url = {j.get("url"): j for j in reader_jobs if j.get("url")}
    for job in jobs or []:
        reader_job = reader_by_url.get(job.get("url")) or {}
        if reader_job.get("location"):
            job["location"] = reader_job["location"]
        if not job.get("posted") and reader_job.get("posted"):
            job["posted"] = reader_job["posted"]

    if scrape.get("fetch_descriptions") and jobs:
        _fill_reader_descriptions(jobs, seen, scrape)

    for j in jobs or []:
        j["source"] = source
    return jobs, err

def _fill_reader_descriptions(jobs, seen, scrape):
    """Fetch public detail text only when the stored job has no description."""
    limit = max(0, int(scrape.get("max_description_details", 50)))
    targets = []
    for job in jobs:
        jid = make_id(scrape.get("_company_name", ""), job.get("url", ""), job.get("title", ""))
        existing = seen.get(jid) if isinstance(seen, dict) else None
        if isinstance(existing, dict) and existing.get("description"):
            continue
        if job.get("url"):
            targets.append(job)
        if len(targets) >= limit:
            break

    def fetch_one(job):
        try:
            r = requests.get("https://r.jina.ai/" + job["url"], headers=HEADERS, timeout=35)
            r.raise_for_status()
            text = norm(r.text)
            if len(text) < 200 or re.search(r"\bpage not found\b", text[:300], re.I):
                return job, ""
            text = re.sub(r"^Title:.*?Markdown Content:\s*", "", text, flags=re.I)
            return job, text
        except Exception:
            return job, ""

    if not targets:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for job, description in executor.map(fetch_one, targets):
            if description:
                job["description"] = description

def _fetch_reader_markdown(scrape, seen, company, source):
    """Fallback for public careers pages that block direct HTTP/browser scraping."""
    list_url = (scrape.get("list_url") or "").strip()
    include = (scrape.get("include") or scrape.get("prefix") or "").strip()
    if not list_url or not include:
        return [], "reader fallback needs list_url and include/prefix"

    reader_url = "https://r.jina.ai/" + list_url
    try:
        r = requests.get(reader_url, headers=HEADERS, timeout=35)
        r.raise_for_status()
    except Exception as e:
        return [], str(e)

    lines = r.text.splitlines()
    jobs = {}
    needs_id = bool(scrape.get("require_id", True)) and include.endswith(("=", "/"))
    for idx, line in enumerate(lines):
        for title, url in re.findall(r"\[([^\]]{4,180})\]\((https?://[^)\s]+)\)", line):
            if not has_include(url, include):
                continue
            if needs_id:
                suffix = url.lower().split(include.lower(), 1)[-1]
                if not re.search(r"\d{2,}", suffix):
                    continue
            title = norm(re.sub(r"!\[[^\]]*\]", " ", title))
            if not title or re.search(r"^(apply|view job|search jobs|saved jobs|learn more)$", title, re.I):
                continue
            nearby = " ".join(lines[idx:idx + 8])
            loc = ""
            if line.lstrip().startswith("|"):
                cells = [norm(cell.strip(" *")) for cell in line.strip().strip("|").split("|")]
                # Common careers-table shape:
                # title | requisition | employment type | location | workplace
                if (
                    len(cells) >= 5
                    and re.fullmatch(r"[A-Z]*\d{4,}", cells[1], re.I)
                    and re.search(r"\b(?:full|part|temporary|contract)\s*time\b", cells[2], re.I)
                ):
                    loc = normalize_location(cells[3])
                if not loc:
                    for cell in cells[1:]:
                        extracted = extract_location_from_text(cell)
                        if extracted:
                            loc = normalize_location(extracted)
                            break
            loc_match = re.search(r"\*\*([^*]{2,80})\*\*", nearby)
            if not loc and loc_match:
                loc = normalize_location(norm(loc_match.group(1)))
            loc = loc or extract_location_from_text(nearby)
            posted = extract_posted_from_text(nearby)
            jobs[url] = {
                "title": title,
                "url": url,
                "location": loc,
                "source": source,
                "posted": posted,
            }
    return list(jobs.values()), None

def fetch_brassring(scrape, seen, company):
    return _fetch_playwright_with_source(scrape, seen, company, "brassring")

def fetch_taleo(scrape, seen, company):
    cfg = dict(scrape)
    cfg["_company_name"] = company
    return _fetch_playwright_with_source(cfg, seen, company, "taleo")

def fetch_phenom(scrape, seen, company):
    """
    Phenom Talent Experience Management scraper.
    Phenom embeds job data as JSON in phApp.ddo on the search-results page.
    Paginates via ?from=N&s=1.  Falls back to Playwright if JSON not found.
    """
    list_url = (scrape.get("list_url") or "").strip()
    if not list_url:
        return [], "no list_url"
    prefix = (scrape.get("prefix") or "").rstrip("/")
    verify_ssl = bool(scrape.get("verify_ssl", True))
    # Derive job URL base from list_url: replace /search-results (or /search-jobs)
    if prefix:
        job_base = prefix + "/"
    else:
        job_base = re.sub(r'/search(?:-results|-jobs).*', '/job/', list_url)

    max_pages = int(scrape.get("hard_max_pages") or 100)
    jobs = {}
    consec_all_seen = 0

    for page in range(max_pages):
        offset = page * 10  # Phenom default page size is 10
        if offset == 0:
            purl = list_url
        else:
            base = list_url.split("?")[0]
            purl = f"{base}?from={offset}&s=1"
        try:
            r = requests.get(purl, headers=HEADERS, timeout=25, verify=verify_ssl)
            r.raise_for_status()
        except Exception as e:
            return list(jobs.values()), str(e)

        m = re.search(r'phApp\.ddo\s*=\s*(\{.{100,}?\});?\s*phApp', r.text, re.S)
        if not m:
            break
        try:
            data = json.loads(m.group(1))
        except Exception:
            break

        search = data.get("eagerLoadRefineSearch") or {}
        total = search.get("totalHits") or 0
        job_list = (search.get("data") or {}).get("jobs") or []
        if not job_list:
            break

        before = len(jobs)
        page_seen = []
        for j in job_list:
            title = norm(j.get("title") or "")
            req_id = str(j.get("reqId") or "").strip()
            if not title or not req_id:
                continue
            jurl = job_base + req_id
            # cityStateCountry is the most complete location field Phenom provides
            raw_loc = (j.get("cityStateCountry") or j.get("location") or
                       j.get("city") or "")
            loc = normalize_location(norm(raw_loc))
            posted = _parse_posted(j.get("postedDate") or j.get("dateCreated") or "")
            jobs[jurl] = {"title": title, "url": jurl, "location": loc,
                          "source": "phenom", "posted": posted}
            jid = make_id(company, jurl, title)
            page_seen.append(jid in seen)
        if len(jobs) == before:
            break
        if scrape.get("stop_on_seen") and page_seen:
            consec_all_seen = consec_all_seen + 1 if all(page_seen) else 0
            if consec_all_seen >= 2:
                break
        if total and offset + 10 >= total:
            break
        time.sleep(0.3)

    if jobs:
        return list(jobs.values()), None

    pw_err = "no Phenom JSON jobs found"
    if scrape.get("allow_browser_fallback") is not False:
        pw_jobs, pw_err = _fetch_playwright_with_source(scrape, seen, company, "phenom")
        if pw_jobs:
            return pw_jobs, pw_err
    if "keysight.com" in list_url:
        return _fetch_jibe_text_cards(scrape, seen, company, "phenom")
    return [], pw_err

def fetch_talemetry(scrape, seen, company):
    return _fetch_playwright_with_source(scrape, seen, company, "talemetry")

def fetch_adp(scrape, seen, company):
    return _fetch_playwright_with_source(scrape, seen, company, "adp")

def _fetch_jibe_text_cards(scrape, seen, company, source):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], (
            "playwright not installed — run:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium"
        )
    list_url = (scrape.get("list_url") or "").strip()
    if not list_url:
        return [], "no list_url"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=HEADERS.get("User-Agent", ""),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            page = ctx.new_page()
            page.goto(list_url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(5000)
            for _ in range(int(scrape.get("scroll_rounds", 8))):
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(400)
            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            browser.close()
    except Exception as e:
        return [], str(e)

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out, seen_urls = [], set()
    for i, line in enumerate(lines):
        m = re.match(r"Req ID:\s*([A-Za-z0-9_-]+)", line, re.I)
        if not m or i == 0:
            continue
        title = lines[i - 1].strip()
        if not title or title.lower() in {"apply now", "regular", "remote"}:
            continue
        req = m.group(1)
        loc = ""
        for j in range(i + 1, min(i + 8, len(lines))):
            if lines[j].lower() == "location" and j + 1 < len(lines):
                loc_parts = [lines[j + 1]]
                if j + 2 < len(lines) and len(lines[j + 2]) <= 3:
                    loc_parts.append(lines[j + 2])
                loc = normalize_location(", ".join(loc_parts))
                break
        url = urljoin(list_url.rstrip("/") + "/", req)
        if url in seen_urls:
            continue
        seen_urls.add(url)
        out.append({"title": title, "url": url, "location": loc, "source": source, "posted": ""})
    return out, None

def _fetch_bambusdev_salesforce(scrape, seen, company):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], (
            "playwright not installed — run:\n"
            "  pip install playwright\n"
            "  python -m playwright install chromium"
        )

    list_url = (scrape.get("list_url") or "").strip()
    prefix = (scrape.get("prefix") or "https://bambusdev.my.site.com/s/details?jobReq=").strip()
    scroll_rounds = int(scrape.get("scroll_rounds", 30))
    if not list_url:
        return [], "no list_url"

    def _bam_location_parts(line):
        known = [
            "Hong Kong", "Singapore", "Tokyo", "London", "Chicago",
            "New York", "Greenwich", "San Francisco",
        ]
        found = [loc for loc in known if re.search(re.escape(loc).replace(r"\ ", r"\s*"), line, re.I)]
        if found:
            return found
        return [line]

    def _detail_from_text(raw_text):
        lines = [norm(ln) for ln in raw_text.splitlines() if norm(ln)]
        desc = norm(raw_text)
        locs = []
        posted_idx = next((idx for idx, line in enumerate(lines[:100]) if re.match(r'^posted\b', line, re.I)), None)
        if posted_idx is not None:
            candidate_lines = list(reversed(lines[max(0, posted_idx - 4):posted_idx]))
        else:
            candidate_lines = lines[:20]
        for line in candidate_lines:
            if re.search(r'\b(apply|share|copy link|similar jobs|login)\b', line, re.I):
                continue
            if line.lower() in {"open roles", "job detail", "careers", "skip to main content"}:
                continue
            if len(line) > 60:
                continue
            for part in _bam_location_parts(line):
                candidate = normalize_location(part)
                if candidate and candidate != "Multiple Locations" and candidate.lower() not in {x.lower() for x in locs}:
                    locs.append(candidate)
        if not locs:
            m = re.search(
                r'\bto be based (?:either )?in\s+(.+?)(?:\.|\s+This\b)',
                desc,
                re.I,
            )
            if m:
                for part in re.split(r'\s*,\s*|\s+\bor\b\s+|\s+\band\b\s+', m.group(1)):
                    candidate = normalize_location(part)
                    if candidate and candidate.lower() not in {x.lower() for x in locs}:
                        locs.append(candidate)
        return {"location": "; ".join(locs), "description": desc if len(desc) > 150 else ""}

    def _fetch_detail(jurl):
        try:
            r = requests.get(jurl, headers=HEADERS, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            return _detail_from_text(soup.get_text("\n", strip=True))
        except Exception:
            return {"location": "", "description": ""}

    def _fetch_browser_detail(page, jurl):
        try:
            page.goto(jurl, wait_until="networkidle", timeout=45_000)
            page.wait_for_timeout(1500)
            raw_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            detail = _detail_from_text(raw_text)
            if re.search(r'\b(css error|sorry to interrupt|loading)\b', detail.get("description") or "", re.I):
                return _fetch_detail(jurl)
            return detail
        except Exception:
            return _fetch_detail(jurl)

    def _jobs_from_text(text):
        parsed = []
        seen_urls = set()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            m = re.match(r"^(REQ\d+)\s*\|\s*(.*?)\s*\|\s*Posted\s+(.+)$", line, re.I)
            if not m or i == 0:
                continue
            title = lines[i - 1].strip()
            if not title or title.lower() in {"more", "clear all", "refine your search"}:
                continue
            req, location, posted = m.groups()
            slug = re.sub(r'[^a-zA-Z0-9-]', '-', title)
            url = prefix + slug + "_" + req
            if url in seen_urls:
                continue
            seen_urls.add(url)
            parsed.append({
                "title": title,
                "url": url,
                "location": location,
                "source": scrape.get("source", "bambusdev"),
                "posted": posted,
            })
        return parsed

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=HEADERS.get("User-Agent", ""),
                viewport={"width": 1440, "height": 900},
                locale="en-US",
            )
            page = ctx.new_page()
            page.goto(list_url, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(2500)
            for _ in range(scroll_rounds):
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(500)
            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            jobs = _jobs_from_text(text)

            detail_targets = []
            fetch_details = bool(scrape.get("fetch_experience_details") or scrape.get("fetch_details"))
            refresh_experience = bool(scrape.get("refresh_experience"))
            if fetch_details:
                for job in jobs:
                    jid_key = make_id(company, job["url"], job["title"])
                    existing = seen.get(jid_key) if isinstance(seen, dict) else None
                    existing_has_desc = isinstance(existing, dict) and existing.get("has_description")
                    if refresh_experience or not existing_has_desc or not job.get("location") or ";" not in (job.get("location") or ""):
                        detail_targets.append(job)
            max_desc = int(scrape.get("max_desc_details", 200))
            if detail_targets[:max_desc]:
                detail_page = ctx.new_page()
                for job in detail_targets[:max_desc]:
                    detail = _fetch_browser_detail(detail_page, job["url"])
                    if detail.get("location"):
                        job["location"] = detail["location"]
                    if detail.get("description"):
                        job["description"] = detail["description"]
            browser.close()
            return jobs, None
    except Exception as e:
        return [], str(e)

def fetch_salesforce(scrape, seen, company):
    if "bambusdev.my.site.com" in (scrape.get("list_url") or ""):
        return _fetch_bambusdev_salesforce(scrape, seen, company)
    return _fetch_playwright_with_source(scrape, seen, company, scrape.get("source", "salesforce"))


# ── Playwright ────────────────────────────────────────────────────────────────

def fetch_playwright(scrape, seen, company):
    from .playwright_scraper import fetch_playwright as _pw
    jobs, err = _pw(scrape, seen, company)
    if not jobs and scrape.get("list_url"):
        reader_jobs, reader_err = _fetch_reader_markdown(scrape, seen, company, "playwright")
        if reader_jobs:
            return reader_jobs, None
        if err is None:
            err = reader_err
    return jobs, err
