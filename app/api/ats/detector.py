"""
ATS auto-detection for JobRadar.

Given a careers URL, detects which ATS the company uses and returns a
ready-to-paste scrape config for companies.json.

Detection runs in three phases:
  1. URL pattern match  — regex on the provided URL, no HTTP request
  2. Redirect + HTML    — follow redirects, scan final page for ATS links/embeds
  3. API probe          — quick call to the detected ATS to verify and get job count
"""

import re
import requests
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from ..common import HEADERS

_TIMEOUT = 12


# ── Phase 1: URL → config ─────────────────────────────────────────────────────

def _parse_url(url):
    """Return a scrape config dict if url matches a known ATS pattern, else None."""
    url = (url or "").strip()
    if not url:
        return None
    # Skip static asset URLs — they're never career pages
    if re.search(r'\.(png|jpg|jpeg|gif|svg|webp|js|css|woff2?|ttf|eot|ico|map|json)(\?|$)', url, re.I):
        return None

    # Greenhouse
    m = re.search(r'boards(?:\.eu)?\.greenhouse\.io/([^/?#\s"\'<>]+)', url, re.I)
    if m:
        token = m.group(1).split('/')[0]
        # "embed" is Greenhouse's own CDN path, not a board token.
        # Real token is in the ?for= query param of embed script URLs.
        _GH_INTERNAL = {"embed", "js", "api", "public", "assets", "css", "job_board"}
        if token.lower() in _GH_INTERNAL:
            for_m = re.search(r'[?&]for=([a-z0-9_-]+)', url, re.I)
            if for_m:
                token = for_m.group(1)
            else:
                return None  # embed script with no for= — skip
        cfg = {"mode": "greenhouse", "board_token": token}
        if ".eu." in m.group(0):
            cfg["region"] = "eu"
        return cfg

    # Lever
    m = re.search(r'jobs\.lever\.co/([^/?#\s"\'<>]+)', url, re.I)
    if m:
        return {"mode": "lever", "lever_slug": m.group(1).split('/')[0]}

    # Ashby
    m = re.search(r'jobs\.ashbyhq\.com/([^/?#\s"\'<>]+)', url, re.I)
    if m:
        return {"mode": "ashby", "ashby_slug": m.group(1).split('/')[0]}

    # Workday myworkdayjobs.com
    m = re.match(r'https?://([^./?#\s]+)\.(wd\d+\.myworkdayjobs\.com)(?:/[a-z]{2}-[A-Z]{2})?/?([^/?#\s]*)', url)
    if m:
        tenant, wd_domain, board = m.group(1), m.group(2), m.group(3) or m.group(1)
        return {"mode": "workday", "list_url": f"https://{tenant}.{wd_domain}/{board}"}

    # Workday myworkdaysite.com
    m = re.match(r'https?://(wd\d+\.myworkdaysite\.com)/recruiting/([^/\s]+)/([^/?#\s]+)', url)
    if m:
        clean = re.match(r'(https?://[^?#\s]+)', url)
        return {"mode": "workday", "list_url": (clean.group(1) if clean else url)}

    # SmartRecruiters
    m = re.search(r'(?:careers|jobs)\.smartrecruiters\.com/([^/?#\s"\'<>]+)', url, re.I)
    if m:
        slug = m.group(1).split('/')[0]
        if len(slug) >= 3 and re.fullmatch(r'[A-Za-z0-9_.-]{3,}', slug) and not re.fullmatch(r'\d+', slug):
            return {"mode": "smartrecruiters", "smartrecruiters_company": slug}

    # BambooHR
    m = re.search(r'([^./\s"\'<>]+)\.bamboohr\.com', url, re.I)
    if m and m.group(1).lower() not in ("www", "app"):
        return {"mode": "bamboohr", "bamboohr_slug": m.group(1)}

    # Workable
    m = re.search(r'apply\.workable\.com/([^/?#\s"\'<>]+)', url, re.I)
    if m:
        return {"mode": "workable", "workable_slug": m.group(1).split('/')[0]}

    # Pinpoint
    m = re.search(r'(https?://[^/\s"\'<>]+\.pinpointhq\.com)', url, re.I)
    if m:
        return {"mode": "pinpoint", "list_url": m.group(1) + "/postings.json"}

    # JazzHR
    m = re.search(r'(https?://(?:[^/\s"\'<>]+\.jazz\.co|app\.jazz\.co/apply/[^/?#\s"\'<>]+))', url, re.I)
    if m:
        return {"mode": "jazzhr", "list_url": m.group(1).rstrip('/')}
    if re.search(r'recruitingbypaycor\.com', url, re.I):
        clean = re.match(r'(https?://[^?#\s]+)', url)
        return {"mode": "jazzhr", "list_url": (clean.group(1) if clean else url)}

    # Jobvite
    m = re.search(r'jobs\.jobvite\.com/([^/?#\s"\'<>]+)', url, re.I)
    if m:
        slug = m.group(1).split('/')[0]
        return {"mode": "jobvite", "list_url": f"https://jobs.jobvite.com/{slug}/jobs"}

    # Oracle Cloud
    m = re.search(r'(https?://[^/\s"\'<>]+)/hcmUI/CandidateExperience', url, re.I)
    if m:
        host = m.group(1)
        site_m = re.search(r'/sites/([^/?#\s"\'<>]+)', url)
        site = site_m.group(1) if site_m else "CX_1"
        return {
            "mode": "oraclecloud",
            "list_url": f"{host}/hcmUI/CandidateExperience/en/sites/{site}/jobs",
            "site": site,
        }

    # Eightfold
    m = re.search(r'(https?://[^/\s"\'<>]+\.eightfold\.ai)', url, re.I)
    if m:
        host = urlparse(m.group(1)).netloc
        sub = host.split('.eightfold.ai')[0]
        return {
            "mode": "eightfold",
            "list_url": m.group(1).rstrip('/') + "/careers",
            "domain": f"{sub}.com",
        }

    # iCIMS — always strip to base domain (handles job-level URLs like /jobs/123/title/job)
    # Skip login/dashboard redirects — those are auth walls, not job listings
    m = re.match(r'(https?://[^/\s"\'<>]+\.icims\.com)', url, re.I)
    if m:
        path = urlparse(url).path.lower()
        if not re.search(r'/(?:login|connect|dashboard|apply)', path):
            return {"mode": "icims", "icims_slug": m.group(1)}

    # TalentBrew / Phenom — internal sentinel set by _urls_from_html
    if url.startswith("JOBRADAR:talentbrew:"):
        rest = url[len("JOBRADAR:talentbrew:"):]
        search_url, job_prefix = rest.split("||", 1)
        return {"mode": "talentbrew", "list_url": search_url, "include": job_prefix}

    # jobsapi — branded iCIMS-backed career sites with /api/jobs endpoint
    # Detected dynamically in the probe; flag here if URL has /api/jobs path
    if re.search(r'/api/jobs\b', url, re.I):
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        return {"mode": "jobsapi", "api_url": root + "/api/jobs",
                "job_url_prefix": root + "/careers-home/jobs/"}

    # Avature
    m = re.search(r'(https?://[^/\s"\'<>]+\.avature\.net)', url, re.I)
    if m:
        return {"mode": "avature", "list_url": m.group(1) + "/careers/SearchJobs"}

    # Taleo
    if re.search(r'\.taleo\.net', url, re.I):
        clean = re.match(r'(https?://[^?#\s]+)', url)
        return {"mode": "taleo", "list_url": (clean.group(1) if clean else url)}

    # ADP Workforce Now / ADP MyJobs
    if re.search(r'workforcenow\.adp\.com/.*/recruitment\.html|myjobs\.adp\.com/', url, re.I):
        clean = re.match(r'(https?://[^#\s]+)', url)
        list_url = clean.group(1) if clean else url
        host = urlparse(list_url).netloc.lower()
        cfg = {
            "mode": "adp",
            "list_url": list_url,
            "validate_prefix": False,
            "require_id": False,
        }
        if "myjobs.adp.com" in host:
            cfg["prefix"] = re.sub(r'([?#].*)$', '', list_url)
            cfg["include"] = "myjobs.adp.com/"
        else:
            cfg["prefix"] = "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
            cfg["include"] = "workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html"
        return cfg

    # SuccessFactors — exclude CDN subdomains and asset files (images, JS, CSS)
    if re.search(r'successfactors\.(?:com|eu)|sfcareer\.com', url, re.I):
        host = urlparse(url).netloc.lower()
        path = urlparse(url).path.lower()
        # Skip CDN/static hosts and any URL ending in a file extension
        if (not re.search(r'^(?:rmkcdn|cdn|static|assets|fonts)\.', host)
                and not re.search(r'\.(png|jpg|jpeg|gif|svg|webp|js|css|woff|ttf|ico)$', path)):
            clean = re.match(r'(https?://[^?#\s]+)', url)
            return {"mode": "successfactors", "list_url": (clean.group(1) if clean else url)}

    # Phenom — only match actual career portal hosts, not CDN/content delivery subdomains.
    # Career portals use the company's own domain pointing to Phenom, or subdomain
    # patterns like careers.company.com (which have phenompeople refs in their HTML).
    # Phenom's own infrastructure: cdn.*, cdn-prod-static.*, assets.*, content-*.
    if re.search(r'\.phenom\.com', url, re.I):
        host = urlparse(url).netloc.lower()
        if not re.search(r'^(?:cdn|assets|static|content|fonts|js|img|media)[\.-]', host):
            clean = re.match(r'(https?://[^?#\s]+)', url)
            return {"mode": "phenom", "list_url": (clean.group(1) if clean else url)}

    # Rippling embedded ATS boards (ats.rippling.com/{slug}/jobs)
    m = re.match(r'https?://ats\.rippling\.com/([^/?#\s]+)', url, re.I)
    if m:
        slug = m.group(1).split('/')[0]
        return {
            "mode": "html",
            "list_url": f"https://ats.rippling.com/{slug}/jobs",
            "include": f"ats.rippling.com/{slug}/jobs/",
            "require_id": False,
        }

    # Rippling hiring widget
    if re.search(r'rippling\.com/(?:hiring|jobs)', url, re.I):
        clean = re.match(r'(https?://[^?#\s]+)', url)
        return {"mode": "rippling", "list_url": (clean.group(1) if clean else url)}

    return None


