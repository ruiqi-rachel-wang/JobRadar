from ..base import *

def fetch_bain(scrape, seen, company):
    try:
        r = requests.get(
            "https://www.bain.com/en/api/jobsearch/keyword/get",
            headers={
                **HEADERS,
                "Accept": "application/json",
                "Referer": "https://www.bain.com/careers/find-a-role/",
            },
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)

    jobs = []
    for item in data.get("results") or []:
        title = norm(item.get("JobTitle") or "")
        path = item.get("Link") or ""
        locs = item.get("Location") or []
        if isinstance(locs, list):
            loc = "; ".join(filter(None, [normalize_location(norm(l)) for l in locs]))
        else:
            loc = normalize_location(norm(locs or ""))
        posted = _parse_posted(item.get("PostedDate") or item.get("DatePosted") or "")
        if title and path:
            jobs.append({
                "title": title,
                "url": urljoin("https://www.bain.com", path),
                "location": loc,
                "source": "bain",
                "posted": posted,
            })
    return jobs, None
