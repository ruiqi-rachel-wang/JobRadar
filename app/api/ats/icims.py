from .base import *

# ── iCIMS ─────────────────────────────────────────────────────────────────────

_JOB_URL_RE = re.compile(r'/jobs/\d+/[^/?#]+/job')

def _is_icims_job_url(href):
    return bool(_JOB_URL_RE.search(href))

def _clean_icims_job_url(href, base):
    """Return canonical job URL without in_iframe/tracking params."""
    url = urljoin(base, href)
    # Strip ?in_iframe=1 and similar params — job URL is clean without them
    url = re.sub(r'[?&]in_iframe=[^&]*', '', url).rstrip('?&')
    return url

def _icims_title_from_url(job_url):
    path = urlparse(job_url).path
    m = _JOB_URL_RE.search(path)
    if not m:
        return ""
    slug = path[m.start():].split('/')[3]  # 0='' 1='jobs' 2=id 3=slug 4='job'
    return norm(unquote(slug).replace("-", " ")).title()

def _parse_icims_location(raw):
    """
    Parse iCIMS compound location strings: 'US-MA-Boston', 'US-NY-New York'.
    Field: job.joblocation — composed of addresscountry + addressstate + addresscity.
    Split on '-' with max 2 splits so city names like 'San-Antonio' stay intact.
    """
    _PLACEHOLDER = {"UNAVAILABLE", "N/A", "NA", "NONE", "NULL", ""}
    raw = (raw or "").strip()
    if not raw:
        return ""
    # Handle dash-separated format: "US-MA-Boston" or "GB-UNAVAILABLE-London"
    parts = raw.split('-', 2)
    if len(parts) == 3:
        country, state, city = parts
        country = _COUNTRY_CODES.get(country.upper(), country)
        city = city.replace('-', ' ')
        state = "" if state.upper() in _PLACEHOLDER else state
        loc = ", ".join(x.strip() for x in [city, state, country] if x.strip())
        return normalize_location(norm(loc))
    if len(parts) == 2:
        country, rest = parts
        country = _COUNTRY_CODES.get(country.upper(), country)
        return normalize_location(norm(f"{rest.replace('-', ' ').strip()}, {country.strip()}"))
    # Handle comma-separated format: "London, UNAVAILABLE, GB"
    if ',' in raw:
        comma_parts = [p.strip() for p in raw.split(',')]
        if len(comma_parts) >= 3:
            comma_parts[-1] = _COUNTRY_CODES.get(comma_parts[-1].upper(), comma_parts[-1])
        comma_parts = [p for p in comma_parts if p.upper() not in _PLACEHOLDER]
        return normalize_location(norm(", ".join(comma_parts)))
    return normalize_location(norm(raw))

_ICIMS_DESC_SELECTORS = re.compile(
    r'iCIMS_JobContent|job[_-]?content|job[_-]?description|job[_-]?detail|description[_-]?content',
    re.I,
)

