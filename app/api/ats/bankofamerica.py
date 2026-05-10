from .base import *

# ── Bank of America careers servlet ───────────────────────────────────────────

def fetch_bankofamerica(scrape, seen, company):
    list_url = scrape.get("list_url") or "https://careers.bankofamerica.com/en-us/job-search/united-states"
    endpoint = scrape.get("api_url") or "https://careers.bankofamerica.com/services/jobssearchservlet"
    rows = int(scrape.get("rows", 100))
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    search = scrape.get("search", "jobsByCountry")
    country = scrape.get("country", "United States")
    jobs = []
    seen_urls = set()
    total = None

    for page in range(max_pages):
        start = page * rows
        # BOA's servlet uses rows as an ending pointer, not a page size.
        params = {"start": start, "rows": start + rows, "search": search}
        if search == "jobsByCountry":
            params["country"] = country
        elif search == "jobsByLocation":
            params["searchstring"] = country
        try:
            r = requests.get(
                endpoint,
                params=params,
                headers={**HEADERS, "Accept": "application/json", "Referer": list_url},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)

        postings = data.get("jobsList") or []
        if total is None:
            total = int(data.get("totalMatches") or 0)
        if not postings:
            break

        for j in postings:
            title = norm(j.get("postingTitle") or "")
            path = j.get("jcrURL") or ""
            jurl = urljoin("https://careers.bankofamerica.com", path)
            if not title or not jurl or jurl in seen_urls:
                continue
            seen_urls.add(jurl)
            loc = normalize_location(norm(", ".join(
                x for x in [j.get("city"), j.get("state"), j.get("country")] if x
            )))
            posted = _parse_posted(j.get("postedDate") or j.get("indexedDate") or "")
            jobs.append({"title": title, "url": jurl, "location": loc, "source": "workday", "posted": posted})

        if total and start + rows >= total:
            break
        if len(postings) < rows:
            break
        time.sleep(0.2)

    return jobs, None

