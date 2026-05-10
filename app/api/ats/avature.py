from .base import *

# ── Avature ───────────────────────────────────────────────────────────────────

def fetch_avature(scrape, seen, company):
    """Avature public search pages, usually /careers/SearchJobs."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    page_size = int(scrape.get("page_size", 12))
    fetch_details = bool(scrape.get("fetch_posted_details", True))
    detail_limit = int(scrape.get("max_posted_details", 1000000))
    jobs = {}

    for page in range(max_pages):
        purl = list_url
        if page:
            purl = make_paged_url(list_url, {"param": "jobRecordsPerPage", "start": page_size}, 0)
            purl = make_paged_url(purl, {"param": "jobOffset", "start": page * page_size}, 0)
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
            if not title or title.lower() in ("apply", "save", "view job"):
                heading = article.find(["h2", "h3", "h4"]) if article else None
                title = norm(heading.get_text(" ", strip=True)) if heading else ""
            parent_text = re.sub(r"\b(Apply|Save|Bookmark|Share|View Job)\b", " ", parent_text, flags=re.I)
            loc_text = parent_text.replace(title, "", 1).strip()
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
    if not jobs and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "avature")
    if fetch_details:
        checked = 0
        for job in jobs.values():
            if job.get("posted") or checked >= detail_limit:
                continue
            try:
                rr = requests.get(job["url"], headers=HEADERS, timeout=20)
                rr.raise_for_status()
                job["posted"] = extract_posted_from_html(rr.text)
            except Exception:
                pass
            checked += 1
            time.sleep(0.05)
    return list(jobs.values()), None

