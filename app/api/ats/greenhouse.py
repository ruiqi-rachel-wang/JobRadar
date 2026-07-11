from .base import *

# ── Greenhouse ────────────────────────────────────────────────────────────────

def fetch_greenhouse(scrape, seen, company):
    token = scrape.get("board_token","")
    if not token:
        return [], "no board_token"
    include_content = bool(
        scrape.get("greenhouse_content")
        or scrape.get("fetch_descriptions")
        or scrape.get("fetch_experience_details")
    )
    region = (scrape.get("region") or "").lower()
    if region == "eu":
        bases = ["https://boards-api.eu.greenhouse.io", "https://boards-api.greenhouse.io"]
    elif scrape.get("try_eu") is True:
        bases = ["https://boards-api.greenhouse.io", "https://boards-api.eu.greenhouse.io"]
    else:
        bases = ["https://boards-api.greenhouse.io"]

    _MAX_RESPONSE_MB = 80  # boards with 2000+ jobs can return 100MB+ — cap to avoid hour-long hangs
    last_err = "board not found"
    for base in bases:
        suffix = "?content=true" if include_content else ""
        url = f"{base}/v1/boards/{token}/jobs{suffix}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20, stream=True)
            if r.status_code == 404:
                last_err = f"404 board not found: {token}"
                r.close()
                continue
            r.raise_for_status()
            chunks = []
            total = 0
            for chunk in r.iter_content(65536):
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_RESPONSE_MB * 1024 * 1024:
                    r.close()
                    last_err = f"response too large (>{_MAX_RESPONSE_MB}MB): {token}"
                    chunks = None
                    break
            if chunks is None:
                continue
            import json as _json
            data = _json.loads(b"".join(chunks))
            jobs = []
            for j in data.get("jobs",[]):
                title  = norm(j.get("title") or "")
                jurl   = j.get("absolute_url") or ""
                loc    = normalize_location(norm((j.get("location") or {}).get("name") or ""))
                posted = _parse_posted(j.get("first_published") or j.get("published_at") or j.get("updated_at") or "")
                desc   = ""
                if include_content:
                    desc = norm(BeautifulSoup(j.get("content") or "", "lxml").get_text(" ", strip=True))
                if title and jurl:
                    jobs.append({"title":title,"url":jurl,"location":loc,"source":"greenhouse","posted":posted,"description":desc})
            return jobs, None
        except Exception as e:
            last_err = str(e)
            continue

    # Fallback: try the newer job-boards.greenhouse.io API (some companies migrated)
    try:
        new_url = f"https://job-boards.greenhouse.io/embed/job_board/jobs?for={token}"
        r = requests.get(new_url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            data = r.json()
            jobs = []
            for j in data.get("jobs", []):
                title  = norm(j.get("title") or "")
                jurl   = j.get("absolute_url") or ""
                loc_raw = (j.get("location") or {}).get("name") or ""
                loc    = normalize_location(norm(loc_raw))
                posted = _parse_posted(j.get("updated_at") or "")
                if title and jurl:
                    jobs.append({"title":title,"url":jurl,"location":loc,"source":"greenhouse","posted":posted})
            if jobs:
                return jobs, None
            last_err = f"new-board no jobs: {token}"
    except Exception as e:
        last_err = str(e)
    if scrape.get("allow_browser_fallback") is not False and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "greenhouse")
    return [], last_err
