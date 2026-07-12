"""
ATS fetcher registry and job-enrichment pipeline.

fetch_jobs() is the single entry point called by scanner_service for every
company. It dispatches to the right ATS fetcher, optionally falls back to
Playwright, enriches every job with experience/skills/employment-type, and
filters by text include rules from the company config.

scan_type controls two fetcher-level behaviours:
  "fresh"  – stop_on_seen injected as True  (fetcher stops on seen pages)
             detail fetch defaults off when JOBRADAR_FAST_SCAN is enabled
             (the default), so all-company scans stay fast.
  "full"   – stop_on_seen forced to False    (fetcher scans all pages)
             detail fetch defaults off when JOBRADAR_FAST_SCAN is enabled;
             set JOBRADAR_FAST_SCAN=false for deep metadata backfills.
"""

import os
import re as _re
import threading as _threading
from statistics import median

from .utils import (
    _parse_posted,
    extract_employment_type as _extract_employment_type,
    extract_experience,
    extract_keywords,
    extract_skills,
    make_id,
    norm,
    normalize_location,
)
from .common import robots_allowed

_HTML_TAG_RE = _re.compile(r'<[^>]+>')
_ENRICH_TEXT_LIMIT = 40_000

def _strip_html(text):
    """Fast HTML tag stripper — avoids BeautifulSoup overhead in the hot path."""
    if not text or '<' not in text:
        return text
    return ' '.join(_HTML_TAG_RE.sub(' ', text).split())

