from urllib.parse import quote

from ..base import *


def fetch_johnsoncontrols(scrape, seen, company):
    """Johnson Controls serves jobs from a public Algolia index."""
    app_id = "UM59DWRPA1"
    api_key = "33719eb8d9f28725f375583b7e78dbab"
    index = "production_JCI_jobs"
    api_url = f"https://{app_id}-dsn.algolia.net/1/indexes/{index}/query"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    hits_per_page = int(scrape.get("hits_per_page") or 100)
    jobs = []

    headers = {
        **HEADERS,
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": api_key,
        "Content-Type": "application/json",
    }

    for page in range(max_pages):
        try:
            params = f"hitsPerPage={hits_per_page}&page={page}"
            r = requests.post(api_url, headers=headers, json={"params": params}, timeout=25)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            if jobs:
                break
            return [], str(e)

        hits = data.get("hits") or []
        if not hits:
            break

        for hit in hits:
            title = norm(hit.get("title") or "")
            external_id = str(hit.get("external_id") or hit.get("job_requisition_id") or hit.get("id") or "").strip()
            if not title or not external_id:
                continue

            slug = hit.get("url_slug") or quote(external_id)
            if isinstance(slug, list):
                slug = slug[0] if slug else quote(external_id)
            url = f"https://jobs.johnsoncontrols.com/job/{slug}"
            raw_locs = hit.get("locations_list") or []
            if isinstance(raw_locs, str):
                raw_locs = [raw_locs]
            loc = normalize_location(norm("; ".join(str(x) for x in raw_locs[:3] if x)))
            posted = _parse_posted(hit.get("posted_date") or hit.get("created_at") or "")

            jid = make_id(company, url, title)
            if scrape.get("stop_on_seen") and jid in seen:
                return jobs, None
            jobs.append({
                "title": title,
                "url": url,
                "location": loc,
                "source": "johnsoncontrols",
                "posted": posted,
            })

        nb_pages = int(data.get("nbPages") or 0)
        if nb_pages and page + 1 >= nb_pages:
            break
        if len(hits) < hits_per_page:
            break

    return jobs, None
