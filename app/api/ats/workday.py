from .base import *
from ..utils import (
    _CANADA_PROVINCES,
    _COUNTRY_ALIASES,
    _COUNTRY_CODES,
    _US_STATES,
)

# ── Workday ───────────────────────────────────────────────────────────────────

def _workday_location_parts(raw):
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        descriptor = raw.get("descriptor") or raw.get("name")
        nested_country = raw.get("country")
        if isinstance(nested_country, dict):
            country_descriptor = nested_country.get("descriptor") or ""
            nested_country = (
                nested_country.get("alpha2Code")
                if country_descriptor.lower() == "georgia"
                else country_descriptor
            ) or nested_country.get("alpha2Code") or nested_country.get("id")
        if descriptor and nested_country:
            return [f"{descriptor}, {nested_country}"]
        for keys in (
            ("city", "region", "country"),
            ("city", "state", "country"),
            ("city", "countryRegion", "country"),
            ("descriptor",),
            ("location",),
            ("name",),
        ):
            vals = []
            for k in keys:
                value = raw.get(k)
                if isinstance(value, dict):
                    value = value.get("descriptor") or value.get("alpha2Code") or value.get("id")
                if value:
                    vals.append(value)
            if vals:
                return [", ".join(str(v) for v in vals)]
        return []
    if isinstance(raw, list):
        parts = []
        for item in raw:
            parts.extend(_workday_location_parts(item))
        return parts
    return []

def _workday_requisition_id(posting):
    """Workday bullet columns can begin with a date or location, not an ID."""
    def identifier(value):
        value = str(value or '').strip()
        return (value if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', value)
                and re.search(r'\d', value)
                and not re.fullmatch(r'\d{4}-\d{2}-\d{2}|\d+(?:Locations?|Days?|Hours?)', value, re.I)
                else None)
    for value in posting.get('bulletFields') or []:
        candidate = identifier(value)
        if candidate:
            return candidate
    tail = (posting.get('externalPath') or '').rsplit('/', 1)[-1]
    return identifier(tail.rsplit('_', 1)[-1]) if '_' in tail else None


def _workday_location(posting):
    parts = []
    for key in ("locations", "location", "jobLocations"):
        parts.extend(_workday_location_parts(posting.get(key)))
    if not parts:
        loc_text = posting.get("locationsText") or ""
        if loc_text:
            parts = re.split(r'\s*(?:;|\||/|\band\b)\s*', loc_text)
    if not parts:
        bullets = posting.get("bulletFields") or []
        for bullet in bullets:
            if not bullet:
                continue
            raw_bullet = norm(str(bullet))
            if re.fullmatch(r'(?:ATCI[-\w]*|(?:REQ|JR|WD|EB|R|S)[-_]?\d[\w-]*|\d{5,})', raw_bullet, re.I):
                continue
            loc = normalize_location(norm(str(bullet)))
            if loc:
                parts = [bullet]
                break

    normalized = []
    seen_locs = set()
    for part in parts:
        loc = normalize_location(norm(str(part)))
        if not loc:
            continue
        key = loc.lower()
        if key not in seen_locs:
            normalized.append(loc)
            seen_locs.add(key)

    if not normalized:
        return ""
    if len(normalized) == 1:
        return normalized[0]
    return "; ".join(normalized)


def _workday_raw_locations(posting):
    parts = []
    for key in ("locations", "location", "jobLocations"):
        parts.extend(_workday_location_parts(posting.get(key)))
    if not parts and posting.get("locationsText"):
        parts = [posting.get("locationsText")]
    output = []
    for part in parts:
        value = norm(str(part))
        if value and value not in output:
            output.append(value)
    return output

