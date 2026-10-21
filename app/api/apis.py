"""
ATS fetcher registry and job-enrichment pipeline.

fetch_jobs() is the single entry point called by scanner_service for every
company. It dispatches to the right ATS fetcher, optionally falls back to
Playwright, enriches every job with experience/skills/employment-type, and
filters by text include rules from the company config.

scan_type controls two fetcher-level behaviours:
  "fresh"  – stop_on_seen injected as True  (fetcher stops on seen pages)
             fetches bounded detail pages for missing descriptions/experience
             so new jobs expose their stated requirements.
  "full"   – stop_on_seen forced to False    (fetcher scans all pages)
             detail fetch defaults off when JOBRADAR_FAST_SCAN is enabled;
             set JOBRADAR_FAST_SCAN=false for deep metadata backfills.
"""

import concurrent.futures
import os
import re as _re

from .utils import (
    _parse_posted,
    extract_employment_type as _extract_employment_type,
    extract_experience,
    extract_keywords,
    extract_skills,
    make_id,
    norm,
    normalize_datetime_utc,
    normalize_description,
    normalize_employment_type,
    normalize_locations,
    normalize_remote_mode,
)
from .common import robots_allowed

_ENRICH_TEXT_LIMIT = 40_000

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
from .ats.paradox         import fetch_paradox
from .ats.cornerstone     import fetch_cornerstone
from .ats.darwinbox       import fetch_darwinbox
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
from .ats.equipmentshare import fetch_equipmentshare
from .ats.accenture import fetch_accenture
from .ats.barnes_noble import fetch_barnes_noble
from .ats.jobsyn import fetch_jobsyn
from .ats.yum import fetch_yum
from .ats.xml_feed import fetch_xml_feed
from .ats.comeet import fetch_comeet
from .ats.mckinsey import fetch_mckinsey
from .ats.mobileye import fetch_mobileye
from .ats.kula import fetch_kula
from .ats.tiktok import fetch_tiktok
from .ats.gem import fetch_gem
from .ats.dover import fetch_dover
from .ats.careyaya import fetch_careyaya
from .ats.remote_ats import fetch_remote_ats

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
    "paradox":         fetch_paradox,
    "cornerstone":     fetch_cornerstone,
    "darwinbox":       fetch_darwinbox,
    "brassring":       fetch_brassring,
    "taleo":           fetch_taleo,
    "phenom":          fetch_phenom,
    "talemetry":       fetch_talemetry,
    "adp":             fetch_adp,
    "salesforce":      fetch_salesforce,
    "jobsapi":         fetch_jobsapi,
    "equipmentshare":  fetch_equipmentshare,
    "accenture":       fetch_accenture,
    "barnes_noble":    fetch_barnes_noble,
    "jobsyn":          fetch_jobsyn,
    "yum":             fetch_yum,
    "playwright":      fetch_playwright,
    "html":            fetch_html,
    "xml_feed":        fetch_xml_feed,
    "comeet":          fetch_comeet,
    "mckinsey":        fetch_mckinsey,
    "mobileye":        fetch_mobileye,
    "kula":            fetch_kula,
    "tiktok":          fetch_tiktok,
    "gem":             fetch_gem,
    "dover":           fetch_dover,
    "careyaya":        fetch_careyaya,
    "remote_ats":      fetch_remote_ats,
}

# Modes served by JSON APIs (not browser automation).
_API_MODES = set(FETCHERS) - {"html", "playwright"}
_EARLY_STOP_MODES = {"workday", "jobsapi", "playwright"}

_NON_JOB_TITLES = {
    "apply", "apply now",
    "claim offer", "log in", "login", "sign in", "signin",
    "learn more", "view all", "view all jobs", "search jobs",
}
_OPENINGS_SUMMARY_RE = _re.compile(r"\b\d[\d,]*\s+openings?$", _re.I)
_NON_JOB_POSTING_RE = _re.compile(
    r"(?:"
    r"\b(?:join|submit to)\s+(?:our\s+)?(?:general\s+)?talent "
    r"(?:community|network|pool)\b|"
    r"\bgeneral talent\b|"
    r"\b(?:general|spontaneous|unsolicited) application\b|"
    r"\bdon'?t see (?:a|the) (?:job|role|position)\b|"
    r"\bfuture opportunit(?:y|ies)\b"
    r")",
    _re.I,
)