def _icims_location_from_detail(job_url, timeout=12):
    """
    Fetch an iCIMS job detail page and extract location, posted date, and description.
    Location: <dt>Location</dt><dd>US-MA-Boston</dd>  (job.joblocation compound field)
    Falls back to JSON-LD jobLocation if the dt/dd structure isn't present.
    Returns (loc, posted, description, raw metadata).
    """
    try:
        fetch_url = job_url + ('&in_iframe=1' if '?' in job_url else '?in_iframe=1')
        r = requests.get(fetch_url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return "", "", "", {}
        soup = BeautifulSoup(r.text, "lxml")

        loc = ""
        posted = ""
        desc = ""
        loc_raw = ""
        posted_raw = ""

        locations = []
        # Primary: <dt>Location</dt><dd>US-MA-Boston</dd>
        # iCIMS HTML can have badly-nested dt/dd elements, so take only the
        # direct text nodes of the <dd> (recursive=False) to avoid spill-over.
        for dt in soup.find_all('dt'):
            label = dt.get_text(strip=True).lower()
            if label in ('location', 'locations', 'job location', 'job locations'):
                dd = dt.find_next_sibling('dd')
                if dd:
                    raw = ' '.join(dd.find_all(string=True, recursive=False)).strip()
                    for part in re.split(r'\s*[|;]\s*|\s+(?=[A-Z]{2}-(?:[A-Z]{2,3}|UNAVAILABLE)-)', raw):
                        candidate = _parse_icims_location(part)
                        if candidate and candidate not in locations:
                            locations.append(candidate)

        # Also grab posted date from detail if available
        for dt in soup.find_all('dt'):
            label = dt.get_text(strip=True).lower()
            if label in ('posted', 'date posted', 'post date'):
                dd = dt.find_next_sibling('dd')
                if dd:
                    raw = ' '.join(dd.find_all(string=True, recursive=False)).strip()
                    posted_raw = raw
                    posted = _parse_posted(raw)

        # Structured data may list additional places even when the visible
        # Location field contains only the primary office.
        for sc in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(sc.string or sc.get_text() or '')
            except (ValueError, TypeError):
                continue
            for posting in _jobposting_objects(data):
                for raw in _jobposting_locations(posting):
                    candidate = normalize_location(raw)
                    if candidate and candidate not in locations:
                        locations.append(candidate)
                posted_raw = posted_raw or posting.get("datePosted") or ""
                posted = posted or _parse_posted(posted_raw)
        loc = "; ".join(locations)
        loc_raw = loc

        # Extract job description text
        desc_el = None
        for el in soup.find_all(True):
            cls = " ".join(el.get("class") or [])
            eid = el.get("id") or ""
            if _ICIMS_DESC_SELECTORS.search(cls) or _ICIMS_DESC_SELECTORS.search(eid):
                desc_el = el
                break
        if desc_el:
            desc = str(desc_el)

        return loc, posted, normalize_description(desc), {
            "location_raw": loc_raw or None,
            "locations_raw": locations,
            "posted_raw": posted_raw or None,
            "posted_at": posted_raw or None,
            "description_raw": desc or None,
        }
    except Exception:
        return "", "", "", {}

def _icims_jobs_from_html(html, page_url):
    """
    Parse iCIMS search results HTML.
    Handles both clean /job URLs and /job?in_iframe=1 variants.
    Location is rarely in the search page — callers enrich via _icims_location_from_detail.
    """
    soup = BeautifulSoup(html, "lxml")
    jobs = {}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not _is_icims_job_url(href):
            continue
        jurl = _clean_icims_job_url(href, page_url)
        raw_text = norm(a.get_text(" ", strip=True))
        # iCIMS puts "Title" prefix in the link text
        title = re.sub(r'^Title\s+', '', raw_text, flags=re.I).strip()
        title = title or _icims_title_from_url(jurl)
        # Try to get location from the surrounding element text
        parent = a
        parent_text = ""
        for _ in range(5):
            parent = parent.parent if parent else None
            if not parent:
                break
            t = norm(parent.get_text(" ", strip=True))
            if len(t) > len(raw_text) + 5:
                parent_text = t
                break
        # Location is reliably fetched from the <dt>Location</dt> element on the
        # detail page. Leave it empty here; fetch_icims enriches all jobs.
        loc = ""
        posted = extract_posted_from_text(parent_text)
        if title and len(title) >= 3:
            id_match = re.search(r"/jobs/(\d+)/", jurl)
            jobs[jurl] = {
                "title": title,
                "url": jurl,
                "ats_id": id_match.group(1) if id_match else None,
                "location": loc,
                "source": "icims",
                "posted": posted or None,
                "posted_raw": posted or None,
            }
    return jobs

def fetch_icims(scrape, seen, company):
    """
    iCIMS boards.

    Strategy:
      1. sitemap.xml — fastest; gives URLs + lastmod date but no location
      2. /jobs/search?ss=1&in_iframe=1 — search page inside the iframe; has job links
      3. Playwright fallback

    After collecting jobs via sitemap, enrich missing locations by fetching each
    job's detail page and reading the <dt>Location</dt> field (job.joblocation:
    compound of addresscountry + addressstate + addresscity).
    """
    slug = scrape.get("icims_slug", "")
    if not slug:
        return [], "no icims_slug"
    # Listing-only audits and fast source checks should not turn one board
    # request into hundreds of detail-page requests. Existing production
    # behavior remains the default when this option is omitted.
    if "fetch_details" in scrape:
        fetch_details = bool(scrape.get("fetch_details"))
    elif "fetch_experience_details" in scrape:
        # Scanner defaults inject this flag for fast/full modes. Respect it so
        # a normal listing scan does not fan out into one request per posting.
        fetch_details = bool(scrape.get("fetch_experience_details"))
    else:
        fetch_details = True

    starts = []
    if slug.startswith("http"):
        starts.append(slug.rstrip("/"))
    else:
        starts.append(f"https://careers-{slug}.icims.com")
        starts.append(f"https://{slug}.icims.com")

    first_err = None
    for base in starts:
        base = base.rstrip("/")

        # Jibe career sites expose their underlying iCIMS postings through a
        # public JSON endpoint. This replaces the legacy iCIMS iframe on sites
        # such as Armor Health and avoids one detail request per posting.
        if "icims.com" not in urlparse(base).netloc.lower():
            try:
                limit = max(1, int(scrape.get("limit") or 100))
                response = requests.get(
                    base + f"/api/jobs?limit={limit}",
                    headers={**HEADERS, "Accept": "application/json"},
                    timeout=25,
                )
                response.raise_for_status()
                payload = response.json()
                total = int(payload.get("totalCount") or 0)
                rows = payload.get("jobs") or []
                if total and len(rows) < total:
                    return [], f"partial Jibe listing: fetched {len(rows)} of {total} jobs"
                jobs = []
                for row in rows:
                    data = row.get("data") if isinstance(row, dict) else None
                    if not isinstance(data, dict) or not data.get("searchable", True):
                        continue
                    title = norm(data.get("title") or "")
                    job_id = str(data.get("req_id") or data.get("slug") or "").strip()
                    metadata = data.get("meta_data") or {}
                    canonical = metadata.get("canonical_url") if isinstance(metadata, dict) else ""
                    job_url = canonical or (base + f"/jobs/{job_id}?lang=en-us" if job_id else "")
                    raw_location = norm(data.get("full_location") or data.get("short_location") or "")
                    if title and job_url:
                        description = data.get("description") or ""
                        jobs.append({
                            "title": title,
                            "url": job_url,
                            "apply_url": data.get("apply_url"),
                            "ats_id": job_id or None,
                            "requisition_id": job_id or None,
                            "location": normalize_location(raw_location),
                            "location_raw": raw_location or None,
                            "locations_raw": [raw_location] if raw_location else [],
                            "source": "icims",
                            "posted_at": data.get("posted_date"),
                            "posted_raw": data.get("posted_date"),
                            "employment_type": data.get("employment_type"),
                            "employment_type_raw": data.get("employment_type"),
                            "department": data.get("department") or None,
                            "description": normalize_description(description),
                            "description_raw": description or None,
                        })
                if jobs or total == 0:
                    return jobs, None
            except Exception as exc:
                first_err = str(exc)

        # ── 1. Sitemap ──────────────────────────────────────────────────────
        sitemap_url = base + "/sitemap.xml"
        try:
            r = requests.get(
                sitemap_url,
                headers={**HEADERS, "Accept": "application/xml,text/xml,*/*"},
                timeout=18,
            )
            if r.status_code == 200 and "xml" in r.headers.get("Content-Type", "").lower():
                root = ET.fromstring(r.content)
                jobs = []
                for url_node in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}url"):
                    loc_node    = url_node.find("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
                    lastmod_node= url_node.find("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod")
                    jurl = (loc_node.text or "").strip() if loc_node is not None else ""
                    if not _is_icims_job_url(jurl):
                        continue
                    jurl   = _clean_icims_job_url(jurl, jurl)
                    title  = _icims_title_from_url(jurl)
                    posted = _parse_posted((lastmod_node.text or "") if lastmod_node is not None else "")
                    if title:
                        id_match = re.search(r"/jobs/(\d+)/", jurl)
                        lastmod_raw = (
                            (lastmod_node.text or "").strip()
                            if lastmod_node is not None else None
                        )
                        jobs.append({
                            "title": title,
                            "url": jurl,
                            "ats_id": id_match.group(1) if id_match else None,
                            "location": "",
                            "source": "icims",
                            "posted": posted or None,
                            "posted_at": lastmod_raw,
                            "posted_raw": lastmod_raw,
                        })

                if jobs:
                    if not fetch_details:
                        return jobs, None
                    # Enrich missing locations/dates/descriptions from detail pages.
                    # Reuse saved data so repeat scans skip jobs that are already complete.
                    targets = []
                    for j in jobs:
                        existing = (
                            seen.get(make_id(company, j["url"], j["title"], "icims", j.get("ats_id", "")))
                            or seen.get(make_id(company, j["url"], j["title"]))
                            if isinstance(seen, dict) else None
                        )
                        if isinstance(existing, dict):
                            j["location"] = j.get("location") or existing.get("location", "")
                            j["posted"] = j.get("posted") or _parse_posted(existing.get("posted") or "")
                        existing_has_desc = isinstance(existing, dict) and existing.get("has_description")
                        if not j.get("location") or not j.get("posted") or not existing_has_desc:
                            targets.append(j)

                    max_workers = min(6, len(targets))
                    timeout_per = int(scrape.get("detail_timeout", 12))

                    def _enrich(j):
                        loc, posted_detail, desc, raw = _icims_location_from_detail(
                            j["url"], timeout=timeout_per
                        )
                        return j["url"], loc, posted_detail, desc, raw

                    if targets:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                            by_url = {j["url"]: j for j in jobs}
                            for jurl, loc, posted_detail, desc, raw in ex.map(_enrich, targets):
                                j = by_url.get(jurl)
                                if j:
                                    if loc:
                                        j["location"] = loc
                                    if posted_detail and not j["posted"]:
                                        j["posted"] = posted_detail
                                    if desc:
                                        j["description"] = desc
                                    j.update({key: value for key, value in raw.items() if value})
                    return jobs, None

            elif r.status_code not in (403, 404):
                first_err = f"{sitemap_url}: HTTP {r.status_code}"
        except Exception as e:
            first_err = str(e)

        # ── 2. Search page (in_iframe) ──────────────────────────────────────
        # iCIMS renders job listings inside an iframe; the real content is at
        # ?ss=1&in_iframe=1. Links inside end with /job?in_iframe=1.
        for search_url in [
            base + "/jobs/search?ss=1&hl=en&in_iframe=1",
            base + "/jobs/search?ss=1&in_iframe=1",
        ]:
            try:
                # iCIMS uses zero-indexed `pr` pages (normally 25 jobs each).
                # Stop only when a page contributes no new job IDs; fetching a
                # single page silently truncated large boards.
                jobs = {}
                max_pages = max(1, int(scrape.get("hard_max_pages") or 200))
                for page_num in range(max_pages):
                    if scan_deadline_exceeded(scrape):
                        raise TimeoutError("scan deadline exceeded")
                    page_url = make_paged_url(
                        search_url,
                        {"param": "pr", "start": 0, "step": 1},
                        page_num,
                    )
                    r = requests.get(
                        page_url,
                        headers={**HEADERS, "Accept": "text/html,*/*"},
                        timeout=20,
                    )
                    r.raise_for_status()
                    page_jobs = _icims_jobs_from_html(r.text, r.url)
                    new_jobs = {
                        url: job for url, job in page_jobs.items()
                        if url not in jobs
                    }
                    if not new_jobs:
                        break
                    jobs.update(new_jobs)
                if jobs:
                    if not fetch_details:
                        return list(jobs.values()), None
                    # Enrich missing detail fields only.
                    all_jobs = list(jobs.values())
                    targets = []
                    for j in all_jobs:
                        existing = (
                            seen.get(make_id(company, j["url"], j["title"], "icims", j.get("ats_id", "")))
                            or seen.get(make_id(company, j["url"], j["title"]))
                            if isinstance(seen, dict) else None
                        )
                        if isinstance(existing, dict):
                            j["location"] = j.get("location") or existing.get("location", "")
                            j["posted"] = j.get("posted") or _parse_posted(existing.get("posted") or "")
                        existing_has_desc = isinstance(existing, dict) and existing.get("has_description")
                        if not j.get("location") or not j.get("posted") or not existing_has_desc:
                            targets.append(j)
                    if targets:
                        with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(targets))) as ex:
                            for jurl, loc, posted_detail, desc, raw in ex.map(
                                lambda j: (j["url"], *_icims_location_from_detail(j["url"])),
                                targets,
                            ):
                                j = jobs.get(jurl)
                                if j:
                                    if loc:
                                        j["location"] = loc
                                    if posted_detail and not j.get("posted"):
                                        j["posted"] = posted_detail
                                    if desc:
                                        j["description"] = desc
                                    j.update({key: value for key, value in raw.items() if value})
                    return list(jobs.values()), None
            except Exception as e:
                if not first_err:
                    first_err = str(e)

    if scrape.get("allow_browser_fallback") is not False and scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        from .playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "icims")
    return [], first_err or "no jobs found"
