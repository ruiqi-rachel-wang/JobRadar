from ..base import *

def _detail_text(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
    except Exception:
        return ""
    return norm(BeautifulSoup(r.text, "lxml").get_text(" ", strip=True))

def _parse_cards(html, company):
    soup = BeautifulSoup(html or "", "lxml")
    jobs = []
    for a in soup.select('a[href*="jobdetail"]'):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        url = urljoin("https://www.kpmguscareers.com/job-search/", href)

        title_el = a.select_one(".list-view .h5") or a.select_one(".grid-view .h4")
        title = norm(title_el.get_text(" ", strip=True) if title_el else "")
        if not title:
            continue

        meta_el = a.select_one(".list-view .text-xs")
        meta = norm(meta_el.get_text(" ", strip=True) if meta_el else "")
        loc = ""
        if "|" in meta:
            loc = meta.split("|", 1)[1].strip()
        loc = normalize_location(loc)

        jobs.append({
            "title": title,
            "url": url,
            "location": loc,
            "source": "kpmg",
            "posted": "",
        })
    return jobs


def fetch_kpmg(scrape, seen, company):
    """KPMG US careers uses a WordPress AJAX endpoint for search results."""
    base_url = "https://www.kpmguscareers.com/job-search/"
    api_url = "https://www.kpmguscareers.com/wp-content/themes/understrap-child-main/page-templates/google/get-jobs.php"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    fetch_exp_detail = bool(scrape.get("fetch_experience_details", True))
    detail_workers = int(scrape.get("detail_workers", 6))
    jobs_by_url = {}

    for page in range(1, max_pages + 1):
        try:
            r = requests.get(
                api_url,
                params={"ajax": "1", "page_type": "", "spage": page},
                headers={**HEADERS, "Referer": base_url, "Accept": "application/json,text/plain,*/*"},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            if jobs_by_url:
                break
            return [], str(e)

        html = ((data.get("postings") or {}).get("jobs") or "")
        page_jobs = _parse_cards(html, company)
        if not page_jobs:
            break

        before = len(jobs_by_url)
        for job in page_jobs:
            jid = make_id(company, job["url"], job["title"])
            if scrape.get("stop_on_seen") and jid in seen:
                return list(jobs_by_url.values()), None
            jobs_by_url[job["url"]] = job

        if len(jobs_by_url) == before:
            break
        if len(page_jobs) < 12:
            break

    if fetch_exp_detail and jobs_by_url:
        targets = []
        for job in jobs_by_url.values():
            jid = make_id(company, job["url"], job["title"])
            existing = seen.get(jid) if isinstance(seen, dict) else None
            if not (isinstance(existing, dict) and existing.get("exp_label")):
                targets.append(job)

        if targets:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
                for job, desc in zip(targets, ex.map(lambda item: _detail_text(item["url"]), targets)):
                    job["description"] = desc

    return list(jobs_by_url.values()), None
