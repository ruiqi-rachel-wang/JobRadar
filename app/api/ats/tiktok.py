from .base import *

# ── TikTok / Life at TikTok ───────────────────────────────────────────────────
#
# Uses the internal REST API at api.lifeattiktok.com (POST, paginated by offset).
# Endpoint: POST /api/v1/public/supplier/search/job/posts
# Response: {"code":0,"data":{"job_post_list":[...],"count":N}}

_API_URL = "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
_JOB_BASE = "https://lifeattiktok.com/search/"

_API_HEADERS = {
    **HEADERS,
    "Content-Type":   "application/json",
    "origin":         "https://lifeattiktok.com",
    "website-path":   "tiktok",
    "accept-language": "en",
}

def fetch_tiktok(scrape, seen, company):
    limit = int(scrape.get("page_size", 100))
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    jobs = []

    for page in range(max_pages):
        offset = page * limit
        body = {"keyword": "", "limit": limit, "offset": offset,
                "recruitment_id_list": [], "job_category_id_list": [],
                "subject_id_list": [], "location_code_list": []}
        try:
            r = requests.post(_API_URL, json=body, headers=_API_HEADERS, timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)

        if data.get("code") != 0:
            return jobs, f"API error code {data.get('code')}: {data.get('message','')}"

        items = (data.get("data") or {}).get("job_post_list") or []
        if not items:
            break

        for item in items:
            jid = str(item.get("id") or "")
            title = norm(item.get("title") or "")
            if not jid or not title:
                continue

            jurl = _JOB_BASE + jid

            city_info = item.get("city_info") or {}
            city = city_info.get("en_name") or ""
            parent = (city_info.get("parent") or {}).get("en_name") or ""
            loc = normalize_location(", ".join(x for x in [city, parent] if x))

            jobs.append({"title": title, "url": jurl, "location": loc,
                         "source": "tiktok", "posted": ""})

        total = (data.get("data") or {}).get("count") or 0
        if total and offset + limit >= total:
            break
        if len(items) < limit:
            break

    return jobs, None
