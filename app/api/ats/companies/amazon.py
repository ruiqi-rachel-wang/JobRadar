from ..base import *

def fetch_amazon(scrape, seen, company):
    """
    Amazon Jobs JSON API — replaces slow Playwright scraping.
    Returns up to 10,000 jobs (API hard cap) at 100/request sorted newest-first.
    """
    do_sort   = scrape.get("sort", True)
    max_pages = int(scrape.get("max_pages", 10000))
    country   = scrape.get("country", "USA")
    jobs      = []

    for page in range(max_pages):
        offset = page * 100
        try:
            r = requests.get(
                "https://www.amazon.jobs/en/search.json",
                params={
                    "offset": offset,
                    "result_limit": 100,
                    "sort": "recent",
                    "country[]": country,
                },
                headers=HEADERS,
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            if jobs: break
            return [], str(e)

        postings = data.get("jobs") or []
        if not postings:
            break

        stop = False
        for j in postings:
            title  = norm(j.get("title") or "")
            path   = j.get("job_path") or ""
            jurl   = f"https://www.amazon.jobs{path}" if path else ""

            # Location: use first entry from locations array if available
            loc_raw = ""
            locs = j.get("locations") or []
            if locs:
                try:
                    loc_data = json.loads(locs[0]) if isinstance(locs[0], str) else locs[0]
                    city    = loc_data.get("city") or ""
                    state   = loc_data.get("normalizedStateName") or loc_data.get("region") or ""
                    country_name = loc_data.get("normalizedCountryName") or ""
                    loc_raw = ", ".join(x for x in [city, state, country_name] if x)
                except Exception:
                    loc_raw = j.get("location") or ""
            else:
                loc_raw = j.get("location") or ""

            loc    = normalize_location(norm(loc_raw))
            posted = _parse_posted(j.get("posted_date") or j.get("updated_time") or "")
            desc = norm(BeautifulSoup(" ".join(str(j.get(k) or "") for k in (
                "basic_qualifications", "preferred_qualifications", "description",
            )), "lxml").get_text(" ", strip=True))

            if title and jurl:
                jid = make_id(company, jurl, title)
                if do_sort and jid in seen:
                    stop = True
                    break
                jobs.append({"title": title, "url": jurl, "location": loc,
                             "source": "amazon", "posted": posted, "description": desc})

        if stop:
            break

        total = int(data.get("hits") or 0)
        if offset + 100 >= total:
            break
        time.sleep(0.2)

    return jobs, None
