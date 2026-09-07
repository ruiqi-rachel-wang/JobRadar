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
        title = norm(j.get("text") or "")
        jurl = j.get("hostedUrl") or ""
        categories = j.get("categories") or {}
        location_raw = norm(categories.get("location") or "")
        desc_parts = []
        intro = j.get("description") or ""
        if intro:
            desc_parts.append(str(intro))
        for list_item in j.get("lists") or []:
            if not isinstance(list_item, dict):
                continue
            heading = norm(list_item.get("text") or "")
            content = list_item.get("content") or ""
            if isinstance(content, list):
                content = "\n".join(str(value) for value in content if value)
            if content:
                desc_parts.append(
                    (f"<h3>{heading}</h3>\n" if heading else "") + str(content)
                )
        if not desc_parts and j.get("descriptionPlain"):
            desc_parts.append(str(j["descriptionPlain"]))
        description_raw = "\n\n".join(desc_parts)
        commitment = norm(categories.get("commitment") or "")
        workplace_type = norm(j.get("workplaceType") or "")
        if title and jurl:
            jobs.append({
                "title": title,
                "url": jurl,
                "ats_id": str(j.get("id") or "") or None,
                "apply_url": j.get("applyUrl"),
                "location": location_raw,
                "location_raw": location_raw or None,
                "source": "lever",
                "posted_at": j.get("createdAt"),
                "description": description_raw or None,
                "description_raw": description_raw or None,
                "employment_type": commitment or None,
                "employment_type_raw": commitment or None,
                "department": categories.get("department"),
                "team": categories.get("team"),
                "workplace_type": workplace_type or None,
                "raw_data": {
                    key: value for key, value in {
                        "categories": categories,
                        "workplaceType": j.get("workplaceType"),
                        "country": j.get("country"),
                        "tags": j.get("tags"),
                    }.items() if value
                } or None,
            })
    return jobs, None
