from .base import *

# ── Microsoft ─────────────────────────────────────────────────────────────────

def fetch_microsoft(scrape, seen, company):
    """GET gcsservices.careers.microsoft.com/search/api/v1/search"""
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    jobs, page, ps = [], 1, 20
    for _ in range(max_pages):
        try:
            try:
                r = requests.get(
                    "https://gcsservices.careers.microsoft.com/search/api/v1/search",
                    params={"l":"en_us","pg":page,"pgSz":ps,"o":"Recent","flt":"true"},
                    headers={**HEADERS,"Referer":"https://jobs.careers.microsoft.com/"},
                    timeout=20)
            except requests.exceptions.SSLError:
                r = requests.get(
                    "https://gcsservices.careers.microsoft.com/search/api/v1/search",
                    params={"l":"en_us","pg":page,"pgSz":ps,"o":"Recent","flt":"true"},
                    headers={**HEADERS,"Referer":"https://jobs.careers.microsoft.com/"},
                    timeout=20, verify=False)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)
        result   = (data.get("operationResult") or {}).get("result") or {}
        postings = result.get("jobs",[])
        if not postings: break
        for j in postings:
            title = norm(j.get("title") or "")
            jid_s = j.get("jobId") or ""
            jurl  = f"https://jobs.careers.microsoft.com/global/en/job/{jid_s}/" if jid_s else ""
            loc   = normalize_location(norm(j.get("officeLocation") or j.get("primaryWorkLocation") or ""))
            posted = _parse_posted(j.get("postingDate") or j.get("postedDate") or j.get("createdDateTime") or j.get("createdDate") or "")
            if title and jurl:
                jobs.append({"title":title,"url":jurl,"location":loc,"source":"microsoft","posted":posted})
        if page*ps >= result.get("totalJobs",0): break
        page += 1
        time.sleep(0.3)
    return jobs, None

