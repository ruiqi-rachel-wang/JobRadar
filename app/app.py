"""
JobRadar v4 — Local Web App
Run:  python app.py
Open: http://localhost:5000

companies.json scrape config:
  mode        — greenhouse | lever | ashby | workday | bamboohr | icims | apple | google | microsoft | pinpoint | html | playwright
  board_token — greenhouse board slug
  lever_slug  — lever company slug
  ashby_slug  — ashby org slug (from jobs.ashbyhq.com/{slug})
  list_url    — URL to start scraping (workday/html/playwright modes)
  include     — substring that MUST appear in job link href (html mode)
  prefix      — URL prefix for job links (playwright mode)
  sort        — true → stop paginating once we hit an already-seen job ID
  pagination  — {type: "query_page"|"next_link"|"none", param, start, step, max_pages}
  max_pages   — cap for api-based scrapers
"""

import json, hashlib, time, threading, re, concurrent.futures, sqlite3
import xml.etree.ElementTree as ET
from datetime  import datetime, timezone
from pathlib   import Path
from urllib.parse import urljoin, urlparse, urlunparse, urlencode, parse_qs, unquote

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / "data"
SEEN_FILE    = DATA_DIR / "seen_jobs.json"
SUMMARY_FILE = DATA_DIR / "scan_summary.json"
DB_FILE      = DATA_DIR / "jobs.db"
DATA_DIR.mkdir(exist_ok=True)

