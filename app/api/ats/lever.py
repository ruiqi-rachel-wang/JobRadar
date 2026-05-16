from .base import *

# ── Lever ─────────────────────────────────────────────────────────────────────

def fetch_lever(scrape, seen, company):
    slug = scrape.get("lever_slug","")
    if not slug:
        return [], "no lever_slug"
    url  = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)
    if not isinstance(data, list): return [], "unexpected response"
    jobs = []
    for j in data:
        title      = norm(j.get("text") or "")
        jurl       = j.get("hostedUrl") or ""
        loc        = normalize_location(norm((j.get("categories") or {}).get("location") or ""))
        created_ms = j.get("createdAt") or 0
        posted     = datetime.fromtimestamp(created_ms/1000).strftime("%Y-%m-%d") if created_ms else ""
        desc_parts  = [j.get("descriptionPlain") or j.get("description") or ""]
        for list_item in j.get("lists") or []:
            desc_parts.append(list_item.get("text") or "")
            desc_parts.extend(list_item.get("content") or [])
        desc = norm(" ".join(str(x) for x in desc_parts if x))
        if title and jurl:
            jobs.append({"title":title,"url":jurl,"location":loc,"source":"lever","posted":posted,"description":desc})
    return jobs, None
