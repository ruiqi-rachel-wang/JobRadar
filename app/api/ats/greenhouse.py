from .base import *
from urllib.parse import quote
from ..utils import _COUNTRY_ALIASES


def _greenhouse_locations(posting):
    primary = norm((posting.get("location") or {}).get("name") or "")
    normalized = normalize_location(primary)
    # Some employers publish only "Hybrid" or a country in location.name,
    # with the actual advertised cities in a public job-location metadata
    # field. Office assignments alone are not reliable: a Remote office can
    # carry the employer's headquarters address.
    if (not normalized or normalized.lower() in {"multiple locations", "various locations"}
            or normalized in _COUNTRY_ALIASES.values()):
        fields = {
            "job posting location", "job posting locations", "job location",
            "primary posting location", "all locations",
        }
        values = []
        for field in posting.get("metadata") or []:
            if not isinstance(field, dict) or norm(field.get("name") or "").lower() not in fields:
                continue
            entries = field.get("value") or []
            if isinstance(entries, str):
                entries = [entries]
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, str) and normalize_location(entry):
                    values.extend(part.strip() for part in re.split(r'[;|│]', entry) if part.strip())
        if values:
            return list(dict.fromkeys(values))
    return [part.strip() for part in re.split(r'[;|│]', primary) if part.strip()]


def _public_job_url(scrape, token, region, posting):
    host = "job-boards.eu.greenhouse.io" if region == "eu" else "job-boards.greenhouse.io"
    posting_id = posting.get("id")
    if posting_id and scrape.get("use_embedded_job_urls"):
        return (f"https://{host}/embed/job_app?for={quote(token, safe='')}"
                f"&token={quote(str(posting_id), safe='')}")
    if posting_id and scrape.get("use_hosted_job_urls"):
        return f"https://{host}/{quote(token, safe='')}/jobs/{posting_id}"
    return posting.get("absolute_url") or ""

# ── Greenhouse ────────────────────────────────────────────────────────────────

def fetch_greenhouse(scrape, seen, company):
    token = scrape.get("board_token","")
    if not token:
        return [], "no board_token"
    # Greenhouse embeds the complete description in the board response, so
    # content=true avoids hundreds of per-job detail requests while preserving
    # the qualifications needed for experience/skills extraction.
    include_content = scrape.get("greenhouse_content") is not False
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
                jurl   = _public_job_url(scrape, token, region, j)
                loc_raw = norm((j.get("location") or {}).get("name") or "")
                posted_raw = j.get("first_published") or j.get("published_at") or j.get("updated_at")
                desc = j.get("content") or None if include_content else None
                metadata = j.get("metadata") or []
                departments = j.get("departments") or []
                offices = j.get("offices") or []
                if title and jurl:
                    jobs.append({
                        "title": title,
                        "url": jurl,
                        "ats_id": str(j.get("id") or "") or None,
                        "requisition_id": (
                            str(j.get("requisition_id"))
                            if j.get("requisition_id") is not None else None
                        ),
                        "location": loc_raw,
                        "location_raw": loc_raw or None,
                        "locations_raw": _greenhouse_locations(j),
                        "source": "greenhouse",
                        "posted_at": posted_raw,
                        "description": desc,
                        "description_raw": desc,
                        "department": (
                            departments[0].get("name")
                            if departments and isinstance(departments[0], dict)
                            else None
                        ),
                        "raw_data": {
                            key: value for key, value in {
                                "metadata": metadata,
                                "departments": departments,
                                "offices": offices,
                                "internal_job_id": j.get("internal_job_id"),
                            }.items() if value
                        } or None,
                    })
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
                jurl   = _public_job_url(scrape, token, region, j)
                loc_raw = (j.get("location") or {}).get("name") or ""
                if title and jurl:
                    jobs.append({
                        "title": title,
                        "url": jurl,
                        "ats_id": str(j.get("id") or "") or None,
                        "location": norm(loc_raw),
                        "location_raw": norm(loc_raw) or None,
                        "locations_raw": _greenhouse_locations(j),
                        "source": "greenhouse",
                        "posted_at": j.get("updated_at"),
                    })
            if jobs:
                return jobs, None
            last_err = f"new-board no jobs: {token}"
    except Exception as e:
        last_err = str(e)
    if scrape.get("allow_browser_fallback") is not False and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "greenhouse")
    return [], last_err
