from .base import *

# ── Eightfold ─────────────────────────────────────────────────────────────────

def fetch_eightfold(scrape, seen, company):
    """Eightfold careers API, e.g. https://albemarle.eightfold.ai/careers."""
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    host = urlparse(list_url).netloc
    if not host:
        return [], f"cannot parse Eightfold URL: {list_url}"
    domain = scrape.get("eightfold_domain") or scrape.get("domain") or ""
    if not domain:
        prefix = host.split(".eightfold.ai")[0]
        domain = f"{prefix}.com"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    limit = int(scrape.get("limit", 100))
    jobs = []
    for page in range(max_pages):
        start = page * limit
        try:
            r = requests.get(
                f"https://{host}/api/apply/v2/jobs",
                params={"domain": domain, "start": start, "num": limit},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            if scrape.get("fallback") == "playwright":
                from .playwright import _fetch_playwright_with_source
                return _fetch_playwright_with_source(scrape, seen, company, "eightfold")
            return jobs, str(e)
        positions = data.get("positions") or []
        if not positions:
            break
        for j in positions:
            title = norm(j.get("name") or j.get("posting_name") or "")
            jurl = j.get("canonicalPositionUrl") or ""
            if not jurl and j.get("id"):
                jurl = f"https://{host}/careers/job/{j.get('id')}"
            loc = normalize_location(norm(j.get("location") or ((j.get("locations") or [""])[0])))
            posted = ""
            ts = j.get("t_create") or j.get("t_update")
            if ts:
                try:
                    posted = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
                except Exception:
                    posted = ""
            if title and jurl:
                jobs.append({"title": title, "url": jurl, "location": loc, "source": "eightfold", "posted": posted})
        count = int(data.get("count") or 0)
        if count and start + limit >= count:
            break
    return jobs, None

