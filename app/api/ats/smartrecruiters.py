from .base import *

# ── SmartRecruiters ───────────────────────────────────────────────────────────

def _fetch_sr_description(slug, posting_id, timeout=15):
    """Fetch job description from the SmartRecruiters detail API."""
    try:
        r = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}",
            headers={**HEADERS, "Accept": "application/json"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return ""
        data = r.json()
        sections = (data.get("jobAd") or {}).get("sections") or {}
        parts = []
        for key in ("jobDescription", "qualifications", "additionalInformation"):
            sec = sections.get(key) or {}
            text = sec.get("text") or ""
            if text:
                parts.append(text)
        return " ".join(parts)
    except Exception:
        return ""


def fetch_smartrecruiters(scrape, seen, company):
    slug = scrape.get("smartrecruiters_company") or scrape.get("company_slug") or scrape.get("slug") or ""
    if not slug:
        return [], "no smartrecruiters_company"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    limit = int(scrape.get("limit", 100))
    jobs = []
    job_ids = {}  # url -> posting_id for detail fetches
    for page in range(max_pages):
        offset = page * limit
        try:
            r = requests.get(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                params={"limit": limit, "offset": offset, **(scrape.get("query_params") or {})},
                headers={**HEADERS, "Accept": "application/json"},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)
        postings = data.get("content") or []
        if not postings:
            break
        for j in postings:
            title = norm(j.get("name") or "")
            posting_id = j.get("id") or ""
            jurl = ""
            if posting_id:
                jurl = f"https://jobs.smartrecruiters.com/{slug}/{posting_id}"
            elif j.get("ref"):
                jurl = (j.get("ref") or "").strip()
            loc_data = j.get("location") or {}
            loc = ""
            if isinstance(loc_data, dict):
                loc = normalize_location(norm(", ".join(
                    x for x in [loc_data.get("city"), loc_data.get("region"), loc_data.get("country")] if x
                )))
            posted = _parse_posted(j.get("releasedDate") or j.get("createdOn") or "")
            if title and jurl:
                jobs.append({"title": title, "url": jurl, "location": loc, "source": "smartrecruiters", "posted": posted})
                if posting_id:
                    job_ids[jurl] = posting_id
        total = int(data.get("totalFound") or 0)
        if total and offset + limit >= total:
            break

    # Fetch descriptions for jobs without them
    max_desc = int(scrape.get("max_desc_details", 500))
    desc_workers = int(scrape.get("detail_workers", 4))
    desc_targets = []
    for j in jobs:
        pid = job_ids.get(j["url"])
        if not pid:
            continue
        jid_key = make_id(company, j["url"], j["title"])
        existing = seen.get(jid_key) if isinstance(seen, dict) else None
        if not (isinstance(existing, dict) and existing.get("has_description")):
            desc_targets.append((j, pid))

    if desc_targets[:max_desc]:
        def _get_desc(args):
            job, pid = args
            return job, _fetch_sr_description(slug, pid)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, desc_workers)) as ex:
            for job, desc in ex.map(_get_desc, desc_targets[:max_desc]):
                if desc:
                    job["description"] = desc

    return jobs, None
