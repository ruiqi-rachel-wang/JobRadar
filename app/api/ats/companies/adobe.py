from ..base import *

_LIST_URL = "https://careers.adobe.com/us/en/search-results"
_JOB_BASE  = "https://careers.adobe.com/us/en/details"

def fetch_adobe(scrape, seen, company):
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    limit     = 10
    jobs      = []

    for page in range(max_pages):
        try:
            r = requests.get(_LIST_URL,
                params={"format": "json", "from": page * limit},
                headers=HEADERS, timeout=20)
            r.raise_for_status()
            items = r.json()["ddoResults"]["eagerLoadRefineSearch"]["data"]["jobs"]
        except Exception as e:
            return jobs, str(e)

        if not items:
            break

        for j in items:
            title  = norm(j.get("title") or "")
            req_id = (j.get("reqId") or j.get("jobId") or "").strip()
            if not title or not req_id:
                continue
            jurl = j.get("applyUrl") or f"{_JOB_BASE}/{req_id}/"
            raw_loc = ", ".join(x for x in [
                j.get("city") or "", j.get("state") or "", j.get("country") or ""
            ] if x)
            loc    = normalize_location(norm(raw_loc))
            posted = _parse_posted(j.get("postedDate") or j.get("dateCreated") or "")
            jobs.append({"title": title, "url": jurl, "location": loc,
                         "source": "adobe", "posted": posted})

        if len(items) < limit:
            break

    return jobs, None
