from .base import *
import concurrent.futures

# ── Browser-backed ATS fallbacks ──────────────────────────────────────────────

def _fetch_playwright_with_source(scrape, seen, company, source):
    if scrape.get("reader_only"):
        jobs, err = _fetch_reader_markdown(scrape, seen, company, source)
        return jobs, err
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
        if isinstance(existing, dict) and (
            existing.get("description") or existing.get("has_description")
        ):
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
    workers = max(1, min(8, int(scrape.get("description_workers", 4))))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for job, description in executor.map(fetch_one, targets):
            if description:
                job["description"] = description


def _fill_detail_titles(jobs, scrape):
    """Replace concatenated card labels with titles from public detail pages."""
    limit = max(0, int(scrape.get("max_title_details", 100)))
    targets = [job for job in jobs if job.get("url")][:limit]
    if not targets:
        return

    def fetch_one(job):
        try:
            response = requests.get(job["url"], headers=HEADERS, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            heading = soup.select_one(scrape.get("detail_title_selector", "h1"))
            title = norm(heading.get_text(" ", strip=True)) if heading else ""
            if not title and soup.title:
                title = norm(soup.title.get_text(" ", strip=True)).split(" | ", 1)[0].strip()
            if not title or re.search(
                r"^(apply|view job|search jobs|learn more|see details)$",
                title,
                re.I,
            ):
                return job, ""
            return job, title
        except Exception:
            return job, ""

    workers = max(1, min(8, int(scrape.get("title_detail_workers", 6))))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for job, title in executor.map(fetch_one, targets):
            if title:
                job["title"] = title

def _fetch_reader_markdown(scrape, seen, company, source):
    """Fallback for public careers pages that block direct HTTP/browser scraping."""
    list_url = (scrape.get("list_url") or "").strip()
    include = (scrape.get("include") or scrape.get("prefix") or "").strip()
    if not list_url or not include:
        return [], "reader fallback needs list_url and include/prefix"

    jobs = {}
    needs_id = bool(scrape.get("require_id", True)) and include.endswith(("=", "/"))
    first_err = None
    expected_total = None
    stopped_on_seen = False
    matched_job_rows = 0
    seen_page_signatures = set()

    pagination = scrape.get("pagination") or {"type": "none"}
    paginate = bool(scrape.get("reader_paginate"))
    pg_type = (pagination.get("type") or "none") if paginate else "none"
    max_pages = int(pagination.get("hard_max_pages") or scrape.get("hard_max_pages") or 100)

    if pg_type == "query_page":
        param = (pagination.get("param") or "page").strip()
        start = int(pagination.get("start", 1))
        step = int(pagination.get("step", 1))
        page_urls = [make_paged_url(list_url, {"param": param, "start": start, "step": step}, i)
                     for i in range(max_pages)]
    else:
        page_urls = [list_url]

    # Reader caches can serve sequential pages from slightly different
    # snapshots while a busy employer is adding jobs. For explicitly opted-in
    # large boards, discover the current total from page one and fetch the
    # remaining pages concurrently in a small bounded pool. This is both much
    # faster and less likely to shift jobs across page boundaries mid-scan.
    prefetched = {}
    if scrape.get("reader_parallel_pages") and pg_type == "query_page" and page_urls:
        try:
            first_response = requests.get(
                "https://r.jina.ai/" + page_urls[0],
                headers=HEADERS,
                timeout=35,
            )
            first_response.raise_for_status()
            prefetched[page_urls[0]] = first_response
            total_match = re.search(
                r"\bShowing\s+\d[\d,]*(?:\s*-\s*\d[\d,]*)?\s+of\s+([\d,]+)\s+results?\b",
                first_response.text,
                re.I,
            )
            if total_match:
                expected_total = int(total_match.group(1).replace(",", ""))
                page_size_match = re.search(
                    r"\bShowing\s+(\d[\d,]*)\s*-\s*(\d[\d,]*)\s+of\b",
                    first_response.text,
                    re.I,
                )
                page_size = (
                    int(page_size_match.group(2).replace(",", ""))
                    - int(page_size_match.group(1).replace(",", ""))
                    + 1
                    if page_size_match else 25
                )
                needed = max(1, (expected_total + page_size - 1) // page_size)
                page_urls = page_urls[:needed]

            def _prefetch(page_url):
                response = requests.get(
                    "https://r.jina.ai/" + page_url,
                    headers=HEADERS,
                    timeout=35,
                )
                response.raise_for_status()
                return page_url, response

            remaining = page_urls[1:]
            if remaining:
                workers = min(4, len(remaining))
                with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                    for fetched_url, response in executor.map(_prefetch, remaining):
                        prefetched[fetched_url] = response
        except Exception as e:
            if first_err is None:
                first_err = str(e)

    empty_streak = 0
    for page_url in page_urls:
        try:
            r = prefetched.get(page_url)
            if r is None:
                r = requests.get("https://r.jina.ai/" + page_url, headers=HEADERS, timeout=35)
                r.raise_for_status()
        except Exception as e:
            if first_err is None:
                first_err = str(e)
            break

        before = len(jobs)
        page_job_urls = []
        page_matched_rows = 0
        total_match = re.search(
            r"\bShowing\s+\d[\d,]*(?:\s*-\s*\d[\d,]*)?\s+of\s+([\d,]+)\s+results?\b",
            r.text,
            re.I,
        )
        if total_match:
            expected_total = int(total_match.group(1).replace(",", ""))
        lines = r.text.splitlines()
        for idx, line in enumerate(lines):
            for title, url in re.findall(r"\[([^\]]{4,180})\]\((https?://[^)\s]+)\)", line):
                if not has_include(url, include):
                    continue
                if needs_id:
                    suffix = url.lower().split(include.lower(), 1)[-1]
                    if not re.search(r"\d{2,}", suffix):
                        continue
                title = norm(re.sub(r"!\[[^\]]*\]", " ", title))
                if not title or re.search(r"^(apply|apply now|view job|search jobs|saved jobs|learn more|read more|see details|job details)$", title, re.I):
                    continue
                title_regex = scrape.get("title_regex")
                if title_regex:
                    title_match = re.search(title_regex, title)
                    if title_match:
                        title = norm(title_match.group(1))
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
                page_job_urls.append(url)
                page_matched_rows += 1
                existing_job = jobs.get(url) or {}
                raw_locations = list(existing_job.get("locations_raw") or [])
                if loc and loc.lower() not in {
                    value.lower() for value in raw_locations
                }:
                    raw_locations.append(loc)
                jobs[url] = {
                    "title": title,
                    "url": url,
                    "location": "; ".join(raw_locations) or loc,
                    "location_raw": (
                        "; ".join(raw_locations) or loc or None
                    ),
                    "locations_raw": raw_locations,
                    "source": source,
                    "posted": posted or existing_job.get("posted"),
                }
        page_signature = tuple(page_job_urls)
        if page_signature and page_signature not in seen_page_signatures:
            seen_page_signatures.add(page_signature)
            matched_job_rows += page_matched_rows
        if len(jobs) == before:
            empty_streak += 1
        else:
            empty_streak = 0
        if empty_streak >= 2:
            break
        if scrape.get("stop_on_seen") and jobs:
            page_jobs = list(jobs.values())[before:]
            if page_jobs and all(
                make_id(company, job.get("url", ""), job.get("title", "")) in seen
                for job in page_jobs
            ):
                stopped_on_seen = True
                break
        if len(page_urls) > 1:
            time.sleep(0.35)
    total_tolerance = max(0, int(scrape.get("reported_total_tolerance") or 0))
    incomplete = (
        not stopped_on_seen
        and expected_total is not None
        and matched_job_rows + total_tolerance < expected_total
    )
    if (
        incomplete
        and scrape.get("reader_parallel_pages")
        and not scrape.get("_retrying_incomplete_reader")
    ):
        # Public reader caches can briefly mix snapshots when many pages are
        # requested together. A bounded sequential retry is slower, but only
        # runs after an incomplete full read and prevents false partial errors.
        retry_scrape = dict(scrape)
        retry_scrape["reader_parallel_pages"] = False
        retry_scrape["stop_on_seen"] = False
        retry_scrape["_retrying_incomplete_reader"] = True
        retry_jobs, retry_err = _fetch_reader_markdown(
            retry_scrape,
            seen,
            company,
            source,
        )
        for job in retry_jobs:
            if job.get("url"):
                jobs[job["url"]] = job
        if len(jobs) + total_tolerance >= expected_total:
            incomplete = False
            first_err = None
        elif retry_err:
            first_err = f"{first_err}; {retry_err}" if first_err else retry_err
    if incomplete:
        partial_err = (
            f"partial listing: fetched {matched_job_rows} "
            f"of {expected_total} result rows"
        )
        first_err = f"{first_err}; {partial_err}" if first_err else partial_err
    return list(jobs.values()), first_err

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
    expected_total = None

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

        m = re.search(r'phApp\.ddo\s*=\s*', r.text)
        if not m:
            break
        try:
            data, _ = json.JSONDecoder().raw_decode(r.text, m.end())
        except Exception:
            break

        search = data.get("eagerLoadRefineSearch") or {}
        total = search.get("totalHits") or 0
        expected_total = max(expected_total or 0, int(total))
        job_list = (search.get("data") or {}).get("jobs") or []
        if not job_list:
            break

        before = len(jobs)
        page_seen = []
        for j in job_list:
            title = norm(j.get("title") or "")
            id_field = scrape.get("job_url_id_field") or "reqId"
            req_id = str(j.get(id_field) or "").strip()
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
        result = list(jobs.values())
        if scrape.get("fetch_descriptions"):
            detail_scrape = dict(scrape)
            detail_scrape["_company_name"] = company
            _fill_reader_descriptions(result, seen, detail_scrape)
        error = None
        if expected_total and len(jobs) < expected_total and not scrape.get("stop_on_seen"):
            error = f"partial Phenom listing: fetched {len(jobs)} of {expected_total} jobs"
        return result, error

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
        out.append({
            "title": title,
            "url": url,
            "ats_id": req,
            "requisition_id": req,
            "location": loc,
            "location_raw": loc or None,
            "locations_raw": [loc] if loc else [],
            "source": source,
            "posted": None,
        })
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
    if scrape.get("reader_only"):
        return _fetch_reader_markdown(scrape, seen, company, "playwright")
    from .playwright_scraper import fetch_playwright as _pw
    jobs, err = _pw(scrape, seen, company)
    if not jobs and scrape.get("list_url"):
        reader_jobs, reader_err = _fetch_reader_markdown(scrape, seen, company, "playwright")
        if reader_jobs:
            jobs, err = reader_jobs, None
        if err is None:
            err = reader_err
    if jobs and scrape.get("fetch_titles_from_details"):
        _fill_detail_titles(jobs, scrape)
    return jobs, err