def _remove_non_jobs(jobs):
    jobs[:] = [
        job for job in jobs
        if (
            (job.get("title") or "").strip().lower() not in _NON_JOB_TITLES
            and not _OPENINGS_SUMMARY_RE.search((job.get("title") or "").strip())
            and not _NON_JOB_POSTING_RE.search((job.get("title") or "").strip())
            and (job.get("title") or "").strip().lower() != "[email protected]"
            and "/cdn-cgi/l/email-protection" not in (job.get("url") or "").lower()
        )
    ]


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_jobs(company, seen, scan_type="fresh", deadline=None):
    """Fetch, enrich, and filter jobs for one company.

    Args:
        company:   Company config dict (from companies.json).
        seen:      Dict of already-known job IDs → metadata.
        scan_type: "fresh" or "full" – controls stop-early and detail-fetch.

    Returns:
        (jobs, error_string | None)
    """
    scrape = _apply_scan_defaults(company.get("scrape") or {}, scan_type)
    if deadline is not None:
        scrape["_deadline_monotonic"] = float(deadline)
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
        # Only inject early-stop for adapters that both consume the flag and
        # preserve newest-first page order. Other adapters must scan to their
        # authoritative end so fresh scans cannot silently miss openings.
        if scrape.get("mode", "html") in _EARLY_STOP_MODES:
            scrape.setdefault("stop_on_seen", True)
        scrape.setdefault("fetch_posted_details",  False)
        # Source cards frequently omit the requirements section entirely.
        # Fetch details for missing descriptions/experience; adapters skip
        # records that already have a usable stored description.
        scrape.setdefault("fetch_experience_details", True)
        scrape.setdefault("fetch_new_experience_only", False)
        detail_limit = int(os.environ.get("JOBRADAR_FRESH_DETAIL_LIMIT", "100"))
        scrape.setdefault("max_experience_details", detail_limit)
        scrape.setdefault("max_desc_details", detail_limit)
        scrape.setdefault("max_posted_details", detail_limit)
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
                "max_experience_details", "max_desc_details", "max_posted_details",
                "_deadline_monotonic",
            }},
            **(source or {}),
        }
        mode = source_scrape.get("mode", "html")
        deadline = source_scrape.get("_deadline_monotonic")
        if deadline and __import__("time").monotonic() >= float(deadline):
            errors.append("company scan deadline reached (partial results)")
            break
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
            jid = make_id(
                company_name,
                job.get("url", ""),
                job.get("title", ""),
                job.get("source", mode),
                job.get("ats_id", ""),
            )
            jobs_by_id[jid] = job
    if jobs_by_id:
        return list(jobs_by_id.values()), None
    return [], "; ".join(errors) if errors else "no jobs found"