# ── Phase 2: HTML extraction ──────────────────────────────────────────────────

_ATS_PATTERNS_RAW = [
    # Greenhouse embed script with for= token: boards.greenhouse.io/embed/...?for=token
    r'boards(?:\.eu)?\.greenhouse\.io/embed/[^\s"\'<>]*\?[^\s"\'<>]*for=[a-z0-9_-]+',
    r'boards(?:\.eu)?\.greenhouse\.io/[^/?#\s"\'<>]+',
    r'jobs\.lever\.co/[^/?#\s"\'<>]+',
    r'jobs\.ashbyhq\.com/[^/?#\s"\'<>]+',
    r'[^./\s"\'<>]+\.wd\d+\.myworkdayjobs\.com(?:/[^/\s"\'<>]+)+',
    r'wd\d+\.myworkdaysite\.com/recruiting/[^/\s"\'<>]+/[^/?#\s"\'<>]+',
    r'(?:careers|jobs)\.smartrecruiters\.com/[^/?#\s"\'<>]+',
    r'[^./\s"\'<>]+\.bamboohr\.com/(?:careers|jobs)',
    r'apply\.workable\.com/[^/?#\s"\'<>]+',
    r'[^./\s"\'<>]+\.pinpointhq\.com',
    r'jobs\.jobvite\.com/[^/?#\s"\'<>]+',
    r'[^./\s"\'<>]+\.eightfold\.ai/careers',
    r'[^./\s"\'<>]+\.jazz\.co(?:/[^/\s"\'<>]*)*',
    r'app\.jazz\.co/apply/[^/?#\s"\'<>]+',
    r'ats\.rippling\.com/[^/\s"\'<>]+/jobs',
    r'recruitingbypaycor\.com/[^/\s"\'<>]+',
    r'[^./\s"\'<>]+\.icims\.com',
    r'[^./\s"\'<>]+\.avature\.net',
    r'[^./\s"\'<>]+\.taleo\.net',
    r'workforcenow\.adp\.com/[^\s"\'<>]+',
    r'myjobs\.adp\.com/[^\s"\'<>]+',
    r'successfactors\.com/[^\s"\'<>]*',
    r'[^./\s"\'<>]+\.phenom\.com',
    r'hcmUI/CandidateExperience[^\s"\'<>]*',
]