def _bounded_enrich_text(text):
    """Keep regex-based enrichment from chewing through huge ATS descriptions."""
    if not text:
        return ""
    text = str(text)
    if len(text) <= _ENRICH_TEXT_LIMIT:
        return text
    head = text[: _ENRICH_TEXT_LIMIT // 2]
    tail = text[-_ENRICH_TEXT_LIMIT // 2 :]
    return head + " " + tail
from .ats.greenhouse      import fetch_greenhouse
from .ats.lever           import fetch_lever
from .ats.ashby           import fetch_ashby
from .ats.workday         import fetch_workday
from .ats.bamboohr        import fetch_bamboohr
from .ats.icims           import fetch_icims
from .ats.pinpoint        import fetch_pinpoint
from .ats.eightfold       import fetch_eightfold
from .ats.smartrecruiters import fetch_smartrecruiters
from .ats.oraclecloud     import fetch_oraclecloud, fetch_oracle
from .ats.avature         import fetch_avature
from .ats.successfactors  import fetch_successfactors
from .ats.workable        import fetch_workable
from .ats.jazzhr          import fetch_jazzhr
from .ats.jobvite         import fetch_jobvite
from .ats.rippling        import fetch_rippling
from .ats.talentbrew      import fetch_talentbrew
from .ats.careerpuck      import fetch_careerpuck
from .ats.paylocity       import fetch_paylocity
try:
    from .ats.playwright import (
        fetch_adp, fetch_brassring, fetch_phenom, fetch_playwright,
        fetch_salesforce, fetch_taleo, fetch_talemetry,
        _fetch_playwright_with_source,
    )
except Exception as _pw_err:
    import logging as _log
    _log.warning(f"Playwright unavailable ({_pw_err}) — browser-based fetchers disabled")
    def _pw_stub(*a, **k): return [], "playwright not available on this platform"
    fetch_adp = fetch_brassring = fetch_phenom = fetch_playwright = _pw_stub
    fetch_salesforce = fetch_taleo = fetch_talemetry = _pw_stub
    def _fetch_playwright_with_source(*a, **k): return [], "playwright not available"
from .ats.html     import fetch_html
from .ats.jobsapi  import fetch_jobsapi
from .ats.xml_feed import fetch_xml_feed

# Map mode name → fetcher function.
FETCHERS = {
    "greenhouse":      fetch_greenhouse,
    "lever":           fetch_lever,
    "ashby":           fetch_ashby,
    "workday":         fetch_workday,
    "bamboohr":        fetch_bamboohr,
    "icims":           fetch_icims,
    "pinpoint":        fetch_pinpoint,
    "eightfold":       fetch_eightfold,
    "smartrecruiters": fetch_smartrecruiters,
    "oraclecloud":     fetch_oraclecloud,
    "oracle":          fetch_oracle,
    "avature":         fetch_avature,
    "successfactors":  fetch_successfactors,
    "workable":        fetch_workable,
    "jazzhr":          fetch_jazzhr,
    "jobvite":         fetch_jobvite,
    "rippling":        fetch_rippling,
    "talentbrew":      fetch_talentbrew,
    "careerpuck":      fetch_careerpuck,
    "paylocity":       fetch_paylocity,
    "brassring":       fetch_brassring,
    "taleo":           fetch_taleo,
    "phenom":          fetch_phenom,
    "talemetry":       fetch_talemetry,
    "adp":             fetch_adp,
    "salesforce":      fetch_salesforce,
    "jobsapi":         fetch_jobsapi,
    "playwright":      fetch_playwright,
    "html":            fetch_html,
    "xml_feed":        fetch_xml_feed,
}

# Modes served by JSON APIs (not browser automation).
_API_MODES = set(FETCHERS) - {"html", "playwright"}

_NON_JOB_TITLES = {
    "claim offer", "log in", "login", "sign in", "signin",
    "learn more", "view all", "view all jobs", "search jobs",
}
_OPENINGS_SUMMARY_RE = _re.compile(r"\b\d[\d,]*\s+openings?$", _re.I)


def _remove_non_jobs(jobs):
    jobs[:] = [
        job for job in jobs
        if (
            (job.get("title") or "").strip().lower() not in _NON_JOB_TITLES
            and not _OPENINGS_SUMMARY_RE.search((job.get("title") or "").strip())
            and (job.get("title") or "").strip().lower() != "[email protected]"
            and "/cdn-cgi/l/email-protection" not in (job.get("url") or "").lower()
        )
    ]


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_jobs(company, seen, scan_type="fresh"):
    """Fetch, enrich, and filter jobs for one company.

    Args:
        company:   Company config dict (from companies.json).
        seen:      Dict of already-known job IDs → metadata.
        scan_type: "fresh" or "full" – controls stop-early and detail-fetch.

    Returns:
        (jobs, error_string | None)
    """
    scrape = _apply_scan_defaults(company.get("scrape") or {}, scan_type)
    mode   = scrape.get("mode", "html")
    list_url = scrape.get("list_url") or scrape.get("url") or ""
    if list_url and not robots_allowed(list_url):
        return [], "blocked by robots.txt"

    if mode == "multi":
        jobs, err = _fetch_multi(scrape, seen, company["name"])
        _enrich(jobs or [], seen, company["name"], scan_type=scan_type)
        _remove_non_jobs(jobs or [])
        _apply_text_filter(jobs or [], scrape)
        return jobs or [], err

    fn = FETCHERS.get(mode)
    if not fn:
        return [], f"unknown mode: {mode}"

    jobs, err = fn(scrape, seen, company["name"])

    # Playwright fallback: if an API fetcher fails and the config has a URL
    # with a prefix/include filter, try scraping the page directly.
    if (err or not jobs) and _should_try_playwright_fallback(mode, scrape):
        fallback, fb_err = _fetch_playwright_with_source(scrape, seen, company["name"], mode)
        if fallback or not jobs:
            jobs, err = fallback, fb_err

    _enrich(jobs or [], seen, company["name"], scan_type=scan_type)
    _remove_non_jobs(jobs or [])
    _apply_text_filter(jobs or [], scrape)
    return jobs or [], err


# ── Private helpers ───────────────────────────────────────────────────────────

def _apply_scan_defaults(scrape, scan_type):
    """Return a copy of scrape with scan-mode defaults injected."""
    scrape = dict(scrape)  # don't mutate the original config
    fast_scan = os.environ.get("JOBRADAR_FAST_SCAN", "true").lower() in {"1", "true", "yes", "on"}
    if scan_type != "full":
        # stop_on_seen is only useful for paginated API sources (workday, greenhouse, etc.).
        # HTML companies scrape a single page; stopping mid-page on the first known
        # job causes the fetcher to return 0 results instead of the full listing.
        if scrape.get("mode", "html") != "html":
            scrape.setdefault("stop_on_seen", True)
        scrape.setdefault("fetch_posted_details",  False)
        scrape.setdefault("fetch_experience_details", not fast_scan)
        scrape.setdefault("fetch_new_experience_only", True)
    else:  # full
        # Always override stop_on_seen so a full scan really is complete.
        scrape["stop_on_seen"] = False
        scrape.setdefault("fetch_posted_details", not fast_scan)
        scrape.setdefault("fetch_experience_details", not fast_scan)
        scrape.setdefault("fetch_new_experience_only", False)
        scrape.setdefault("refresh_experience", not fast_scan)
    return scrape


def _should_try_playwright_fallback(mode, scrape):
    """True when an API fetcher has failed and Playwright may recover it."""
    return (
        mode not in {"playwright", "html"}
        and scrape.get("allow_browser_fallback") is not False
        and scrape.get("list_url")
        and (scrape.get("prefix") or scrape.get("include"))
    )


def _fetch_multi(scrape, seen, company_name):
    """Fetch one company from multiple public career sources."""
    sources = scrape.get("sources") or []
    jobs_by_id = {}
    errors = []
    for source in sources:
        source_scrape = {
            **{k: v for k, v in scrape.items() if k in {
                "stop_on_seen", "fetch_posted_details", "fetch_experience_details",
                "fetch_new_experience_only", "refresh_experience", "sort",
            }},
            **(source or {}),
        }
        mode = source_scrape.get("mode", "html")
        fn = FETCHERS.get(mode)
        if not fn:
            errors.append(f"unknown mode: {mode}")
            continue
        try:
            source_jobs, err = fn(source_scrape, seen, company_name)
        except Exception as exc:
            source_jobs, err = [], str(exc)
        if (err or not source_jobs) and _should_try_playwright_fallback(mode, source_scrape):
            fallback, fb_err = _fetch_playwright_with_source(source_scrape, seen, company_name, mode)
            if fallback or not source_jobs:
                source_jobs, err = fallback, fb_err
        if err:
            errors.append(err)
        for job in source_jobs or []:
            jid = make_id(company_name, job.get("url", ""), job.get("title", ""))
            jobs_by_id[jid] = job
    if jobs_by_id:
        return list(jobs_by_id.values()), None
    return [], "; ".join(errors) if errors else "no jobs found"


def _enrich(jobs, seen, company_name, scan_type="fresh"):
    """Normalise location/date and extract experience, skills, employment type."""
    is_full = scan_type == "full"
    for j in jobs:
        j["location"] = normalize_location(j.get("location", ""))
        j["posted"]   = _parse_posted(j.get("posted", ""))

        # Strip HTML tags so regex-based extractors see plain text.
        raw_desc = j.get("description", "") or ""
        if raw_desc and '<' in raw_desc:
            raw_desc = _strip_html(raw_desc)
            j["description"] = raw_desc

        jid      = make_id(company_name, j.get("url", ""), j.get("title", ""))
        existing = seen.get(jid) if isinstance(seen, dict) else None
        desc     = _bounded_enrich_text(raw_desc or j.get("description", ""))

        # Reuse cached fields when we have no description (avoids re-extraction).
        # In a full scan, do not preserve title-only estimates; try company
        # peers first, then recompute title as the final fallback.
        existing_source = (existing.get("exp_source") or "") if isinstance(existing, dict) else ""
        can_reuse_existing = (
            isinstance(existing, dict)
            and existing.get("exp_label")
            and (
                not desc  # no new text to compute from
                or (not is_full and existing_source in {"text", "company_peer"})  # fresh: trust stored result
            )
            and (not is_full or existing_source in {"text", "company_peer"})
        )
        if can_reuse_existing:
            j.setdefault("exp_min",    existing.get("exp_min"))
            j.setdefault("exp_max",    existing.get("exp_max"))
            j.setdefault("exp_label",  existing.get("exp_label", ""))
            j.setdefault("exp_source", existing.get("exp_source", ""))
            j.setdefault("skills",     existing.get("skills") or [])
        else:
            exp = extract_experience(j.get("title", ""), desc, allow_title_fallback=False)
            if not exp.get("label"):
                exp = _peer_experience_estimate(company_name, j.get("title", ""), seen)
            if desc and not exp.get("label"):
                exp = extract_experience(j.get("title", ""), "", allow_title_fallback=True)
            j["exp_min"]    = exp.get("min")
            j["exp_max"]    = exp.get("max")
            j["exp_label"]  = exp.get("label", "")
            j["exp_source"] = exp.get("source", "")
            if not j.get("skills"):
                j["skills"] = extract_skills(j.get("title", ""), desc)

        if not j.get("employment_type"):
            j["employment_type"] = _extract_employment_type(j.get("title", ""), desc)
        if not j.get("keywords"):
            j["keywords"] = extract_keywords(j.get("title", ""), desc, j.get("skills") or [])


_TITLE_LEVEL_WORDS = {
    "intern", "internship", "co", "op", "coop", "junior", "jr", "senior", "sr",
    "staff", "principal", "lead", "manager", "director", "associate", "ii", "iii",
    "iv", "v", "l1", "l2", "l3", "l4", "l5", "l6", "l7", "i",
}
_TITLE_STOP_WORDS = {
    "the", "and", "or", "of", "for", "to", "with", "in", "on", "remote",
    "hybrid", "temporary", "contract", "full", "time", "part",
}


def _title_core_tokens(title):
    raw = _re.sub(r'[^a-z0-9+#.]+', ' ', (title or '').lower())
    tokens = []
    for token in raw.split():
        token = token.strip(".")
        if len(token) < 2:
            continue
        if token in _TITLE_LEVEL_WORDS or token in _TITLE_STOP_WORDS:
            continue
        tokens.append(token)
    return set(tokens)


# Peer index: company (lowercase) → [(title_tokens, job_dict), …]. Built in a
# single pass over the whole seen dict, then reused by every company in the
# scan — the old per-company scan re-walked all ~500K seen entries 1,300+ times.
_peer_cache: dict = {}
_peer_cache_seen_id: int = 0
_peer_cache_lock = _threading.Lock()

def _build_peer_index(seen):
    """Group peer-eligible jobs by company, pre-tokenizing titles once."""
    for _attempt in range(5):
        try:
            snap = list(seen.values())
            break
        except RuntimeError:  # dict mutated mid-copy by a scan thread
            import time as _t; _t.sleep(0.005 * (_attempt + 1))
    else:
        snap = []
    index = {}
    for item in snap:
        if not isinstance(item, dict):
            continue
        if (item.get("exp_source") or "") not in {"text", "company_peer"}:
            continue
        if item.get("exp_min") is None and item.get("exp_max") is None:
            continue
        tokens = _title_core_tokens(item.get("title", ""))
        if not tokens:
            continue
        index.setdefault((item.get("company") or "").lower(), []).append((tokens, item))
    return index

def _get_company_peers(company_name, seen):
    """Return [(title_tokens, job)] peers for company_name from the cached index."""
    global _peer_cache, _peer_cache_seen_id
    with _peer_cache_lock:
        sid = id(seen)
        if sid != _peer_cache_seen_id:
            _peer_cache = _build_peer_index(seen)
            _peer_cache_seen_id = sid
        return _peer_cache.get((company_name or "").lower(), [])


def _peer_experience_estimate(company_name, title, seen):
    """Estimate from same-company jobs with similar titles and non-title evidence."""
    if not isinstance(seen, dict):
        return {"min": None, "max": None, "label": "", "source": ""}
    query_tokens = _title_core_tokens(title)
    if not query_tokens:
        return {"min": None, "max": None, "label": "", "source": ""}

    candidates = []
    for peer_tokens, item in _get_company_peers(company_name, seen):
        overlap = len(query_tokens & peer_tokens)
        score = overlap / max(len(query_tokens | peer_tokens), 1)
        if overlap >= 2 and score >= 0.35:
            candidates.append((score, item))

    if not candidates:
        return {"min": None, "max": None, "label": "", "source": ""}

    candidates.sort(key=lambda row: row[0], reverse=True)
    strongest = [item for score, item in candidates if score >= candidates[0][0] - 0.1][:7]
    mins = [float(item["exp_min"]) for item in strongest if item.get("exp_min") is not None]
    maxes = [float(item["exp_max"]) for item in strongest if item.get("exp_max") is not None]
    if not mins:
        return {"min": None, "max": None, "label": "", "source": ""}

    def _fmt_year(value):
        if value is None:
            return ""
        return str(int(value)) if float(value).is_integer() else str(value).rstrip("0").rstrip(".")

    lo = median(mins)
    hi = median(maxes) if maxes and len(maxes) >= max(1, len(strongest) // 2) else None
    if hi is not None and hi < lo:
        hi = lo
    label = f"{_fmt_year(lo)}-{_fmt_year(hi)} years" if hi is not None else f"{_fmt_year(lo)}+ years"
    return {"min": lo, "max": hi, "label": label, "source": "company_peer"}


def _apply_text_filter(jobs, scrape):
    """Remove jobs whose title/location/URL don't match required keywords."""
    include_text = scrape.get("text_include")
    if not include_text or not jobs:
        return
    needles = include_text if isinstance(include_text, list) else [include_text]
    needles = [str(n).lower() for n in needles if str(n).strip()]
    if not needles:
        return
    haystack = lambda j: " ".join([j.get("title",""), j.get("location",""), j.get("url","")]).lower()
    jobs[:] = [j for j in jobs if any(n in haystack(j) for n in needles)]