def _workday_location_has_country(location):
    """Whether a normalized Workday location identifies a country."""
    location = normalize_location(location or "")
    if not location:
        return False
    parts = [part.strip() for part in location.split(";") if part.strip()]
    if not parts:
        return False
    known_countries = {value.lower() for value in _COUNTRY_ALIASES.values()}
    for part in parts:
        lower = part.lower()
        if any(lower == country or lower.endswith(f", {country}") for country in known_countries):
            continue
        codes = re.findall(r"\b[A-Z]{2}\b", part)
        if any(code in _US_STATES or code in _CANADA_PROVINCES or code in _COUNTRY_CODES for code in codes):
            continue
        return False
    return True

def _workday_detail_location(info):
    if not isinstance(info, dict):
        return ""
    country = ""
    country_obj = info.get("country")
    if isinstance(country_obj, dict):
        country = country_obj.get("descriptor") or ""
    elif isinstance(country_obj, str):
        country = country_obj

    parts = _workday_location_parts(info.get("location"))
    # Workday often exposes both a public city ("Irving, TX") and an
    # internal office descriptor ("Irving - 6011 Connection") for the same
    # primary location. Use the requisition location only as a fallback so the
    # UI shows the advertised locations instead of counting one office twice.
    if not parts or all(
        normalize_location(norm(str(part))) == "Multiple Locations"
        for part in parts
    ):
        parts = _workday_location_parts(info.get("jobRequisitionLocation"))
    primary_count = len(parts)
    parts.extend(_workday_location_parts(info.get("additionalLocations")))

    normalized = []
    seen_locs = set()
    for index, part in enumerate(parts):
        raw = norm(str(part))
        has_country_suffix = re.search(r'\b(?:US|USA|United States(?: of America)?|[A-Z]{2})(?:\s+\d{2,6})?$', raw, re.I)
        # The requisition country belongs to its primary location. An
        # additional office can be in another country (e.g. Getafe/Hamburg).
        part_country = country if index < primary_count else ""
        country_prefix = bool(part_country and re.match(
            rf'^(?:US|USA|United States(?: of America)?|{re.escape(part_country)})\s*[-,]', raw, re.I))
        if part_country and not country_prefix and "," not in raw and raw.lower() not in ("remote", "multiple locations") and not has_country_suffix:
            raw = f"{raw}, {part_country}"
        elif part_country and raw.lower() == "remote":
            raw = f"Remote, {part_country}"
        loc = normalize_location(raw)
        if len(parts) > 1 and loc in ("US", "UK", "Canada", "India", "Australia", "Germany", "France"):
            continue
        if loc and loc != "Multiple Locations" and loc.lower() not in seen_locs:
            normalized.append(loc)
            seen_locs.add(loc.lower())
    return "; ".join(normalized)

def _fetch_workday_detail(base_url, tenant, board, path):
    if not path:
        return {}
    detail_url = f"{base_url}/wday/cxs/{tenant}/{board}{path}"
    try:
        r = None
        for attempt in range(3):
            r = requests.get(
                detail_url,
                headers={**HEADERS, "Accept": "application/json"},
                timeout=20,
            )
            if r.status_code not in (403, 429):
                break
            time.sleep(1.5 * (attempt + 1))
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}

    info = data.get("jobPostingInfo") if isinstance(data, dict) else {}
    loc = _workday_detail_location(info)
    # The detail endpoint's additionalLocations field is authoritative. The
    # list-page helper intentionally does not read it, so derive the complete
    # stored list from the normalized detail result here.
    raw_locations = [
        part.strip() for part in (loc or "").split(";") if part.strip()
    ]
    desc = None
    if isinstance(info, dict):
        desc = (
            info.get("jobDescription")
            or info.get("externalJobDescription")
            or info.get("description")
            or None
        )
    if isinstance(info, dict):
        posted_raw = (
            info.get("postedOn")
            or info.get("postedDate")
            or info.get("startDate")
        )
    else:
        posted_raw = None
    return {
        "posted": _parse_posted(posted_raw) or _posted_from_json(data) or None,
        "posted_at": posted_raw,
        "posted_raw": posted_raw,
        "location": loc or None,
        "location_raw": raw_locations[0] if raw_locations else None,
        "locations_raw": raw_locations,
        "description": desc,
        "description_raw": desc,
        "raw_data": {
            key: info.get(key)
            for key in (
                "timeType", "remoteType", "jobFamilyGroup",
                "additionalLocations", "country",
            )
            if isinstance(info, dict) and info.get(key)
        } or None,
    }

