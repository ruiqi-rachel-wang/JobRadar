from .base import *

# ── Bank of America careers servlet ───────────────────────────────────────────

def _boa_locations(posting):
    """Read the primary address and every additional office from the servlet.

    The search result's city/state can refer to an additional office. The
    coded locationString identifies the actual primary office, while the two
    additional-location fields preserve the other eligible locations.
    """
    locations = []
    keys = set()

    def add(value):
        value = norm(value)
        key = normalize_location(value).casefold() if value else ""
        if key and key not in keys:
            keys.add(key)
            locations.append(value)

    def coded(value):
        found = []
        for match in re.finditer(
            r"(?:^|[,;])\s*([A-Z]{2})\s+-\s+(.+?)\s+-\s+(.+?)\s+-\s+",
            value or "",
        ):
            country, region, city = (norm(part) for part in match.groups())
            found.append(", ".join(part for part in (city, region, country) if part))
        return found

    primary = coded(posting.get("locationString"))
    if primary:
        for value in primary:
            add(value)
    else:
        add(", ".join(posting.get(key) or "" for key in ("city", "state", "country")).strip(", "))
    for value in coded(posting.get("additionalLocations")):
        add(value)
    # Human-readable entries are separated by semicolons. Split from the
    # right so commas inside a street address cannot become a city.
    for entry in (posting.get("additionalLocationsList") or "").split(";"):
        parts = [norm(part) for part in entry.rsplit(",", 3)]
        if len(parts) >= 3:
            add(", ".join(parts[-3:]))
    return locations


def _set_boa_locations(job, locations):
    job["locations_raw"] = locations
    job["location_raw"] = "; ".join(locations) or None
    job["location"] = "; ".join(normalize_location(value) for value in locations)


def fetch_bankofamerica(scrape, seen, company):
    list_url = scrape.get("list_url") or "https://careers.bankofamerica.com/en-us/job-search/united-states"
    endpoint = scrape.get("api_url") or "https://careers.bankofamerica.com/services/jobssearchservlet"
    rows = max(1, int(scrape.get("rows", 100)))
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    search = scrape.get("search", "jobsByCountry")
    country = scrape.get("country", "United States")
    jobs = {}
    page_signatures = set()
    total = None
    received = 0
    invalid = 0

    for page in range(max_pages):
        if scan_deadline_exceeded(scrape):
            return list(jobs.values()), "company scan deadline reached (partial results)"
        start = page * rows
        # BOA's servlet uses rows as an ending pointer, not a page size.
        params = {"start": start, "rows": start + rows, "search": search}
        if search == "jobsByCountry":
            params["country"] = country
        elif search == "jobsByLocation":
            params["searchstring"] = country
        try:
            r = requests.get(
                endpoint, params=params,
                headers={**HEADERS, "Accept": "application/json", "Referer": list_url},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, dict) or not isinstance(data.get("jobsList"), list) or "totalMatches" not in data:
                raise ValueError("invalid Bank of America listing response")
            postings = data["jobsList"]
            if total is None:
                total = int(data["totalMatches"])
        except Exception as e:
            return list(jobs.values()), str(e)

        if not postings:
            break
        signature = tuple(tuple(j.get(key) for key in (
            "jcrURL", "locationString", "additionalLocations", "additionalLocationsList",
        )) for j in postings)
        if signature in page_signatures:
            return list(jobs.values()), "repeated Bank of America listing page (partial results)"
        page_signatures.add(signature)
        received += len(postings)

        for posting in postings:
            title = norm(posting.get("postingTitle") or "")
            path = posting.get("jcrURL") or ""
            jurl = urljoin("https://careers.bankofamerica.com", path)
            parsed = urlparse(jurl)
            if (not title or parsed.hostname != "careers.bankofamerica.com"
                    or not re.match(r"/en-us/job-detail/\d+/", parsed.path)):
                invalid += 1
                continue
            locations = _boa_locations(posting)
            if jurl in jobs:
                combined = jobs[jurl]["locations_raw"] + locations
                _set_boa_locations(jobs[jurl], _boa_locations({"additionalLocationsList": ";".join(combined)}))
                continue
            requisition_id = str(posting.get("jobRequisitionId") or "").strip() or None
            job = {
                "title": title, "url": jurl, "source": "workday",
                "ats_id": requisition_id, "requisition_id": requisition_id,
                "posted": _parse_posted(posting.get("postedDate") or posting.get("indexedDate") or ""),
                "raw_data": {key: posting.get(key) for key in (
                    "city", "state", "country", "locationString",
                    "additionalLocations", "additionalLocationsList",
                )},
            }
            _set_boa_locations(job, locations)
            jobs[jurl] = job

        if total is not None and start + rows >= total:
            break
        if len(postings) < rows:
            break
        time.sleep(0.2)

    errors = []
    if total is not None and received < total:
        errors.append(f"partial Bank of America listing: fetched {received} of {total} source records")
    if invalid:
        errors.append(f"invalid Bank of America posting records: {invalid}")
    return list(jobs.values()), "; ".join(errors) or None