def _enrich(jobs, seen, company_name, scan_type="fresh"):
    """Create canonical fields while preserving every source value separately."""
    def _description_input(job):
        return (
            job.get("description_raw")
            if "description_raw" in job
            else job.get("description")
        )

    # HTML parsing is the most expensive normalization step on very large
    # boards. Process it in small bounded batches so we gain parallelism
    # without materializing an unbounded queue or duplicating normalization.
    descriptions = [_description_input(job) for job in jobs]
    canonical_descriptions = [None] * len(jobs)
    batch_size = 128
    workers = min(4, max(1, len(jobs)))
    if len(jobs) >= 32:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for start in range(0, len(jobs), batch_size):
                batch = descriptions[start:start + batch_size]
                canonical_descriptions[start:start + len(batch)] = list(
                    executor.map(normalize_description, batch)
                )
    else:
        canonical_descriptions = [
            normalize_description(value) for value in descriptions
        ]

    for index, j in enumerate(jobs):
        source = j.get("source", "")
        ats_id = j.get("ats_id", "")

        raw_location = (
            j.get("location_raw")
            if "location_raw" in j
            else j.get("location")
        )
        raw_locations = (
            j.get("locations_raw")
            if "locations_raw" in j
            else j.get("locations") or raw_location
        )
        location_data = normalize_locations(
            raw_locations,
            remote_mode=j.get("remote_mode") or j.get("workplace_type"),
        )
        j["location_raw"] = raw_location or None
        j["locations"] = location_data["locations"]
        j["location"] = location_data["location"]
        j["country_iso"] = j.get("country_iso") or location_data["country_iso"]
        j["remote_mode"] = (
            normalize_remote_mode(
                j.get("remote_mode") or j.get("workplace_type"),
                str(raw_location or ""),
            )
            or location_data["remote_mode"]
        )

        posted_raw = (
            j.get("posted_raw")
            if "posted_raw" in j
            else j.get("posted_at") or j.get("posted")
        )
        posted_at = normalize_datetime_utc(j.get("posted_at") or posted_raw)
        j["posted_raw"] = posted_raw or None
        j["posted_at"] = posted_at
        j["posted"] = _parse_posted(posted_at or posted_raw) or None

        raw_desc = descriptions[index]
        canonical_desc = canonical_descriptions[index]
        j["description_raw"] = raw_desc or None
        j["description"] = canonical_desc

        raw_employment = (
            j.get("employment_type_raw")
            if "employment_type_raw" in j
            else j.get("employment_type")
        )
        j["employment_type_raw"] = raw_employment or None

        raw_data = dict(j.get("raw_data") or {})
        if raw_location is not None:
            raw_data.setdefault("location", raw_location)
        if posted_raw is not None:
            raw_data.setdefault("posted", posted_raw)
        if raw_employment is not None:
            raw_data.setdefault("employment_type", raw_employment)
        j["raw_data"] = raw_data or None

        jid = make_id(
            company_name,
            j.get("url", ""),
            j.get("title", ""),
            source,
            ats_id,
        )
        existing = None
        if isinstance(seen, dict):
            existing = seen.get(jid) or seen.get(
                make_id(
                    company_name,
                    j.get("url", ""),
                    j.get("title", ""),
                )
            )
        desc = _bounded_enrich_text(canonical_desc or "")

        # Reuse only experience extracted from the posting itself. Historical
        # title and company-peer estimates are deliberately discarded.
        existing_source = (existing.get("exp_source") or "") if isinstance(existing, dict) else ""
        provider_experience = (
            j.get("exp_source") == "provider"
            and (j.get("exp_min") is not None or j.get("exp_max") is not None)
        )
        can_reuse_existing = (
            isinstance(existing, dict)
            and existing.get("exp_label")
            and not desc
            and existing_source in {"text", "oracle_detail"}
        )
        if provider_experience:
            pass
        elif can_reuse_existing:
            j.setdefault("exp_min",    existing.get("exp_min"))
            j.setdefault("exp_max",    existing.get("exp_max"))
            j.setdefault("exp_label",  existing.get("exp_label"))
            j.setdefault("exp_source", existing.get("exp_source"))
            j.setdefault("skills",     existing.get("skills") or [])
        else:
            exp = extract_experience(j.get("title", ""), desc, allow_title_fallback=False)
            j["exp_min"]    = exp.get("min")
            j["exp_max"]    = exp.get("max")
            j["exp_label"]  = exp.get("label") or None
            j["exp_source"] = exp.get("source") or None
            if (
                j.get("description_is_detail")
                and j["exp_min"] is None
                and j["exp_max"] is None
                and j["exp_source"] == "text"
            ):
                j["exp_source"] = "oracle_detail"
            if not j.get("skills"):
                j["skills"] = extract_skills(j.get("title", ""), desc)

        j["employment_type"] = (
            normalize_employment_type(raw_employment)
            or _extract_employment_type(j.get("title", ""), desc)
        )
        if not j.get("keywords"):
            j["keywords"] = extract_keywords(j.get("title", ""), desc, j.get("skills") or [])

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
