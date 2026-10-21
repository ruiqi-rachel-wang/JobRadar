from .base import *

# ── Jobvite ───────────────────────────────────────────────────────────────────

def fetch_jobvite(scrape, seen, company):
    list_url = scrape.get("list_url", "")
    if not list_url:
        slug = scrape.get("jobvite_slug") or scrape.get("slug") or ""
        list_url = f"https://jobs.jobvite.com/{slug}/jobs" if slug else ""
    if not list_url:
        return [], "no list_url or jobvite_slug"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        return [], str(e)
    soup = BeautifulSoup(r.text, "lxml")
    jobs = {}
    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        if "/job/" not in href:
            continue
        title = norm(a.get_text(" ", strip=True))
        if not title or title.lower() in ("apply", "view job"):
            continue
        jurl = urljoin(r.url, href)
        row = a.find_parent("tr") or a.find_parent("li") or a.find_parent("div")
        parent_text = norm(row.get_text(" ", strip=True)) if row else title
        location_raw = extract_location_from_text(parent_text.replace(title, " ", 1))
        loc = normalize_location(location_raw)
        posted = extract_posted_from_text(parent_text)
        ats_match = re.search(r"/job/([^/?#]+)", jurl)
        jobs[jurl] = {
            "title": title,
            "url": jurl,
            "ats_id": ats_match.group(1) if ats_match else None,
            "location": loc,
            "location_raw": location_raw or None,
            "locations_raw": [location_raw] if location_raw else [],
            "source": "jobvite",
            "posted": posted or None,
        }
    result = list(jobs.values())
    if result and scrape.get("fetch_experience_details"):
        limit_details = int(
            scrape.get("max_experience_details")
            or scrape.get("max_desc_details")
            or 0
        )
        targets = [job for job in result if not job.get("description")]
        if limit_details > 0:
            targets = targets[:limit_details]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, max(1, len(targets)))
        ) as executor:
            details = executor.map(
                lambda job: fetch_detail_description(job.get("url") or ""),
                targets,
            )
            for job, detail in zip(targets, details):
                if detail:
                    job.update(detail)
    return result, None
