from .base import *

# ── Rippling ──────────────────────────────────────────────────────────────────

def fetch_rippling(scrape, seen, company):
    list_url = scrape.get("list_url") or "https://www.rippling.com/careers/open-roles"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        return [], str(e)
    soup = BeautifulSoup(r.text, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return [], "no __NEXT_DATA__ jobs payload"
    try:
        data = json.loads(script.string)
    except Exception as e:
        return [], str(e)
    items = (((data.get("props") or {}).get("pageProps") or {}).get("jobs") or {}).get("items") or []
    jobs = []
    for j in items:
        title = norm(j.get("name") or "")
        jurl = j.get("url") or ""
        loc_parts = []
        for loc_data in (j.get("locations") or []):
            if isinstance(loc_data, dict):
                s = normalize_location(norm(", ".join(
                    x for x in [loc_data.get("city"), loc_data.get("stateCode"), loc_data.get("country")] if x
                )))
                if s:
                    loc_parts.append(s)
        loc = "; ".join(loc_parts)
        if title and jurl:
            jobs.append({"title": title, "url": jurl, "location": loc, "source": "rippling", "posted": ""})
    return jobs, None


