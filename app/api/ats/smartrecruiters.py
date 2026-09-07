from .base import *

# ── SmartRecruiters ───────────────────────────────────────────────────────────

def _fetch_sr_description(slug, posting_id, timeout=15):
    """Fetch structured SmartRecruiters detail metadata."""
    try:
        r = requests.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}",
            headers={**HEADERS, "Accept": "application/json"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        sections = (data.get("jobAd") or {}).get("sections") or {}
        parts = []
        for key in ("jobDescription", "qualifications", "additionalInformation"):
            sec = sections.get(key) or {}
            text = sec.get("text") or ""
            if text:
                heading = sec.get("title") or key.replace("Information", " information").replace("job", "Job ")
                parts.append(f"<h3>{heading}</h3>\n{text}")
        return {
            "description": "\n\n".join(parts) or None,
            "apply_url": data.get("applyUrl"),
            "raw_data": {
                key: data.get(key)
                for key in ("language", "experienceLevel", "customField")
                if data.get(key)
            } or None,
        }
    except Exception:
        return {}


def fetch_smartrecruiters(scrape, seen, company):
    slug = scrape.get("smartrecruiters_company") or scrape.get("company_slug") or scrape.get("slug") or ""
    if not slug:
        return [], "no smartrecruiters_company"
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    limit = int(scrape.get("limit", 100))
    jobs = []
    job_ids = {}  # url -> posting_id for detail fetches
    for page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return jobs, "company scan deadline reached (partial results)"
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
            loc_raw = ""
            if isinstance(loc_data, dict):
                loc_raw = norm(", ".join(
                    x for x in [loc_data.get("city"), loc_data.get("region"), loc_data.get("country")] if x
                ))
            department = j.get("department") or {}
            function = j.get("function") or {}
            emp = j.get("typeOfEmployment") or {}
            emp_raw = (
                emp.get("label") or emp.get("id")
                if isinstance(emp, dict) else emp
            )
            if title and jurl:
                jobs.append({
                    "title": title,
                    "url": jurl,
                    "ats_id": str(posting_id) or None,
                    "requisition_id": j.get("refNumber"),
                    "location": loc_raw,
                    "location_raw": loc_raw or None,
                    "country_iso": (
                        str(loc_data.get("country")).upper()
                        if isinstance(loc_data, dict)
                        and len(str(loc_data.get("country") or "")) == 2
                        else None
                    ),
                    "remote_mode": (
                        "remote"
                        if isinstance(loc_data, dict) and loc_data.get("remote") is True
                        else "onsite"
                        if isinstance(loc_data, dict) and loc_data.get("remote") is False
                        else None
                    ),
                    "source": "smartrecruiters",
                    "posted_at": j.get("releasedDate") or j.get("createdOn"),
                    "employment_type": emp_raw,
                    "employment_type_raw": emp_raw,
                    "department": (
                        department.get("label")
                        if isinstance(department, dict) else department
                    ),
                    "team": (
                        function.get("label")
                        if isinstance(function, dict) else function
                    ),
                    "raw_data": {
                        key: j.get(key)
                        for key in (
                            "industry", "function", "department",
                            "experienceLevel", "language",
                        )
                        if j.get(key)
                    } or None,
                })
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
        jid_key = make_id(
            company, j["url"], j["title"], "smartrecruiters", j.get("ats_id")
        )
        existing = (
            seen.get(jid_key)
            or seen.get(make_id(company, j["url"], j["title"]))
            if isinstance(seen, dict) else None
        )
        if not (isinstance(existing, dict) and existing.get("has_description")):
            desc_targets.append((j, pid))

    if desc_targets[:max_desc]:
        def _get_desc(args):
            job, pid = args
            return job, _fetch_sr_description(slug, pid)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, desc_workers)) as ex:
            for job, detail in ex.map(_get_desc, desc_targets[:max_desc]):
                if detail.get("description"):
                    job["description"] = detail["description"]
                    job["description_raw"] = detail["description"]
                if detail.get("apply_url"):
                    job["apply_url"] = detail["apply_url"]
                if detail.get("raw_data"):
                    job.setdefault("raw_data", {}).update(detail["raw_data"])

    return jobs, None