def _urls_from_html(html, base_url):
    """Extract all candidate ATS URLs from page HTML."""
    soup = BeautifulSoup(html, "lxml")
    urls = set()

    for tag in soup.find_all(href=True):
        v = (tag.get("href") or "").strip()
        if v and not v.startswith(('javascript:', 'mailto:', '#', 'tel:')):
            urls.add(urljoin(base_url, v))

    for tag in soup.find_all(src=True):
        v = (tag.get("src") or "").strip()
        if v:
            urls.add(urljoin(base_url, v))

    for tag in soup.find_all(True):
        for attr, val in tag.attrs.items():
            if isinstance(val, str) and attr.startswith('data-') and val.startswith('http'):
                urls.add(val)

    # Scan raw HTML text for ATS domain patterns (catches JS-embedded configs)
    for pat in _ATS_PATTERNS_RAW:
        for found in re.finditer(pat, html, re.I):
            candidate = found.group(0).lstrip('/')
            if candidate.startswith('http'):
                urls.add(candidate)
            else:
                urls.add("https://" + candidate)

    # Next.js / env-var Greenhouse token (e.g. NEXT_PUBLIC_GREENHOUSE_BOARD_TOKEN)
    # Token may live only in a linked JS bundle — fetch the most likely one.
    gh_env = re.search(
        r'GREENHOUSE_BOARD_TOKEN["\s]*[:=]["\s]*["\']([a-z][a-z0-9_-]{2,})["\']',
        html, re.I,
    )
    if gh_env:
        token = gh_env.group(1)
        if len(token) > 2:
            urls.add(f"https://boards.greenhouse.io/{token}")
    elif "greenhouse" not in html.lower():
        # Try fetching the main Next.js / app JS bundle for embedded tokens
        for js_src in re.findall(r'src="(/_next/static/chunks/pages/[^"]+\.js|/_next/static/[^"]+/pages/index[^"]+\.js)"', html)[:3]:
            try:
                rj = requests.get(urljoin(base_url, js_src), headers=HEADERS, timeout=8)
                m = re.search(
                    r'GREENHOUSE_BOARD_TOKEN["\s]*[:=]["\s]*["\']([a-z][a-z0-9_-]{2,})["\']',
                    rj.text, re.I,
                )
                if m and len(m.group(1)) > 2:
                    urls.add(f"https://boards.greenhouse.io/{m.group(1)}")
                    break
            except Exception:
                pass

    # TalentBrew / Phenom — detected via page meta tags.
    tb_page = re.search(r'(?:tb-page-type|career-site-page-type|talentbrew|jibecdn)[^"]{0,60}(?:search|job|career)', html, re.I)
    if tb_page:
        parsed_base = urlparse(base_url)
        tb_base = f"{parsed_base.scheme}://{parsed_base.netloc}"
        search_path = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+search[^"]*)"', html)
        search_url = search_path.group(1) if search_path else f"{tb_base}/search-jobs"
        # Use a non-URL sentinel that won't be confused with real URLs
        urls.add(f"JOBRADAR:talentbrew:{search_url}||{tb_base}/job/")

    # Greenhouse embed: data-token / for="..." attribute
    for pat in [
        r'(?:data-token|"for"|\'for\')\s*[=:]\s*["\']([^"\']+)["\']',
        r'Grnhse\.Setup\s*\(\s*["\']([^"\']+)["\']',
    ]:
        m = re.search(pat, html, re.I)
        if m:
            token = m.group(1)
            if re.fullmatch(r'[a-z0-9_-]+', token, re.I):
                urls.add(f"https://boards.greenhouse.io/{token}")

    return urls


# ── Phase 3: API probe ────────────────────────────────────────────────────────

def _probe(cfg, timeout=10):
    """
    Quick API probe to verify config and get job count.
    Returns (job_count, error_str).  job_count = -1 means "reachable but uncountable".
    """
    mode = cfg.get("mode", "")
    try:
        if mode == "greenhouse":
            token = cfg.get("board_token", "")
            bases = (
                ["https://boards-api.eu.greenhouse.io", "https://boards-api.greenhouse.io"]
                if cfg.get("region") == "eu"
                else ["https://boards-api.greenhouse.io", "https://boards-api.eu.greenhouse.io"]
            )
            for base in bases:
                r = requests.get(f"{base}/v1/boards/{token}/jobs?content=false",
                                 headers=HEADERS, timeout=timeout)
                if r.status_code == 200:
                    if cfg.get("region") != "eu" and "eu.greenhouse" in base:
                        cfg["region"] = "eu"
                    return len(r.json().get("jobs", [])), None
                if r.status_code != 404:
                    r.raise_for_status()
            return 0, "board not found on greenhouse.io or eu.greenhouse.io"

        elif mode == "lever":
            slug = cfg.get("lever_slug", "")
            r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json",
                             headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return (len(data) if isinstance(data, list) else 0), None

        elif mode == "ashby":
            slug = cfg.get("ashby_slug", "")
            payload = {
                "operationName": "ApiJobBoardWithTeams",
                "variables": {"organizationHostedJobsPageName": slug},
                "query": (
                    "query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {"
                    " jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName)"
                    " { jobPostings { id } } }"
                ),
            }
            r = requests.post("https://jobs.ashbyhq.com/api/non-user-graphql",
                              json=payload,
                              headers={**HEADERS, "Content-Type": "application/json"},
                              timeout=timeout)
            r.raise_for_status()
            data = r.json()
            if data.get("errors"):
                return 0, data["errors"][0].get("message", "Ashby error")
            board = ((data.get("data") or {}).get("jobBoard")) or {}
            return len(board.get("jobPostings") or []), None

        elif mode == "workday":
            list_url = cfg.get("list_url", "")
            m = re.match(r'https?://([^./?#]+)\.(wd\d+\.myworkdayjobs\.com)/?([^/?#]*)', list_url)
            if m:
                tenant, wd_domain, board = m.group(1), m.group(2), m.group(3) or m.group(1)
                api_url = f"https://{tenant}.{wd_domain}/wday/cxs/{tenant}/{board}/jobs"
            else:
                m2 = re.match(r'https?://(wd\d+\.myworkdaysite\.com)/recruiting/([^/]+)/([^/?#]+)', list_url)
                if m2:
                    wd, tenant, board = m2.group(1), m2.group(2), m2.group(3)
                    api_url = f"https://{wd}/wday/cxs/{tenant}/{board}/jobs"
                else:
                    return -1, "could not parse Workday URL for probing"
            r = requests.post(api_url,
                              json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                              headers={**HEADERS, "Content-Type": "application/json", "Accept": "application/json"},
                              timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return int(data.get("total") or len(data.get("jobPostings", []))), None

        elif mode == "smartrecruiters":
            slug = cfg.get("smartrecruiters_company", "")
            r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
                             headers={**HEADERS, "Accept": "application/json"}, timeout=timeout)
            r.raise_for_status()
            return int(r.json().get("totalFound", 0)), None

        elif mode == "bamboohr":
            slug = cfg.get("bamboohr_slug", "")
            r = requests.get(f"https://{slug}.bamboohr.com/careers/list",
                             headers={**HEADERS, "Accept": "application/json"}, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            result = data.get("result", []) if isinstance(data, dict) else []
            return len(result if isinstance(result, list) else []), None

        elif mode == "workable":
            slug = cfg.get("workable_slug", "")
            r = requests.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}",
                             params={"details": "false"},
                             headers={**HEADERS, "Accept": "application/json"}, timeout=timeout)
            r.raise_for_status()
            return len(r.json().get("jobs", [])), None

        elif mode == "pinpoint":
            list_url = cfg.get("list_url", "")
            r = requests.get(list_url, headers={**HEADERS, "Accept": "application/json"}, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            items = data.get("data") if isinstance(data, dict) else data
            return (len(items) if isinstance(items, list) else -1), None

        elif mode == "oraclecloud":
            list_url = cfg.get("list_url", "")
            site = cfg.get("site", "CX_1")
            host = urlparse(list_url).netloc
            r = requests.get(
                f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
                params={
                    "finder": f"findReqs;siteNumber={site},limit=1,offset=0,sortBy=POSTING_DATES_DESC",
                    "expand": "requisitionList",
                },
                headers={**HEADERS, "Accept": "application/json"}, timeout=timeout,
            )
            r.raise_for_status()
            items = r.json().get("items") or []
            total = max((int(item.get("TotalJobsCount") or 0) for item in items), default=0)
            return total, None

        elif mode == "eightfold":
            list_url = cfg.get("list_url", "")
            host = urlparse(list_url).netloc
            domain = cfg.get("domain") or re.sub(r'\.eightfold\.ai$', '.com', host)
            r = requests.get(f"https://{host}/api/apply/v2/jobs",
                             params={"domain": domain, "start": 0, "num": 1},
                             headers={**HEADERS, "Accept": "application/json"}, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            cfg["domain"] = domain
            return int(data.get("count") or len(data.get("positions", []))), None

        elif mode == "talentbrew":
            list_url = cfg.get("list_url", "")
            r = requests.get(list_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            # Count from meta tag if available
            m = re.search(r'search-analytics-total-jobs[^"]*content="(\d+)"', r.text)
            return (int(m.group(1)) if m else -1), None

        elif mode == "jobsapi":
            api_url = cfg.get("api_url", "")
            r = requests.get(api_url, params={"limit": 1}, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            total = int(data.get("totalCount") or data.get("total") or 0)
            jobs = data.get("jobs") or []
            # Try to correct the job_url_prefix by testing both patterns
            if jobs and total > 0:
                j = jobs[0].get("data", jobs[0])
                req_id = str(j.get("req_id") or j.get("slug") or "")
                if req_id:
                    root = api_url.rsplit("/api/jobs", 1)[0]
                    for suffix in [f"/careers-home/jobs/{req_id}", f"/jobs/{req_id}"]:
                        tr = requests.get(root + suffix, headers=HEADERS, timeout=6, allow_redirects=True)
                        if tr.status_code == 200:
                            cfg["job_url_prefix"] = root + suffix.replace(req_id, "")
                            break
            return total or -1, None

        elif mode == "adp":
            list_url = cfg.get("list_url", "")
            r = requests.get(list_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            r.raise_for_status()
            return -1, None

        elif mode == "icims":
            slug = cfg.get("icims_slug", "")
            base = slug if slug.startswith("http") else f"https://careers-{slug}.icims.com"
            # Try sitemap — if IP-blocked or empty, report as unverified
            try:
                rs = requests.get(base + "/sitemap.xml",
                                  headers={**HEADERS, "Accept": "application/xml,*/*"},
                                  timeout=timeout)
                if rs.status_code == 200 and "xml" in rs.headers.get("Content-Type", "").lower():
                    job_urls = re.findall(r'<loc>[^<]*/jobs/[^<]+/job</loc>', rs.text)
                    return len(job_urls), None if job_urls else "sitemap found but no jobs"
            except Exception:
                pass
            # Fallback: search page
            try:
                rp = requests.get(base + "/jobs/search?ss=1&in_iframe=1",
                                  headers=HEADERS, timeout=timeout)
                if rp.status_code == 200 and len(rp.text) > 500:
                    return -1, None
            except Exception:
                pass
            return 0, "iCIMS site blocked or unreachable"

        else:
            # HTML-scraped modes: just verify the URL is reachable
            list_url = cfg.get("list_url", "")
            if list_url:
                r = requests.get(list_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
                r.raise_for_status()
                return -1, None

    except Exception as e:
        return 0, str(e)

    return -1, None


# ── Public entry point ────────────────────────────────────────────────────────

def detect_ats(careers_url, timeout=12):
    """
    Detect which ATS a company uses given their careers URL.

    Returns a list of result dicts (best match first):
      {
        "mode":      str,
        "config":    dict,   # ready-to-use scrape config
        "job_count": int,    # -1 = reachable but uncountable (HTML scraper)
        "verified":  bool,   # True if API probe succeeded
        "error":     str | None,
        "source":    "url" | "redirect" | "html",
      }
    """
    seen_keys  = set()
    seen_modes = set()   # only keep the best result per mode
    results    = []

    def _add(cfg, source):
        mode = cfg.get("mode", "")
        key  = (mode, str(sorted(cfg.items())))
        if key in seen_keys:
            return
        seen_keys.add(key)
        count, err = _probe(cfg, timeout=timeout)
        verified = err is None
        result = {
            "mode":      mode,
            "config":    dict(cfg),
            "job_count": count,
            "verified":  verified,
            "error":     err,
            "source":    source,
        }
        # For HTML-scraped modes (successfactors, icims, etc.) keep only the
        # first (best) result per mode — duplicates usually differ only in URL
        # path and the first one found via redirect/url is the most reliable.
        if mode in seen_modes and source == "html":
            return
        seen_modes.add(mode)
        results.append(result)

    # Phase 1: direct URL match
    if careers_url:
        cfg = _parse_url(careers_url)
        if cfg:
            _add(cfg, "url")
            if results and results[0]["verified"]:
                return results  # fast path

    # Phase 2: fetch + redirect chain + HTML scan
    # Build a list of URLs to try: provided URL + root domain (fallback for job-detail URLs
    # and infinite-redirect paths like Nintendo's /jobs → /jobs loop).
    parsed = urlparse(careers_url)
    root_url = f"{parsed.scheme}://{parsed.netloc}"
    urls_to_try = [careers_url]
    if parsed.path not in ("", "/"):
        urls_to_try.append(root_url)

    fetched_html = None
    fetched_url  = careers_url

    for attempt_url in urls_to_try:
        try:
            r = requests.get(attempt_url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            for resp in r.history:
                cfg = _parse_url(resp.url)
                if cfg:
                    _add(cfg, "redirect")
            cfg = _parse_url(r.url)
            if cfg:
                _add(cfg, "redirect")
            if "html" in r.headers.get("Content-Type", "") or r.text.lstrip().startswith('<'):
                fetched_html = r.text
                fetched_url  = r.url
            break  # succeeded — don't try root
        except Exception:
            continue  # redirect loop or error — try root URL next

    if fetched_html:
        for u in _urls_from_html(fetched_html, fetched_url):
            cfg = _parse_url(u)
            if cfg:
                _add(cfg, "html")

        # SuccessFactors fingerprint — CDN assets hint the page is a SF portal
        if "successfactors" not in {res["mode"] for res in results}:
            if re.search(r'rmkcdn\.successfactors\.com|sap-successfactors', fetched_html, re.I):
                _add({"mode": "successfactors", "list_url": fetched_url}, "html")

        # Phenom fingerprint — CDN (phenompeople.com) assets signal a Phenom career site
        if "phenom" not in {res["mode"] for res in results}:
            if re.search(r'cdn\.phenompeople\.com|assets\.phenompeople\.com', fetched_html, re.I):
                _add({"mode": "phenom", "list_url": fetched_url}, "html")

    results.sort(key=lambda x: (
        0 if x["verified"] else 1,
        0 if x["source"] == "url" else (1 if x["source"] == "redirect" else 2),
        -(x["job_count"] if x["job_count"] >= 0 else 0),
    ))
    return results
