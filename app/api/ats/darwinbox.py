from .base import *


_TENANT_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.I,
)


def _tenant(value):
    raw = norm(value or "").lower()
    if "://" in raw:
        host = (urlparse(raw).hostname or "").lower()
        match = re.fullmatch(
            r"([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
            r"\.darwinbox\.(in|com)",
            host,
        )
        return match.groups() if match else (None, None)
    if raw.endswith(".com"):
        tenant, tld = raw[:-4], "com"
    elif raw.endswith(".in"):
        tenant, tld = raw[:-3], "in"
    else:
        tenant, tld = raw, "in"
    if not _TENANT_RE.fullmatch(tenant):
        return None, None
    return tenant, tld


def _first_text(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _location_values(item):
    for field in ("tool_tip_locations", "officelocation_show_arr_list"):
        values = item.get(field)
        if isinstance(values, list):
            unique = []
            for value in values:
                if not isinstance(value, str):
                    continue
                cleaned = value.strip().rstrip(",")
                if cleaned and cleaned not in unique:
                    unique.append(cleaned)
            if unique:
                return unique
    fallback = _first_text(
        item.get("locations"),
        item.get("officelocation_show_arr"),
        item.get("country"),
    )
    return [fallback] if fallback else []


def _experience(item):
    minimum = _number(
        item.get("experience_from")
        if item.get("experience_from") not in (None, "")
        else item.get("experience_from_num")
    )
    maximum = _number(
        item.get("experience_to")
        if item.get("experience_to") not in (None, "")
        else item.get("experience_to_num")
    )
    if minimum is None and maximum is None:
        return None, None, None

    def display(value):
        return str(int(value)) if value is not None and value.is_integer() else str(value)

    if minimum is not None and maximum is not None and maximum >= minimum:
        label = (
            f"{display(minimum)}+ years"
            if maximum == minimum
            else f"{display(minimum)}–{display(maximum)} years"
        )
    elif minimum is not None:
        label = f"{display(minimum)}+ years"
    else:
        label = f"Up to {display(maximum)} years"
    return minimum, maximum, label


def fetch_darwinbox(scrape, seen, company):
    """Fetch a Darwinbox tenant's complete first-party paginated JSON feed."""
    configured = (
        scrape.get("darwinbox_tenant")
        or scrape.get("company_slug")
        or scrape.get("list_url")
    )
    tenant, tld = _tenant(configured)
    if not tenant:
        return [], f"invalid Darwinbox tenant: {configured!r}"

    base_url = f"https://{tenant}.darwinbox.{tld}"
    api_url = f"{base_url}/ms/candidateapi/job/alljobs"
    page_size = max(10, min(100, int(scrape.get("page_size") or 100)))
    max_pages = max(1, min(1000, int(scrape.get("max_pages") or 500)))
    headers = {
        **HEADERS,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": base_url,
        "Referer": f"{base_url}/ms/candidatev2/main/careers/allJobs",
    }
    jobs_by_id = {}
    expected_total = None

    for page in range(1, max_pages + 1):
        if scan_deadline_exceeded(scrape):
            return list(jobs_by_id.values()), (
                f"company scan deadline reached after {len(jobs_by_id)} jobs "
                "(partial results)"
            )
        try:
            response = requests.post(
                api_url,
                params={"companyId": "main"},
                json={
                    "companyId": "main",
                    "page": page,
                    "sort_option": "new",
                    "limit": page_size,
                },
                headers=headers,
                timeout=35,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return list(jobs_by_id.values()), (
                f"Darwinbox page {page}: {exc}"
                + (" (partial results)" if jobs_by_id else "")
            )

        if not isinstance(payload, dict) or payload.get("status") != "success":
            return list(jobs_by_id.values()), (
                f"Darwinbox API failure at page {page}"
                + (" (partial results)" if jobs_by_id else "")
            )
        page_items = payload.get("data") or []
        if not isinstance(page_items, list):
            return list(jobs_by_id.values()), (
                f"Darwinbox returned invalid data at page {page}"
                + (" (partial results)" if jobs_by_id else "")
            )
        try:
            expected_total = int(payload.get("job_counts"))
        except (TypeError, ValueError):
            pass

        for item in page_items:
            if not isinstance(item, dict):
                continue
            ats_id = norm(item.get("id") or "")
            title = _first_text(
                item.get("designation_display_name"),
                item.get("title"),
                item.get("designation_name"),
            )
            if not ats_id or not title:
                continue
            locations = _location_values(item)
            employment_raw = _first_text(
                item.get("emp_type_name"),
                item.get("emp_type"),
            )
            remote_raw = item.get("is_remote")
            if isinstance(remote_raw, bool):
                workplace_type = "remote" if remote_raw else "onsite"
            elif isinstance(remote_raw, int):
                workplace_type = "remote" if remote_raw else "onsite"
            else:
                workplace_type = None
            posted_raw = (
                item.get("posted_on")
                if item.get("posted_on") not in (None, "")
                else item.get("created_on")
            )
            exp_min, exp_max, exp_label = _experience(item)
            description_raw = item.get("jd") if isinstance(item.get("jd"), str) else None
            jobs_by_id[ats_id] = {
                "title": title,
                "url": f"{base_url}/ms/candidate/careers/{ats_id}",
                "ats_id": ats_id,
                "requisition_id": norm(item.get("internal_job_code") or "") or None,
                "location": "; ".join(locations),
                "location_raw": locations[0] if locations else None,
                "locations_raw": locations,
                "country_iso": None,
                "workplace_type": workplace_type,
                "employment_type": employment_raw,
                "employment_type_raw": employment_raw,
                "posted": posted_raw,
                "posted_raw": posted_raw,
                "description": description_raw,
                "description_raw": description_raw,
                "department": _first_text(
                    item.get("department_name"),
                    item.get("department_name_only"),
                    item.get("department"),
                ),
                "team": _first_text(
                    item.get("functional_area_name"),
                    item.get("functional_area"),
                ),
                "exp_min": exp_min,
                "exp_max": exp_max,
                "exp_label": exp_label,
                "exp_source": "provider" if exp_label else None,
                "source": "darwinbox",
                "raw_data": {
                    field: item.get(field)
                    for field in (
                        "_id", "parent_company_id", "designation",
                        "department_id", "employee_type", "employee_sub_type",
                        "functional_area", "job_tags", "capability", "country",
                        "experience_from", "experience_to", "created_on",
                    )
                    if item.get(field) not in (None, "", [], {})
                } or None,
            }

        if expected_total is not None and len(jobs_by_id) >= expected_total:
            break
        if not page_items or len(page_items) < page_size:
            break
    else:
        return list(jobs_by_id.values()), (
            f"Darwinbox pagination reached {max_pages} pages "
            f"with {len(jobs_by_id)} of {expected_total or 'unknown'} jobs "
            "(partial results)"
        )

    if expected_total is not None and len(jobs_by_id) < expected_total:
        return list(jobs_by_id.values()), (
            f"partial listing: fetched {len(jobs_by_id)} "
            f"of {expected_total} Darwinbox jobs"
        )
    return list(jobs_by_id.values()), None
