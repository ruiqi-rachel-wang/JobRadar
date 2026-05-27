from .base import *

# ── Browser-backed ATS fallbacks ──────────────────────────────────────────────

def _fetch_playwright_with_source(scrape, seen, company, source):
    cfg = dict(scrape)
    cfg["mode"] = "playwright"
    jobs, err = fetch_playwright(cfg, seen, company)
    if not jobs and scrape.get("list_url"):
        reader_jobs, reader_err = _fetch_reader_markdown(scrape, seen, company, source)
        if reader_jobs:
            return reader_jobs, None
        if err is None:
            err = reader_err
    for j in jobs or []:
        j["source"] = source
    return jobs, err

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
    for idx, line in enumerate(lines):
        for title, url in re.findall(r"\[([^\]]{4,180})\]\((https?://[^)\s]+)\)", line):
            if not has_include(url, include):
                continue
            title = norm(re.sub(r"!\[[^\]]*\]", " ", title))
            if not title or re.search(r"^(apply|view job|search jobs|saved jobs|learn more)$", title, re.I):
                continue
            nearby = " ".join(lines[idx + 1:idx + 8])
            loc = ""
            loc_match = re.search(r"\*\*([^*]{2,80})\*\*", nearby)
            if loc_match:
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
    return _fetch_playwright_with_source(scrape, seen, company, "taleo")

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

    max_pages = int(scrape.get("hard_max_pages") or 10000)
    jobs = {}

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
        if len(jobs) == before:
            break
        if total and offset + 10 >= total:
            break
        time.sleep(0.3)

    if jobs:
        return list(jobs.values()), None

    pw_err = "no Phenom JSON jobs found"
    if scrape.get("allow_browser_fallback"):
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
            browser.close()
    except Exception as e:
        return [], str(e)

    jobs = []
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
        jobs.append({
            "title": title,
            "url": url,
            "location": location,
            "source": scrape.get("source", "bambusdev"),
            "posted": posted,
        })
    return jobs, None

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
