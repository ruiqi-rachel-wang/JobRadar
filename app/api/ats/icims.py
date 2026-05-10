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
        city = city.replace('-', ' ')
        state = "" if state.upper() in _PLACEHOLDER else state
        loc = ", ".join(x.strip() for x in [city, state, country] if x.strip())
        return normalize_location(norm(loc))
    if len(parts) == 2:
        country, rest = parts
        return normalize_location(norm(f"{rest.replace('-', ' ').strip()}, {country.strip()}"))
    # Handle comma-separated format: "London, UNAVAILABLE, GB"
    if ',' in raw:
        comma_parts = [p.strip() for p in raw.split(',')]
        comma_parts = [p for p in comma_parts if p.upper() not in _PLACEHOLDER]
        return normalize_location(norm(", ".join(comma_parts)))
    return normalize_location(norm(raw))

def _icims_location_from_detail(job_url, timeout=12):
    """
    Fetch an iCIMS job detail page and extract location using:
      <dt>Location</dt><dd>US-MA-Boston</dd>  (job.joblocation compound field)
    Falls back to JSON-LD jobLocation if the dt/dd structure isn't present.
    """
    try:
        fetch_url = job_url + ('&in_iframe=1' if '?' in job_url else '?in_iframe=1')
        r = requests.get(fetch_url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return "", ""
        soup = BeautifulSoup(r.text, "lxml")

        loc = ""
        posted = ""

        # Primary: <dt>Location</dt><dd>US-MA-Boston</dd>
        # iCIMS HTML can have badly-nested dt/dd elements, so take only the
        # direct text nodes of the <dd> (recursive=False) to avoid spill-over.
        for dt in soup.find_all('dt'):
            label = dt.get_text(strip=True).lower()
            if label == 'location':
                dd = dt.find_next_sibling('dd')
                if dd:
                    raw = ' '.join(dd.find_all(string=True, recursive=False)).strip()
                    loc = _parse_icims_location(raw)

        # Also grab posted date from detail if available
        for dt in soup.find_all('dt'):
            label = dt.get_text(strip=True).lower()
            if label in ('posted', 'date posted', 'post date'):
                dd = dt.find_next_sibling('dd')
                if dd:
                    raw = ' '.join(dd.find_all(string=True, recursive=False)).strip()
                    posted = _parse_posted(raw)

        # Fallback: JSON-LD jobLocation
        if not loc:
            for sc in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(sc.string or '')
                    jl = (data.get('jobLocation') or {})
                    if isinstance(jl, list):
                        jl = jl[0] if jl else {}
                    addr = jl.get('address') or {}
                    city    = addr.get('addressLocality') or ''
                    state   = addr.get('addressRegion') or ''
                    country = addr.get('addressCountry') or ''
                    # Filter placeholder region values iCIMS uses when state is unavailable
                    if state.upper() in ("UNAVAILABLE", "N/A", "NA", "NONE", "NULL"):
                        state = ''
                    candidate = ", ".join(x for x in [city, state, country] if x)
                    if candidate:
                        loc = normalize_location(norm(candidate))
                        break
                except Exception:
                    pass

        return loc, posted
    except Exception:
        return "", ""

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
            jobs[jurl] = {"title": title, "url": jurl, "location": loc,
                          "source": "icims", "posted": posted}
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

    starts = []
    if slug.startswith("http"):
        starts.append(slug.rstrip("/"))
    else:
        starts.append(f"https://careers-{slug}.icims.com")
        starts.append(f"https://{slug}.icims.com")

    first_err = None
    for base in starts:
        base = base.rstrip("/")

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
                        jobs.append({"title": title, "url": jurl, "location": "",
                                     "source": "icims", "posted": posted})

                if jobs:
                    # Enrich locations from detail pages in parallel
                    max_workers = min(6, len(jobs))
                    timeout_per = int(scrape.get("detail_timeout", 12))

                    def _enrich(j):
                        loc, posted_detail = _icims_location_from_detail(j["url"], timeout=timeout_per)
                        return j["url"], loc, posted_detail

                    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                        for jurl, loc, posted_detail in ex.map(_enrich, jobs):
                            for j in jobs:
                                if j["url"] == jurl:
                                    if loc:
                                        j["location"] = loc
                                    if posted_detail and not j["posted"]:
                                        j["posted"] = posted_detail
                                    break
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
                r = requests.get(
                    search_url,
                    headers={**HEADERS, "Accept": "text/html,*/*"},
                    timeout=20,
                )
                r.raise_for_status()
                jobs = _icims_jobs_from_html(r.text, r.url)
                if jobs:
                    # Enrich location for all jobs from their detail pages.
                    all_jobs = list(jobs.values())
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(all_jobs))) as ex:
                        for jurl, loc, posted_detail in ex.map(
                            lambda j: (j["url"], *_icims_location_from_detail(j["url"])),
                            all_jobs,
                        ):
                            j = jobs.get(jurl)
                            if j:
                                if loc:
                                    j["location"] = loc
                                if posted_detail and not j.get("posted"):
                                    j["posted"] = posted_detail
                    return list(jobs.values()), None
            except Exception as e:
                if not first_err:
                    first_err = str(e)

    if scrape.get("list_url") and (scrape.get("prefix") or scrape.get("include")):
        return _fetch_playwright_with_source(scrape, seen, company, "icims")
    return [], first_err or "no jobs found"