_seen_lock = threading.Lock()

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id         TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                url        TEXT DEFAULT '',
                company    TEXT NOT NULL,
                location   TEXT DEFAULT '',
                source     TEXT DEFAULT '',
                categories TEXT DEFAULT '[]',
                first_seen TEXT NOT NULL,
                posted     TEXT DEFAULT ''
            )
        """)
        # Migrate existing DBs that don't have the posted column
        try:
            conn.execute("ALTER TABLE jobs ADD COLUMN posted TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()

init_db()

def upsert_jobs(job_list):
    if not job_list:
        return
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO jobs "
            "(id, title, url, company, location, source, categories, first_seen, posted) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(j["id"], j["title"], j.get("url",""), j["company"],
              j.get("location",""), j.get("source",""),
              json.dumps(j.get("categories",[])), j["first_seen"],
              j.get("posted",""))
             for j in job_list],
        )
        conn.commit()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/json,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── Companies ─────────────────────────────────────────────────────────────────

def load_companies():
    path = BASE_DIR / "companies.json"
    if not path.exists():
        print("⚠  companies.json not found"); return []
    companies = json.loads(path.read_text(encoding="utf-8"))
    from collections import Counter
    modes = Counter((c.get("scrape") or {}).get("mode","html") for c in companies)
    print(f"✓ {len(companies)} companies — " +
          " | ".join(f"{m}:{n}" for m,n in sorted(modes.items())))
    return companies

COMPANIES = load_companies()

def current_companies():
    """Reload companies.json so config edits show up without stale process state."""
    global COMPANIES
    COMPANIES = load_companies()
    return COMPANIES

# ── Seen-jobs store ───────────────────────────────────────────────────────────

def load_seen():
    if SEEN_FILE.exists():
        try:   return json.loads(SEEN_FILE.read_text(encoding="utf-8"))
        except: return {}
    return {}

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, indent=2, ensure_ascii=False))

def make_id(company, url, title):
    return hashlib.sha256(f"{company}|{url}|{title}".encode()).hexdigest()[:20]

def norm(s):
    return " ".join((s or "").split())

_US_STATES = {
    'AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA',
    'KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ',
    'NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT',
    'VA','WA','WV','WI','WY','DC',
}

def normalize_location(loc):
    if not loc:
        return ''
    loc = ' '.join(loc.split())
    lower = loc.lower()
    # Remote variants
    if lower in ('remote','anywhere','worldwide','global','distributed',
                 'work from home','wfh','remote / anywhere','remote-first'):
        return 'Remote'
    if lower.startswith('remote') and len(lower) <= 14:
        return 'Remote'
    # Strip US country suffix and re-add normalized ", US"
    m = re.search(r',?\s*(united states of america|united states|usa|u\.s\.a\.|u\.s\.)\s*$', lower)
    if m:
        base = loc[:m.start()].strip().rstrip(',').strip()
        return (base + ', US') if base else 'US'
    # "City, ST" or "City ST" where ST is a US 2-letter state abbrev → append ", US"
    m2 = re.search(r',\s*([A-Z]{2})\s*$', loc)
    if m2 and m2.group(1) in _US_STATES:
        return loc + ', US'
    return loc

def _parse_posted(raw):
    """Parse a date string to ISO YYYY-MM-DD; return '' on failure."""
    if not raw:
        return ''
    raw = str(raw).strip()
    m = re.match(r'(\d{2})/(\d{2})/(\d{4})', raw)
    if m:
        return f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    return raw[:10]

def company_categories_map():
    return {c.get("name"): c.get("categories", []) for c in current_companies()}

# ── URL helpers ───────────────────────────────────────────────────────────────

def has_include(url, pattern):
    return pattern.lower() in url.lower()

def get_param_title(url, param):
    if not param: return ""
    pname = param.lstrip("&?").rstrip("=")
    qs    = parse_qs(urlparse(url).query)
    if pname in qs:
        return unquote(qs[pname][0]).replace("+", " ").strip()
    marker = param if param.startswith(("&","?")) else f"&{param}"
    idx    = url.lower().find(marker.lower())
    if idx != -1:
        val = url[idx+len(marker):].split("&")[0].split("#")[0]
        return unquote(val).replace("+", " ").replace("-", " ").strip()
    return ""

def make_paged_url(base, pg_cfg, idx):
    param = pg_cfg.get("param", "page")
    step  = pg_cfg.get("step")
    start = int(pg_cfg.get("start", 1))
    val   = start + (idx * int(step)) if step else start + idx
    fi    = base.find("#")
    frag  = base[fi:] if fi != -1 else ""
    base  = base[:fi]  if fi != -1 else base
    parts = list(urlparse(base))
    qs    = parse_qs(parts[4], keep_blank_values=True)
    qs[param] = [str(val)]
    parts[4]  = urlencode(qs, doseq=True)
    return urlunparse(parts) + frag

# ══════════════════════════════════════════════════════════════════════════════
#  SCRAPERS
# ══════════════════════════════════════════════════════════════════════════════

# ── Greenhouse ────────────────────────────────────────────────────────────────

def fetch_greenhouse(scrape, seen, company):
    token = scrape.get("board_token","")
    if not token:
        return [], "no board_token"
    # Try US API first, fall back to EU
    for base in ["https://boards-api.greenhouse.io", "https://boards-api.eu.greenhouse.io"]:
        url = f"{base}/v1/boards/{token}/jobs?content=true"
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 404:
                continue
            r.raise_for_status()
            data = r.json()
            jobs = []
            for j in data.get("jobs",[]):
                title  = norm(j.get("title") or "")
                jurl   = j.get("absolute_url") or ""
                loc    = normalize_location(norm((j.get("location") or {}).get("name") or ""))
                posted = _parse_posted(j.get("updated_at") or "")
                if title and jurl:
                    jobs.append({"title":title,"url":jurl,"location":loc,"source":"greenhouse","posted":posted})
            return jobs, None
        except Exception as e:
            last_err = str(e)
            continue
    return [], last_err

# ── Lever ─────────────────────────────────────────────────────────────────────

def fetch_lever(scrape, seen, company):
    slug = scrape.get("lever_slug","")
    if not slug:
        return [], "no lever_slug"
    url  = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)
    if not isinstance(data, list): return [], "unexpected response"
    jobs = []
    for j in data:
        title      = norm(j.get("text") or "")
        jurl       = j.get("hostedUrl") or ""
        loc        = normalize_location(norm((j.get("categories") or {}).get("location") or ""))
        created_ms = j.get("createdAt") or 0
        posted     = datetime.fromtimestamp(created_ms/1000, tz=timezone.utc).strftime("%Y-%m-%d") if created_ms else ""
        if title and jurl:
            jobs.append({"title":title,"url":jurl,"location":loc,"source":"lever","posted":posted})
    return jobs, None

# ── Ashby ─────────────────────────────────────────────────────────────────────

def fetch_ashby(scrape, seen, company):
    """Ashby GraphQL API — covers companies using jobs.ashbyhq.com"""
    slug = scrape.get("ashby_slug","")
    if not slug:
        return [], "no ashby_slug"
    payload = {
        "operationName": "ApiJobBoardWithTeams",
        "variables": {"organizationHostedJobsPageName": slug},
        "query": (
            "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {"
            "  jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {"
            "    jobPostings {"
            "      id title locationName employmentType"
            "    }"
            "  }"
            "}"
        ),
    }
    try:
        r = requests.post(
            "https://jobs.ashbyhq.com/api/non-user-graphql",
            json=payload,
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)
    if data.get("errors"):
        return [], "; ".join((err.get("message") or str(err)) for err in data.get("errors", []))
    board    = ((data.get("data") or {}).get("jobBoard")) or {}
    postings = board.get("jobPostings") or []
    jobs = []
    for j in postings:
        title   = norm(j.get("title") or "")
        jid     = j.get("id") or ""
        jurl    = f"https://jobs.ashbyhq.com/{slug}/{jid}" if jid else ""
        raw_loc = j.get("locationName") or ""
        loc     = normalize_location(norm(raw_loc))
        posted  = ""
        if title:
            jobs.append({"title":title,"url":jurl,"location":loc,"source":"ashby","posted":posted})
    return jobs, None

# ── Workday ───────────────────────────────────────────────────────────────────

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
    if not m:
        return [], f"cannot parse Workday URL: {list_url}"

    tenant    = m.group(1)
    wd_domain = m.group(2)
    board     = m.group(3) or tenant

    api_url  = f"https://{tenant}.{wd_domain}/wday/cxs/{tenant}/{board}/jobs"
    base_url = f"https://{tenant}.{wd_domain}"

    max_pages       = int(scrape.get("max_pages", 30))
    limit           = 20
    jobs            = []
    total           = None
    consec_all_seen = 0

    for idx in range(max_pages):
        offset = idx * limit
        try:
            r = requests.post(
                api_url,
                json={"appliedFacets":{},"limit":limit,"offset":offset,"searchText":""},
                headers={**HEADERS, "Content-Type":"application/json", "Accept":"application/json"},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            if jobs: break
            return [], str(e)

        postings = data.get("jobPostings",[])
        if not postings:
            break
        if total is None:
            total = data.get("total", 0)

        page_jobs = []
        for j in postings:
            title  = norm(j.get("title") or "")
            path   = j.get("externalPath") or ""
            jurl   = base_url + path if path else ""
            loc    = normalize_location(norm(j.get("locationsText") or ""))
            posted = _parse_posted(j.get("postedDate") or "")
            if title:
                page_jobs.append({"title":title,"url":jurl,"location":loc,"source":"workday","posted":posted})
        jobs.extend(page_jobs)

        # Early stop on incremental runs: if 2 full pages are all already-seen, stop
        if seen and page_jobs:
            all_seen = all(make_id(company, j["url"], j["title"]) in seen for j in page_jobs)
            consec_all_seen = consec_all_seen + 1 if all_seen else 0
            if consec_all_seen >= 2:
                break

        if total and offset + limit >= total:
            break
        time.sleep(0.2)

    return jobs, None

# ── BambooHR ──────────────────────────────────────────────────────────────────

def fetch_bamboohr(scrape, seen, company):
    """BambooHR careers JSON endpoint: https://{slug}.bamboohr.com/careers/list"""
    slug = scrape.get("bamboohr_slug","")
    if not slug:
        return [], "no bamboohr_slug"
    url = f"https://{slug}.bamboohr.com/careers/list"
    try:
        r = requests.get(
            url,
            headers={**HEADERS, "Accept":"application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)

    postings = data.get("result", []) if isinstance(data, dict) else []
    if not isinstance(postings, list):
        return [], "unexpected response"

    jobs = []
    for j in postings:
        if not isinstance(j, dict):
            continue
        title = norm(j.get("jobOpeningName") or j.get("title") or "")
        jid   = j.get("id") or ""
        loc_data = j.get("location") or {}
        if isinstance(loc_data, dict):
            loc = normalize_location(norm(", ".join(
                x for x in [loc_data.get("city"), loc_data.get("state")] if x
            )))
        else:
            loc = normalize_location(norm(loc_data))
        jurl = f"https://{slug}.bamboohr.com/careers/view/{jid}" if jid else ""
        if title and jurl:
            jobs.append({"title":title,"url":jurl,"location":loc,"source":"bamboohr","posted":""})
    return jobs, None

# ── iCIMS ─────────────────────────────────────────────────────────────────────

def _icims_title_from_url(job_url):
    path = urlparse(job_url).path
    m = re.search(r"/jobs/\d+/([^/]+)/job", path)
    if not m:
        return ""
    return norm(unquote(m.group(1)).replace("-", " ")).title()

def _icims_jobs_from_html(html, page_url):
    soup = BeautifulSoup(html, "lxml")
    jobs = {}
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if "/jobs/" not in href or not href.rstrip("/").endswith("/job"):
            continue
        jurl = urljoin(page_url, href)
        title = norm(a.get_text(" ", strip=True)) or _icims_title_from_url(jurl)
        if title and len(title) >= 3:
            jobs[jurl] = {"title":title,"url":jurl,"location":"","source":"icims","posted":""}
    return jobs

def fetch_icims(scrape, seen, company):
    """
    iCIMS boards usually expose https://careers-{slug}.icims.com/sitemap.xml.
    Some redirect to a different subdomain or block sitemap XML, so fall back to
    /jobs/search and parse job links from the result page.
    """
    slug = scrape.get("icims_slug","")
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
        sitemap_url = base + "/sitemap.xml"
        try:
            r = requests.get(sitemap_url, headers={**HEADERS, "Accept":"application/xml,text/xml,*/*"}, timeout=18)
            if r.status_code == 200 and "xml" in r.headers.get("Content-Type", "").lower():
                root = ET.fromstring(r.content)
                jobs = []
                for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
                    jurl = (loc.text or "").strip()
                    if "/jobs/" not in jurl or not jurl.rstrip("/").endswith("/job"):
                        continue
                    title = _icims_title_from_url(jurl)
                    if title:
                        jobs.append({"title":title,"url":jurl,"location":"","source":"icims","posted":""})
                if jobs:
                    return jobs, None
            elif r.status_code not in (403, 404):
                first_err = f"{sitemap_url}: HTTP {r.status_code}"
        except Exception as e:
            first_err = str(e)

        try:
            r = requests.get(
                base + "/jobs/search?ss=1",
                headers={**HEADERS, "Accept":"text/html,*/*"},
                timeout=20,
            )
            r.raise_for_status()
            jobs = _icims_jobs_from_html(r.text, r.url)
            if jobs:
                return list(jobs.values()), None
        except Exception as e:
            if not first_err:
                first_err = str(e)

    return [], first_err or "no jobs found"

# ── Apple ─────────────────────────────────────────────────────────────────────

def fetch_apple(scrape, seen, company):
    """POST https://jobs.apple.com/api/role/search"""
    do_sort   = scrape.get("sort", True)
    max_pages = int(scrape.get("max_pages", 20))
    jobs, page = [], 0
    for _ in range(max_pages):
        payload = {
            "filters":{"postingpostLocation":["UNITED STATES OF AMERICA"],
                       "postingpostLocation_or":True},
            "page":page, "locale":"en-us", "sort":"newest",
        }
        try:
            r = requests.post("https://jobs.apple.com/api/role/search",
                              json=payload,
                              headers={**HEADERS,"Content-Type":"application/json"},
                              timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)
        results = data.get("searchResults",[])
        if not results: break
        stop = False
        for j in results:
            title  = norm(j.get("postingTitle") or "")
            pid    = j.get("positionId") or ""
            slug_u = re.sub(r"[^a-z0-9]+","-",title.lower()).strip("-")
            teams  = j.get("teams") or [{}]
            tc     = (teams[0].get("teamCode") or "") if teams else ""
            tp     = f"?team={tc}" if tc else ""
            jurl   = f"https://jobs.apple.com/en-us/details/{pid}/{slug_u}{tp}" if pid else ""
            locs   = j.get("locations") or [{}]
            loc    = normalize_location(norm((locs[0].get("name") or "") if locs else ""))
            if title and pid:
                jid = make_id(company, jurl, title)
                if do_sort and jid in seen: stop=True; break
                jobs.append({"title":title,"url":jurl,"location":loc,"source":"apple"})
        if stop: break
        total = data.get("totalRecords",0)
        ps    = data.get("pageSize",20)
        page += 1
        if page*ps >= total: break
        time.sleep(0.3)
    return jobs, None


# ── Google ────────────────────────────────────────────────────────────────────

def fetch_google(scrape, seen, company):
    """GET careers.google.com/api/v3/search/"""
    do_sort   = scrape.get("sort", True)
    max_pages = int(scrape.get("max_pages", 20))
    jobs, page, ps = [], 1, 20
    for _ in range(max_pages):
        try:
            r = requests.get(
                "https://careers.google.com/api/v3/search/",
                params={"location":"United States","page":page,"pageSize":ps},
                headers={**HEADERS,"Referer":"https://careers.google.com/"},
                timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)
        results = data.get("jobs",[])
        if not results: break
        stop = False
        for j in results:
            title = norm(j.get("title") or "")
            jid_s = j.get("id") or ""
            jurl  = f"https://careers.google.com/jobs/results/{jid_s}/" if jid_s else ""
            loc   = normalize_location(norm((j.get("locations") or [""])[0]))
            if title and jurl:
                jid = make_id(company, jurl, title)
                if do_sort and jid in seen: stop=True; break
                jobs.append({"title":title,"url":jurl,"location":loc,"source":"google"})
        if stop: break
        if page >= data.get("pageCount",1): break
        page += 1
        time.sleep(0.3)
    return jobs, None


# ── Microsoft ─────────────────────────────────────────────────────────────────

def fetch_microsoft(scrape, seen, company):
    """GET gcsservices.careers.microsoft.com/search/api/v1/search"""
    max_pages = int(scrape.get("max_pages", 20))
    jobs, page, ps = [], 1, 20
    for _ in range(max_pages):
        try:
            r = requests.get(
                "https://gcsservices.careers.microsoft.com/search/api/v1/search",
                params={"l":"en_us","pg":page,"pgSz":ps,"o":"Recent","flt":"true"},
                headers={**HEADERS,"Referer":"https://jobs.careers.microsoft.com/"},
                timeout=20)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return jobs, str(e)
        result   = (data.get("operationResult") or {}).get("result") or {}
        postings = result.get("jobs",[])
        if not postings: break
        for j in postings:
            title = norm(j.get("title") or "")
            jid_s = j.get("jobId") or ""
            jurl  = f"https://jobs.careers.microsoft.com/global/en/job/{jid_s}/" if jid_s else ""
            loc   = normalize_location(norm(j.get("officeLocation") or j.get("primaryWorkLocation") or ""))
            if title and jurl:
                jobs.append({"title":title,"url":jurl,"location":loc,"source":"microsoft"})
        if page*ps >= result.get("totalJobs",0): break
        page += 1
        time.sleep(0.3)
    return jobs, None


# ── Pinpoint ─────────────────────────────────────────────────────────────────

def fetch_pinpoint(scrape, seen, company):
    """Pinpoint careers JSON endpoint, e.g. https://careers.example.com/postings.json"""
    list_url = scrape.get("list_url","")
    if not list_url:
        return [], "no list_url"
    try:
        r = requests.get(
            list_url,
            headers={**HEADERS, "Accept":"application/json"},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        return [], str(e)

    postings = data.get("data") if isinstance(data, dict) else data
    if not isinstance(postings, list):
        return [], "unexpected response"

    host = scrape.get("public_host","").rstrip("/")
    jobs = []
    for j in postings:
        if not isinstance(j, dict):
            continue
        title = norm(j.get("title") or "")
        path  = j.get("path") or ""
        jurl  = j.get("url") or ""
        if host and path:
            jurl = host + path
        loc_data = j.get("location") or {}
        loc = normalize_location(norm(loc_data.get("name") if isinstance(loc_data, dict) else loc_data))
        if title and jurl:
            jobs.append({"title":title,"url":jurl,"location":loc,"source":"pinpoint"})
    return jobs, None


# ── HTML ──────────────────────────────────────────────────────────────────────

def fetch_html(scrape, seen, company):
    """Universal HTML scraper for simple static career pages."""
    list_url    = scrape.get("list_url","")
    include     = scrape.get("include","")
    title_param = scrape.get("title_param","")
    do_sort     = scrape.get("sort", False)
    pg_cfg      = scrape.get("pagination") or {"type":"none"}
    pg_type     = pg_cfg.get("type","none")
    max_pages   = int(pg_cfg.get("max_pages", 20))

    if not list_url: return [], "no list_url"
    if not include:  return [], "no include pattern"

    visited: set = set()

    def scrape_one(page_url):
        try:
            r = requests.get(page_url, headers=HEADERS, timeout=25)
            r.raise_for_status()
        except Exception as e:
            return {}, str(e), None, False

        soup  = BeautifulSoup(r.text, "lxml")
        found = {}
        stop  = False

        for a in soup.find_all("a", href=True):
            href = (a.get("href") or "").strip()
            if not href or href.startswith(("#","javascript:","mailto:")):
                continue
            abs_url = urljoin(page_url, href)
            if not has_include(abs_url, include):
                continue
            if abs_url in visited:
                continue
            visited.add(abs_url)

            if title_param:
                title = get_param_title(abs_url, title_param)
            else:
                title = norm(a.get_text(" ", strip=True))

            if not title or len(title) < 4:
                seg   = urlparse(abs_url).path.rstrip("/").split("/")[-1]
                title = re.sub(r"[-_]"," ", seg).strip()
            if not title or len(title) < 4:
                continue

            jid = make_id(company, abs_url, title)
            if do_sort and jid in seen:
                stop = True; break

            found[abs_url] = {"title":title,"url":abs_url,"location":"","source":"html"}

        next_url = None
        for sel in ["a[rel='next']","a.next","[aria-label='Next page']","li.next a",
                    ".pagination .next a","[class*=next] a"]:
            nxt = soup.select_one(sel)
            if nxt and nxt.get("href"):
                cand = urljoin(page_url, nxt["href"])
                if cand != page_url:
                    next_url = cand; break

        return found, None, next_url, stop

    all_jobs: dict = {}
    first_err      = None

    if pg_type == "none":
        found, err, _, _ = scrape_one(list_url)
        if err: first_err = err
        all_jobs.update(found)

    elif pg_type == "query_page":
        for idx in range(max_pages):
            purl = make_paged_url(list_url, pg_cfg, idx)
            found, err, _, stop = scrape_one(purl)
            if err and not first_err: first_err = err
            new = {k:v for k,v in found.items() if k not in all_jobs}
            all_jobs.update(new)
            if stop or (not new and not found): break
            time.sleep(0.35)

    elif pg_type == "next_link":
        current = list_url
        for _ in range(max_pages):
            found, err, next_url, stop = scrape_one(current)
            if err and not first_err: first_err = err
            all_jobs.update(found)
            if stop or not next_url: break
            current = next_url
            time.sleep(0.35)

    return list(all_jobs.values()), first_err


# ── Playwright ────────────────────────────────────────────────────────────────

def fetch_playwright(scrape, seen, company):
    try:
        from playwright_scraper import fetch_playwright as _pw
        return _pw(scrape, seen, company)
    except Exception:
        import sys, os
        sys.path.insert(0, os.path.join(BASE_DIR, "scrapers"))
        from playwright_scraper import fetch_playwright as _pw
        return _pw(scrape, seen, company)


# ── Dispatch ──────────────────────────────────────────────────────────────────

FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever":      fetch_lever,
    "ashby":      fetch_ashby,
    "workday":    fetch_workday,
    "bamboohr":   fetch_bamboohr,
    "icims":      fetch_icims,
    "apple":      fetch_apple,
    "google":     fetch_google,
    "microsoft":  fetch_microsoft,
    "pinpoint":   fetch_pinpoint,
    "playwright": fetch_playwright,
    "html":       fetch_html,
}

def fetch_jobs(company, seen):
    scrape = company.get("scrape") or {}
    mode   = scrape.get("mode","html")
    fn     = FETCHERS.get(mode)
    if not fn: return [], f"unknown mode: {mode}"
    jobs, err = fn(scrape, seen, company["name"])
    include_text = scrape.get("text_include")
    if include_text and jobs:
        needles = include_text if isinstance(include_text, list) else [include_text]
        needles = [str(n).lower() for n in needles if str(n).strip()]
        if needles:
            jobs = [
                j for j in jobs
                if any(
                    n in " ".join([j.get("title",""), j.get("location",""), j.get("url","")]).lower()
                    for n in needles
                )
            ]
    return jobs, err


# ── Scan ──────────────────────────────────────────────────────────────────────

scan_state = {
    "running":False, "done":False, "started_at":0,
    "progress":[], "results":[], "new_count":0,
}

_API_MODES = {"greenhouse","lever","ashby","workday","bamboohr","icims","apple","google","microsoft","pinpoint"}

def scan_runner(selected_names):
    try:
        do_scan(selected_names)
    except Exception as e:
        scan_state.update(
            running=False,
            done=True,
            results=[],
            new_count=0,
        )
        for p in scan_state.get("progress", []):
            if p.get("status") in {"pending", "scanning"}:
                p.update(status="error", error=str(e), count=0, new=0)

def do_scan(selected_names):
    seen    = load_seen()
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    results = []
    t_scan  = time.monotonic()

    scan_state.update(running=True, done=False, started_at=time.time(), progress=[], results=[], new_count=0)
    companies = current_companies()
    target = [c for c in companies if c["name"] in selected_names]

    # Pre-populate progress list so the frontend sees all companies immediately
    progress = [
        {"company": c["name"],
         "mode":    (c.get("scrape") or {}).get("mode", "html"),
         "status":  "pending", "count": 0, "new": 0}
        for c in target
    ]
    scan_state["progress"] = progress
    all_summaries = []

    def _commit(i, company, raw, err, duration):
        name     = company["name"]
        cats     = company.get("categories", [])
        new_this = 0
        with _seen_lock:
            for j in raw:
                jid    = make_id(name, j["url"], j["title"])
                is_new = jid not in seen
                if is_new:
                    seen[jid] = {
                        "first_seen": now_str,
                        "company": name,
                        "title": j["title"],
                        "url": j.get("url", ""),
                    }
                    new_this += 1
                results.append({
                    **j, "id": jid, "company": name, "categories": cats,
                    "is_new": is_new, "first_seen": seen[jid]["first_seen"],
                })
        entry = {
            "company":    name,
            "mode":       progress[i]["mode"],
            "status":     "done" if not err else "error",
            "count":      len(raw),
            "new":        new_this,
            "duration_s": duration,
        }
        if err:
            entry["error"] = err
        progress[i].update(entry)
        all_summaries.append(dict(entry))

    def _run_one(i, company):
        progress[i]["status"] = "scanning"
        t0 = time.monotonic()
        try:
            raw, err = fetch_jobs(company, seen)
            return i, company, raw or [], err, round(time.monotonic() - t0, 1)
        except Exception as e:
            return i, company, [], str(e), round(time.monotonic() - t0, 1)

    api_cos = [(i, c) for i, c in enumerate(target)
               if (c.get("scrape") or {}).get("mode", "html") in _API_MODES]
    pw_cos  = [(i, c) for i, c in enumerate(target)
               if (c.get("scrape") or {}).get("mode", "html") not in _API_MODES]

    # API companies run in parallel (pure HTTP, thread-safe)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(_run_one, i, c): None for i, c in api_cos}
        for fut in concurrent.futures.as_completed(futs):
            _commit(*fut.result())

    # Playwright / HTML companies run sequentially; browsers are heavy and
    # several career sites are fragile when many browser instances launch.
    for i, c in pw_cos:
        _commit(*_run_one(i, c))

    save_seen(seen)
    upsert_jobs(results)

    # Write scan summary to disk
    elapsed = round(time.monotonic() - t_scan, 1)
    failed  = [s for s in all_summaries if s["status"] == "error"]
    zero    = [s for s in all_summaries if s["status"] == "done" and s["count"] == 0]
    SUMMARY_FILE.write_text(json.dumps({
        "scan_time":        now_str,
        "duration_seconds": elapsed,
        "total_companies":  len(target),
        "success":          len(all_summaries) - len(failed),
        "failed_count":     len(failed),
        "zero_jobs_count":  len(zero),
        "failed":  [{"company": s["company"], "mode": s.get("mode"), "error": s.get("error")} for s in failed],
        "zero_jobs": [{"company": s["company"], "mode": s.get("mode")} for s in zero],
        "per_company": sorted(all_summaries, key=lambda x: x["company"]),
    }, indent=2, ensure_ascii=False))

    results.sort(key=lambda j: (0 if j["is_new"] else 1, j["company"]))
    scan_state.update(
        running=False, done=True, results=results,
        new_count=sum(1 for j in results if j["is_new"]),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", companies=current_companies())

@app.route("/api/scan", methods=["POST"])
def start_scan():
    if scan_state["running"] and time.time() - float(scan_state.get("started_at") or 0) > 6 * 60 * 60:
        scan_state.update(running=False, done=True)
    if scan_state["running"]:
        return jsonify({"ok":True, "already_running":True})
    data     = request.get_json(silent=True) or {}
    selected = data.get("companies",[])
    if not selected:
        return jsonify({"error":"No companies selected"}), 400
    threading.Thread(target=scan_runner, args=(selected,), daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/reset_scan", methods=["POST"])
def reset_scan():
    scan_state.update(running=False, done=True, started_at=0, progress=[], results=[], new_count=0)
    return jsonify({"ok":True})

@app.route("/api/progress")
def get_progress():
    return jsonify({
        "running":   scan_state["running"],
        "done":      scan_state["done"],
        "progress":  scan_state["progress"],
        "results":   scan_state["results"],
        "new_count": scan_state["new_count"],
    })

@app.route("/api/reset_seen", methods=["POST"])
def reset_seen():
    if SEEN_FILE.exists(): SEEN_FILE.unlink()
    scan_state["results"] = []
    return jsonify({"ok":True})

@app.route("/api/companies")
def api_companies():
    return jsonify(current_companies())

@app.route("/api/summary")
def api_summary():
    if SUMMARY_FILE.exists():
        return jsonify(json.loads(SUMMARY_FILE.read_text()))
    return jsonify({"error": "No scan summary yet"}), 404

@app.route("/api/jobs")
def api_jobs():
    keyword  = (request.args.get("keyword")  or "").strip().lower()
    location = (request.args.get("location") or "").strip().lower()
    since    = (request.args.get("since")    or "").strip()
    category = (request.args.get("category") or "").strip()
    limit    = min(int(request.args.get("limit", 500) or 500), 5000)

    clauses, params = [], []
    if keyword:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(company) LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if location:
        clauses.append("LOWER(location) LIKE ?")
        params.append(f"%{location}%")
    if since:
        clauses.append("first_seen >= ?")
        params.append(since)
    if category:
        clauses.append("categories LIKE ?")
        params.append(f'%"{category}"%')

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql   = (f"SELECT id, title, url, company, location, source, categories, first_seen, posted "
             f"FROM jobs {where} ORDER BY COALESCE(NULLIF(posted,''), first_seen) DESC LIMIT ?")
    params.append(limit)

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            total_rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        jobs = []
        for r in rows:
            cats   = json.loads(r["categories"] or "[]")
            fs     = (r["first_seen"] or "")[:10]
            posted = (r["posted"] or "")[:10]
            jobs.append({
                "id":         r["id"],
                "title":      r["title"],
                "url":        r["url"],
                "company":    r["company"],
                "location":   r["location"] or "",
                "source":     r["source"] or "",
                "categories": cats,
                "first_seen": r["first_seen"],
                "posted":     posted,
                "is_new":     fs == today,
            })
        if not jobs and total_rows == 0:
            jobs = jobs_from_seen_fallback(keyword, location, since, category, limit, today)
        return jsonify(jobs)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def jobs_from_seen_fallback(keyword, location, since, category, limit, today):
    cats_by_company = company_categories_map()
    jobs = []
    for jid, item in load_seen().items():
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        company = item.get("company", "")
        first_seen = item.get("first_seen", "")
        cats = cats_by_company.get(company, [])
        loc = item.get("location", "")
        if keyword and keyword not in f"{title} {company}".lower():
            continue
        if location and location not in loc.lower():
            continue
        if since and first_seen < since:
            continue
        if category and category not in cats:
            continue
        fs = first_seen[:10]
        jobs.append({
            "id":         jid,
            "title":      title,
            "url":        item.get("url", ""),
            "company":    company,
            "location":   loc,
            "source":     item.get("source", "seen"),
            "categories": cats,
            "first_seen": first_seen,
            "posted":     "",
            "is_new":     fs == today,
        })
    jobs.sort(key=lambda j: j.get("first_seen", ""), reverse=True)
    return jobs[:limit]

if __name__ == "__main__":
    print("\n🚀  JobRadar v4 → http://localhost:5000\n")
    app.run(debug=False, port=5000, threaded=True)
