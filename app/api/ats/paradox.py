from .base import *


def _paradox_location(job):
    raw_locations = []
    for item in job.get("locations") or []:
        if not isinstance(item, dict):
            continue
        raw = norm(
            item.get("locationText")
            or item.get("locationParsedText")
            or ", ".join(
                str(item.get(key) or "").strip()
                for key in ("city", "state", "country")
                if str(item.get(key) or "").strip()
            )
        )
        if raw and raw not in raw_locations:
            raw_locations.append(raw)
    return raw_locations


def fetch_paradox(scrape, seen, company):
    """Fetch a Paradox career site's first-party JSON job feed."""
    list_url = (scrape.get("list_url") or "").strip()
    if not list_url:
        return [], "no list_url"
    parsed = urlparse(list_url)
    if not parsed.scheme or not parsed.netloc:
        return [], f"invalid Paradox URL: {list_url}"

    base_url = f"{parsed.scheme}://{parsed.netloc}"
    api_url = f"{base_url}/api/get-jobs"
    page_size = max(25, min(100, int(scrape.get("page_size") or 100)))
    page_workers = max(1, min(8, int(scrape.get("page_workers") or 6)))
    hard_max_pages = max(1, int(scrape.get("hard_max_pages") or 500))
    total_tolerance = max(0, int(scrape.get("reported_total_tolerance") or 0))
    sort_by = (scrape.get("sort_by") or "title").strip()
    languages = scrape.get("site_available_languages") or ["en"]
    thread_state = threading.local()

    headers = {
        **HEADERS,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": base_url,
        "Referer": list_url,
    }
    body = {
        "disable_switch_search_mode": bool(
            scrape.get("disable_switch_search_mode", False)
        ),
        "site_available_languages": languages,
    }

    def _fetch_page(page_number):
        if scan_deadline_exceeded(scrape):
            raise TimeoutError("company scan deadline reached")
        # The endpoint requires the signed cookie issued by the public jobs
        # page. Sessions are thread-local, so establish it once per worker.
        if not getattr(thread_state, "ready", False):
            handshake = requests.get(list_url, headers=HEADERS, timeout=30)
            handshake.raise_for_status()
            thread_state.ready = True
        response = requests.post(
            api_url,
            params={
                "page_size": page_size,
                "page_number": page_number,
                "sort_by": sort_by,
            },
            json=body,
            headers=headers,
            timeout=35,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Paradox response was not a JSON object")
        return data

    try:
        first = _fetch_page(1)
    except Exception as exc:
        return [], str(exc)

    total = max(0, int(first.get("totalJob") or 0))
    page_count = min(
        hard_max_pages,
        max(1, (total + page_size - 1) // page_size),
    )
    pages = {1: first}
    page_errors = []

    if page_count > 1:
        def _try_page(number):
            try:
                return number, _fetch_page(number), None
            except Exception as exc:
                return number, None, str(exc)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=page_workers
        ) as executor:
            for number, data, error in executor.map(
                _try_page, range(2, page_count + 1)
            ):
                if data is not None:
                    pages[number] = data
                if error:
                    page_errors.append(f"page {number}: {error}")

    page_payloads = [pages[number] for number in sorted(pages)]
    observed_totals = [
        int(data.get("totalJob") or 0)
        for data in page_payloads
        if int(data.get("totalJob") or 0) > 0
    ]
    # A high-volume board changes while its pages are in flight. There is no
    # server-side snapshot token, so compare against the smallest total
    # actually observed during this bounded parallel read. An initial count
    # can legitimately fall when jobs close before the last page responds.
    expected_total = min(observed_totals) if observed_totals else total

    def _raw_item_key(item):
        if not isinstance(item, dict):
            return None
        return (
            item.get("uniqueID")
            or item.get("requisitionID")
            or item.get("reference")
            or item.get("originalURL")
        )

    preliminary_ids = {
        key
        for data in page_payloads
        for item in (data.get("jobs") or [])
        if (key := _raw_item_key(item))
    }
    # Large active boards can shift a few rows across page boundaries while
    # the first parallel pass is in flight. A second bounded pass is cheaper
    # and more accurate than accepting a permanent tolerance: merge immutable
    # ATS IDs from both snapshots, then run canonical enrichment only once.
    if (
        not page_errors
        and expected_total
        and len(preliminary_ids) < expected_total
        and scrape.get("retry_incomplete", True)
    ):
        def _retry_page(number):
            try:
                return _fetch_page(number)
            except Exception:
                return None

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=page_workers
        ) as executor:
            retry_payloads = [
                data
                for data in executor.map(
                    _retry_page, range(1, page_count + 1)
                )
                if data is not None
            ]
        page_payloads.extend(retry_payloads)
        retry_totals = [
            int(data.get("totalJob") or 0)
            for data in retry_payloads
            if int(data.get("totalJob") or 0) > 0
        ]
        if retry_totals:
            expected_total = min([expected_total, *retry_totals])

    jobs_by_id = {}
    for data in page_payloads:
        for item in data.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            title = norm(item.get("title") or "")
            original_url = norm(item.get("originalURL") or "")
            url = urljoin(base_url + "/", original_url)
            ats_id = norm(
                item.get("uniqueID")
                or item.get("requisitionID")
                or item.get("reference")
                or ""
            )
            if not title or not (url or ats_id):
                continue

            raw_locations = _paradox_location(item)
            employment_raw = item.get("employmentType")
            remote_value = item.get("isRemote")
            if remote_value is True:
                workplace_type = "remote"
            elif remote_value is False:
                workplace_type = "onsite"
            else:
                workplace_type = None
            description = item.get("description") or None
            key = ats_id or url
            normalized_locations = [
                normalize_location(value)
                for value in raw_locations
                if normalize_location(value)
            ]
            jobs_by_id[key] = {
                "title": title,
                "url": url,
                "ats_id": ats_id or None,
                "requisition_id": norm(
                    item.get("requisitionID")
                    or item.get("reference")
                    or ""
                ) or None,
                "apply_url": item.get("applyURL") or None,
                "location": "; ".join(normalized_locations),
                "location_raw": raw_locations[0] if raw_locations else None,
                "locations_raw": raw_locations,
                "workplace_type": workplace_type,
                "employment_type": employment_raw,
                "employment_type_raw": employment_raw,
                "description": description,
                "description_raw": description,
                "source": "paradox",
                "raw_data": {
                    field: item.get(field)
                    for field in (
                        "sourceID", "companyID", "companyName", "brandName",
                        "postingType", "employmentStatus", "customFields",
                        "facilityName", "jobCardExtraFields",
                    )
                    if item.get(field) not in (None, "", [], {})
                } or None,
            }

    error = None
    if page_errors:
        error = "; ".join(page_errors[:5]) + " (partial results)"
    elif (
        expected_total
        and len(jobs_by_id) + total_tolerance < expected_total
    ):
        error = (
            f"partial listing: fetched {len(jobs_by_id)} "
            f"of {expected_total} jobs"
        )
    return list(jobs_by_id.values()), error
