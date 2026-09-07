"""TikTok's public careers API adapter."""

from .base import HEADERS, make_id, norm, requests


DEFAULT_API_URL = (
    "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts"
)


def _name(value):
    if not isinstance(value, dict):
        return ""
    return norm(value.get("en_name") or value.get("i18n_name") or value.get("name") or "")


def _location_parts(city_info):
    parts = []
    current = city_info
    while isinstance(current, dict):
        value = _name(current)
        if value and value not in parts:
            parts.append(value)
        current = current.get("parent")
    return parts


def _to_job(post):
    job_id = str(post.get("id") or "").strip()
    title = norm(post.get("title") or "")
    if not job_id or not title:
        return None

    location_parts = _location_parts(post.get("city_info"))
    location_raw = ", ".join(location_parts)
    description_parts = [
        norm(post.get("description") or ""),
        norm(post.get("requirement") or ""),
    ]
    description = "\n\n".join(value for value in description_parts if value)
    employment = _name(post.get("recruit_type"))
    department = _name(post.get("job_category"))

    return {
        "title": title,
        "url": f"https://lifeattiktok.com/search/{job_id}",
        "ats_id": job_id,
        "location": location_raw,
        "location_raw": location_raw or None,
        "locations_raw": [location_raw] if location_raw else [],
        "source": "tiktok",
        "description": description or None,
        "description_raw": description or None,
        "employment_type": employment or None,
        "employment_type_raw": employment or None,
        "department": department or None,
        "raw_data": {"job_code": post.get("code")} if post.get("code") else None,
    }


def fetch_tiktok(scrape, seen, company):
    api_url = scrape.get("api_url") or DEFAULT_API_URL
    page_size = max(1, min(int(scrape.get("page_size") or 500), 500))
    max_pages = max(1, int(scrape.get("hard_max_pages") or 20))
    headers = {
        **HEADERS,
        "Content-Type": "application/json",
        "accept-language": "en-US",
        "origin": "https://lifeattiktok.com",
        "website-path": "tiktok",
    }
    jobs = []
    error = None

    for page in range(max_pages):
        payload = {
            "job_category_id_list": [],
            "location_code_list": [],
            "recruitment_id_list": [],
            "subject_id_list": [],
            "tag_id_list": [],
            "keyword": "",
            "limit": page_size,
            "offset": page * page_size,
        }
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=45)
            response.raise_for_status()
            body = response.json()
            data = body.get("data") or {}
            postings = data.get("job_post_list") or []
            total = int(data.get("count") or 0)
        except Exception as exc:
            error = str(exc)
            break

        page_hit_seen = False
        for post in postings:
            if not isinstance(post, dict):
                continue
            job = _to_job(post)
            if not job:
                continue
            jobs.append(job)
            if scrape.get("stop_on_seen") and make_id(
                company,
                job["url"],
                job["title"],
                job["source"],
                job["ats_id"],
            ) in seen:
                page_hit_seen = True

        if page_hit_seen or not postings or len(jobs) >= total:
            break

    if jobs:
        return jobs, error
    return [], error or "no jobs found"