def fetch_workday(scrape, seen, company):
    """
    Workday JSON API — much faster and more reliable than Playwright for Workday sites.
    Parses tenant/board from list_url: https://{tenant}.wd{n}.myworkdayjobs.com[/en-US]/{board}
    """
    list_url = scrape.get("list_url","")
    if not list_url:
        return [], "no list_url"

    m = re.match(
        r'https://([^.]+)\.(wd\d+\.myworkdayjobs\.com)(?:/[a-z]{2}-[A-Z]{2})?/?([^/?#]*)',
        list_url,
    )
    public_base = None
    if not m:
        mysite = re.match(
            r'https://(wd\d+\.myworkdaysite\.com)/recruiting/([^/]+)/([^/?#]+)',
            list_url,
        )
        if mysite:
            wd_domain = mysite.group(1)
            tenant = mysite.group(2)
            board = mysite.group(3)
            base_url = f"https://{wd_domain}"
            api_url = f"{base_url}/wday/cxs/{tenant}/{board}/jobs"
            public_base = f"{base_url}/recruiting/{tenant}/{board}"
            m = True
    if not m:
        # Some companies use Workday for apply but expose public listings on a
        # custom careers domain. Keep the source as workday while scraping the
        # public listing page.
        if scrape.get("fallback") == "bankofamerica":
            from .bankofamerica import fetch_bankofamerica
            return fetch_bankofamerica(scrape, seen, company)
        if scrape.get("fallback") == "html":
            from .html import fetch_html
            jobs, err = fetch_html({**scrape, "mode": "html", "include": scrape.get("include") or scrape.get("prefix") or list_url}, seen, company)
            for j in jobs or []:
                j["source"] = "workday"
            return jobs, err
        if scrape.get("fallback") == "playwright" and scrape.get("allow_browser_fallback") is not False:
            from .playwright import _fetch_playwright_with_source
            return _fetch_playwright_with_source(scrape, seen, company, "workday")
        return [], f"cannot parse Workday URL: {list_url}"

    if public_base is None:
        tenant    = m.group(1)
        wd_domain = m.group(2)
        board     = m.group(3) or tenant

        api_url  = f"https://{tenant}.{wd_domain}/wday/cxs/{tenant}/{board}/jobs"
        base_url = f"https://{tenant}.{wd_domain}"
        public_base = f"{base_url}/{board}"

    # Workday's public API paginates with a hard 20-row page size. Older
    # configs often set max_pages=30 as a safety cap, which silently limited
    # large companies. By default, scan until Workday's total says we are done.
    max_pages       = int(scrape.get("hard_max_pages") or 10000)
    limit           = 20
    jobs            = []
    total           = None
    consec_all_seen = 0
    fetch_details   = bool(scrape.get("fetch_posted_details", False))
    fetch_exp_detail = bool(scrape.get("fetch_experience_details", True))
    fetch_new_exp_only = bool(scrape.get("fetch_new_experience_only", False))
    refresh_experience = bool(scrape.get("refresh_experience", False))
    refresh_location = bool(scrape.get("refresh_location", False))
    fetch_location_details = bool(scrape.get("fetch_location_details", refresh_location))
    detail_limit    = int(
        scrape.get("max_experience_details")
        or scrape.get("max_posted_details")
        or 1000000
    )
    detail_delay    = float(scrape.get("posted_detail_delay", 0.05))
    detail_workers  = int(scrape.get("detail_workers", 3))
    detail_count    = 0
    stop_early      = bool(scrape.get("stop_on_seen", False) or scrape.get("sort", False))
    page_workers    = max(1, int(scrape.get("page_workers", 4)))

    def _fetch_page(offset, applied_facets=None):
        if scan_deadline_exceeded(scrape):
            raise TimeoutError("company scan deadline reached")
        last_error = None
        for attempt in range(3):
            try:
                r = requests.post(
                    api_url,
                    json={
                        "appliedFacets": applied_facets or {},
                        "limit": limit,
                        "offset": offset,
                        "searchText": "",
                    },
                    headers={**HEADERS, "Content-Type":"application/json", "Accept":"application/json"},
                    timeout=25,
                )
                if r.status_code in (403, 429):
                    last_error = RuntimeError(f"HTTP {r.status_code}")
                else:
                    r.raise_for_status()
                    data = r.json()
                    if not isinstance(data, dict):
                        raise ValueError("Workday returned a non-object JSON response")
                    return data
            except Exception as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(2.0 * (attempt + 1))
        raise last_error or RuntimeError("Workday request failed")

    # Full scans read every page anyway, so fetch them concurrently. The
    # stop-on-seen path stays sequential — early stop needs page order.
    pages = None
    page_errors = {}
    subdivision_error = None
    if not stop_early and max_pages > 1:
        try:
            first = _fetch_page(0)
        except Exception as e:
            return [], str(e)
        total_hint = int(first.get("total") or 0)
        if total_hint > 0:
            if total_hint == 2000 and first.get("facets"):
                deduped = {}
                exhaustion_errors = []
                facet_priority = (
                    "jobFamilyGroup", "workerSubType", "timeType",
                    "locations", "locationMainGroup",
                )
                facet_max_depth = max(
                    1,
                    int(scrape.get("facet_max_depth") or 6),
                )

                def _posting_key(posting):
                    bullets = posting.get("bulletFields") or []
                    return (
                        posting.get("externalPath")
                        or (str(bullets[0]) if bullets else "")
                        or "|".join(
                            str(posting.get(key) or "")
                            for key in ("title", "locationsText")
                        )
                    )

                def _absorb(postings):
                    for posting in postings or []:
                        deduped[_posting_key(posting)] = posting

                def _pick_facet(data, already_applied):
                    by_parameter = {}
                    for facet in data.get("facets") or []:
                        if not isinstance(facet, dict):
                            continue
                        parameter = facet.get("facetParameter")
                        values = facet.get("values") or []
                        if (
                            not parameter
                            # Distance is meaningful only around a supplied
                            # search location. Workday still advertises it on
                            # unscoped searches, where selecting it leaves the
                            # same capped result and creates fake partitions.
                            or parameter == "distance"
                            or parameter in already_applied
                            or len(values) < 2
                        ):
                            continue
                        usable = [
                            (value.get("id"), int(value.get("count") or 0))
                            for value in values
                            if isinstance(value, dict)
                            and value.get("id")
                            and int(value.get("count") or 0) > 0
                        ]
                        if usable:
                            by_parameter[parameter] = usable
                    for parameter in facet_priority:
                        if parameter in by_parameter:
                            return parameter, by_parameter[parameter]
                    if by_parameter:
                        return max(
                            by_parameter.items(),
                            key=lambda item: len(item[1]),
                        )
                    return None

                def _fetch_query_pages(filters, first_page):
                    if scan_deadline_exceeded(scrape):
                        raise TimeoutError("company scan deadline reached")
                    query_total = int(first_page.get("total") or 0)
                    _absorb(first_page.get("jobPostings") or [])
                    page_count = min(
                        max_pages,
                        max(1, (query_total + limit - 1) // limit),
                    )
                    offsets = [index * limit for index in range(1, page_count)]
                    if not offsets:
                        return

                    def _fetch_filtered(offset):
                        return _fetch_page(offset, filters)

                    with concurrent.futures.ThreadPoolExecutor(
                        max_workers=page_workers
                    ) as executor:
                        for offset, result in zip(
                            offsets,
                            executor.map(_fetch_filtered, offsets),
                        ):
                            _absorb(result.get("jobPostings") or [])

                def _exhaust(filters, first_page=None, depth=0):
                    if scan_deadline_exceeded(scrape):
                        exhaustion_errors.append("company scan deadline reached")
                        return
                    try:
                        data = first_page or _fetch_page(0, filters)
                    except Exception as exc:
                        exhaustion_errors.append(
                            f"facet query {filters or '{}'} failed: {exc}"
                        )
                        return
                    query_total = int(data.get("total") or 0)
                    if query_total != 2000:
                        try:
                            _fetch_query_pages(filters, data)
                        except Exception as exc:
                            exhaustion_errors.append(
                                f"facet pagination {filters or '{}'} failed: {exc}"
                            )
                        return
                    # Preserve postings that have no value for the next facet;
                    # they otherwise disappear when only child partitions are
                    # absorbed.
                    _absorb(data.get("jobPostings") or [])
                    if depth >= facet_max_depth:
                        _absorb(data.get("jobPostings") or [])
                        exhaustion_errors.append(
                            f"Workday query remained capped at 2000 for {filters}"
                        )
                        return
                    facet = _pick_facet(data, set(filters))
                    if not facet:
                        try:
                            _fetch_query_pages(filters, data)
                        except Exception as exc:
                            exhaustion_errors.append(str(exc))
                        exhaustion_errors.append(
                            f"Workday query could not subdivide capped result for {filters}"
                        )
                        return
                    parameter, values = facet
                    for value_id, _count in values:
                        if scan_deadline_exceeded(scrape):
                            exhaustion_errors.append("company scan deadline reached")
                            return
                        _exhaust(
                            {**filters, parameter: [value_id]},
                            depth=depth + 1,
                        )

                _exhaust({}, first_page=first)
                all_postings = list(deduped.values())
                pages = {}
                for index in range(0, len(all_postings), limit):
                    pages[index] = {
                        "jobPostings": all_postings[index:index + limit],
                        "total": len(all_postings),
                    }
                if exhaustion_errors:
                    subdivision_error = "; ".join(exhaustion_errors[:5])
            else:
                pages = {0: first}
                page_count = min(max_pages, max(1, (total_hint + limit - 1) // limit))
                offsets = [i * limit for i in range(1, page_count)]
                if offsets:
                    def _try_fetch_page(offset):
                        try:
                            return offset, _fetch_page(offset), None
                        except Exception as e:
                            return offset, None, str(e)
                    with concurrent.futures.ThreadPoolExecutor(max_workers=page_workers) as ex:
                        for offset, data, page_err in ex.map(_try_fetch_page, offsets):
                            pages[offset] = data
                            if page_err:
                                page_errors[offset] = page_err
        # total missing/0: fall back to the sequential loop, which paginates
        # until an empty page instead of trusting the reported total.

    # When pagination dies partway, return the partial jobs WITH an error so
    # the scanner records the fetch as incomplete — a full scan must never run
    # job expiry against a partial list (it would close every job on the pages
    # that were never reached).
    fetch_err = subdivision_error

    for idx in range(max_pages):
        if scan_deadline_exceeded(scrape):
            fetch_err = "company scan deadline reached (partial results)"
            break
        offset = idx * limit
        if pages is not None:
            if offset not in pages:
                break
            data = pages[offset]
            if data is None:
                fetch_err = (
                    f"page fetch failed at offset {offset}: "
                    f"{page_errors.get(offset, 'unknown error')} (partial results)"
                )
                if jobs: break
                return [], fetch_err
        else:
            try:
                data = _fetch_page(offset)
            except Exception as e:
                if jobs:
                    fetch_err = f"page fetch failed at offset {offset}: {e} (partial results)"
                    break
                return [], str(e)

        postings = data.get("jobPostings",[])
        if not postings:
            break
        if total is None:
            total = data.get("total", 0)

        page_jobs = []
        page_had_missing_posted = False
        for j in postings:
            title  = norm(j.get("title") or "")
            path   = j.get("externalPath") or ""
            jurl   = f"{public_base}{path}" if path else ""
            raw_locations = _workday_raw_locations(j)
            raw_location = raw_locations[0] if raw_locations else None
            loc    = _workday_location(j)
            posted_raw = j.get("postedOn") or j.get("postedDate")
            posted = _parse_posted(posted_raw)
            bullets = j.get("bulletFields") or []
            requisition_id = _workday_requisition_id(j)
            ats_id = requisition_id or path.rsplit("/", 1)[-1] or None
            if title:
                jid = make_id(company, jurl, title, "workday", ats_id)
                existing = (
                    seen.get(jid)
                    or seen.get(make_id(company, jurl, title))
                    if isinstance(seen, dict) else None
                )
                if not posted and isinstance(existing, dict):
                    posted = _parse_posted(existing.get("posted") or "")
                existing_source = (existing.get("exp_source") or "") if isinstance(existing, dict) else ""
                existing_has_description = (
                    isinstance(existing, dict)
                    and bool(existing.get("has_description"))
                )
                existing_location = (
                    normalize_location(existing.get("location") or "")
                    if isinstance(existing, dict)
                    else ""
                )
                if (
                    not refresh_location
                    and
                    (
                        (loc in ("", "Multiple Locations")
                         and existing_location not in ("", "Multiple Locations"))
                        or (not _workday_location_has_country(loc)
                            and _workday_location_has_country(existing_location))
                    )
                ):
                    loc = existing_location
                    raw_locations = [
                        item.get("display") or item.get("raw")
                        for item in existing.get("locations") or []
                        if isinstance(item, dict) and (item.get("raw") or item.get("display"))
                    ] or [existing_location]
                    raw_location = "; ".join(raw_locations)
                existing_exp = (
                    isinstance(existing, dict)
                    and bool(existing.get("exp_label"))
                    and not refresh_experience
                    and existing_source == "text"
                )
                # If we already have the description stored, skip re-fetching.
                # Re-fetching returns the same text and causes hours-long hangs for large companies.
                desc_already_fetched = bool(existing_has_description) and not refresh_experience
                exp_detail_needed = (
                    fetch_exp_detail
                    and not existing_exp
                    and not desc_already_fetched
                    and (
                        refresh_experience
                        or not fetch_new_exp_only
                        or not isinstance(existing, dict)
                    )
                )
                # A list-page placeholder such as "2 Locations" must always
                # be resolved from the detail endpoint. Configured location
                # enrichment additionally repairs incomplete country data.
                location_detail_needed = (
                    loc == "Multiple Locations"
                    or (
                        fetch_location_details
                        and (refresh_location or not _workday_location_has_country(loc))
                    )
                )
                needs_detail = (
                    exp_detail_needed
                    or location_detail_needed
                    or (not posted)
                )
                # Fast scans avoid detail pages unless rich metadata was
                # explicitly requested; those requests dominate Workday runtime.
                fetch_for_this = (
                    fetch_details
                    or location_detail_needed
                    or exp_detail_needed
                )
                if needs_detail and fetch_for_this and detail_count < detail_limit:
                    page_had_missing_posted = True
                elif not posted:
                    page_had_missing_posted = True
                page_jobs.append({
                    "title": title,
                    "url": jurl,
                    "ats_id": ats_id,
                    "requisition_id": requisition_id,
                    "location": loc,
                    "location_raw": raw_location,
                    "locations_raw": raw_locations,
                    "source": "workday",
                    "posted": posted or None,
                    "posted_raw": posted_raw,
                    "posted_at": posted_raw,
                    "employment_type": j.get("timeType"),
                    "employment_type_raw": j.get("timeType"),
                    "workplace_type": j.get("remoteType"),
                    "department": j.get("jobFamilyGroup"),
                    "raw_data": {
                        key: j.get(key)
                        for key in (
                            "bulletFields", "locations", "timeType",
                            "jobFamilyGroup", "remoteType", "externalPath",
                        )
                        if j.get(key)
                    } or None,
                    "description": None,
                    "_detail_path": path if needs_detail and fetch_for_this else "",
                    "_needs_location_detail": location_detail_needed,
                })

        detail_targets = []
        remaining_details = max(0, detail_limit - detail_count)
        for pos, job in enumerate(page_jobs):
            if not job.get("_detail_path") or scan_deadline_exceeded(scrape):
                continue
            if job.get("_needs_location_detail"):
                detail_targets.append((pos, job))
            elif remaining_details:
                detail_targets.append((pos, job))
                remaining_details -= 1
        if detail_targets:
            def _fetch_target(item):
                pos, job = item
                detail = _fetch_workday_detail(base_url, tenant, board, job["_detail_path"])
                if not detail.get("location"):
                    previous = (
                        seen.get(make_id(company, job["url"], job["title"], "workday", job.get("ats_id")))
                        or seen.get(make_id(company, job["url"], job["title"]))
                    ) if isinstance(seen, dict) else None
                    if isinstance(previous, dict) and previous.get("location") not in (None, "", "Multiple Locations"):
                        cached = [
                            value.get("display") or value.get("raw")
                            for value in previous.get("locations") or []
                            if isinstance(value, dict) and (value.get("display") or value.get("raw"))
                        ] or [previous["location"]]
                        detail.update(location="; ".join(cached), location_raw="; ".join(cached), locations_raw=cached)
                return pos, detail

            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, detail_workers)) as ex:
                futures = [ex.submit(_fetch_target, item) for item in detail_targets]
                for fut in concurrent.futures.as_completed(futures):
                    pos, detail = fut.result()
                    job = page_jobs[pos]
                    job["posted"] = job.get("posted") or detail.get("posted", "")
                    if detail.get("location"):
                        job["location"] = detail["location"]
                    if detail.get("location_raw"):
                        job["location_raw"] = detail["location_raw"]
                    if detail.get("locations_raw"):
                        job["locations_raw"] = detail["locations_raw"]
                    if detail.get("posted_at"):
                        job["posted_at"] = detail["posted_at"]
                    if detail.get("posted_raw"):
                        job["posted_raw"] = detail["posted_raw"]
                    job["description"] = detail.get("description", "")
                    job["description_raw"] = detail.get("description_raw")
                    if detail.get("raw_data"):
                        if not isinstance(job.get("raw_data"), dict):
                            job["raw_data"] = {}
                        job["raw_data"].update(detail["raw_data"])
            detail_count += len(detail_targets)
            if detail_delay:
                time.sleep(detail_delay)
        for job in page_jobs:
            job.pop("_detail_path", None)
            job.pop("_needs_location_detail", None)
        jobs.extend(page_jobs)

        if stop_early and seen and page_jobs:
            all_seen = all(
                (
                    make_id(
                        company,
                        j["url"],
                        j["title"],
                        j.get("source", ""),
                        j.get("ats_id", ""),
                    ) in seen
                    or make_id(company, j["url"], j["title"]) in seen
                )
                for j in page_jobs
            )
            consec_all_seen = consec_all_seen + 1 if all_seen else 0
            backfilling_dates = fetch_details and page_had_missing_posted and detail_count < detail_limit
            if consec_all_seen >= 2 and not backfilling_dates:
                break

        if total and offset + limit >= total:
            break
        if pages is None:
            time.sleep(0.2)

    return jobs, fetch_err
