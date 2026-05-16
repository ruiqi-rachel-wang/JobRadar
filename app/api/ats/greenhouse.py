from .base import *

# ── Greenhouse ────────────────────────────────────────────────────────────────

def fetch_greenhouse(scrape, seen, company):
    token = scrape.get("board_token","")
    if not token:
        return [], "no board_token"
    # Try US API first, fall back to EU
    for base in ["https://boards-api.greenhouse.io", "https://boards-api.eu.greenhouse.io"]:
        url = f"{base}/v1/boards/{token}/jobs?content=true"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = r.json()
            jobs = []
            for j in data.get("jobs",[]):
                title  = norm(j.get("title") or "")
                jurl   = j.get("absolute_url") or ""
                loc    = normalize_location(norm((j.get("location") or {}).get("name") or ""))
                posted = _parse_posted(j.get("first_published") or j.get("published_at") or j.get("updated_at") or "")
                desc   = norm(BeautifulSoup(j.get("content") or "", "lxml").get_text(" ", strip=True))
                if title and jurl:
                    jobs.append({"title":title,"url":jurl,"location":loc,"source":"greenhouse","posted":posted,"description":desc})
            return jobs, None
        except Exception as e:
            last_err = str(e)
            continue
    if scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "greenhouse")
    return [], last_err
