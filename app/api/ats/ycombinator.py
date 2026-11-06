"""Employer-owned public listings on Y Combinator's startup jobs pages."""
import concurrent.futures
import json
import re
import threading
import time
from urllib.parse import quote, urljoin, urlparse

import pycountry
from bs4 import BeautifulSoup

from .base import HEADERS, requests, scan_deadline_exceeded
from ..utils import extract_experience, make_id, norm


ORIGIN = "https://www.ycombinator.com"
_REQUEST_SLOTS = threading.BoundedSemaphore(3)
_START_LOCK = threading.Lock()
_LAST_START = 0.0
_REQUEST_INTERVAL = 0.25


def _read_page(url, scrape, timeout):
    """Share a modest request budget across concurrently scanned employers."""
    global _LAST_START
    with _REQUEST_SLOTS:
        with _START_LOCK:
            if scan_deadline_exceeded(scrape):
                raise TimeoutError("YC scan deadline exceeded")
            delay = _REQUEST_INTERVAL - (time.monotonic() - _LAST_START)
            if delay > 0:
                time.sleep(delay)
            if scan_deadline_exceeded(scrape):
                raise TimeoutError("YC scan deadline exceeded")
            _LAST_START = time.monotonic()
        response = requests.get(url, headers=HEADERS, timeout=timeout)
        response.raise_for_status()
        return _page_props(response.text)


def _key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _page_props(body):
    node = BeautifulSoup(body, "lxml").find(attrs={"data-page": True})
    if not node:
        raise ValueError("YC did not return a readable public jobs page")
    data = json.loads(node["data-page"])
    props = data.get("props")
    if not isinstance(props, dict):
        raise ValueError("YC page has no job properties")
    return props


def _verify_employer(props, slug, company, expected_id=None):
    employer = props.get("company") or {}
    if employer.get("slug") != slug:
        raise ValueError("YC employer slug differs from configured employer")
    if expected_id is not None:
        if str(employer.get("id")) != str(expected_id):
            raise ValueError("YC employer id differs from configured employer")
    elif _key(employer.get("name")) != _key(company):
        raise ValueError("YC employer name differs from configured employer")
    return employer


def _locations(value):
    """Split advertised offices without borrowing the company's headquarters."""
    output = []
    for part in re.split(r"\s+/\s+(?![^()]*\))", norm(value)):
        remote = re.fullmatch(r"Remote\s*\(([^)]+)\)", part, re.I)
        if remote:
            for country in re.split(r"[;,]", remote[1]):
                country = country.strip()
                known = pycountry.countries.get(alpha_2=country.upper()) if len(country) == 2 else None
                output.append("Remote, " + (known.name if known else country))
        elif part:
            output.append(part)
    return list(dict.fromkeys(output))


def fetch_ycombinator(scrape, seen, company):
    slug = str(scrape.get("yc_slug") or "").strip()
    if not re.fullmatch(r"[\w-]+", slug):
        return [], "missing or invalid yc_slug"
    listing_url = f"{ORIGIN}/companies/{quote(slug, safe='')}/jobs"
    try:
        props = _read_page(listing_url, scrape, 25)
        employer = _verify_employer(props, slug, company, scrape.get("yc_company_id"))
        postings = props.get("jobPostings")
        if not isinstance(postings, list):
            raise ValueError("YC did not return an explicit jobPostings list")
    except Exception as exc:
        return [], str(exc)

    jobs, targets, errors, emitted = [], [], [], set()
    for posting in postings:
        if not isinstance(posting, dict):
            errors.append("invalid YC posting record")
            continue
        if posting.get("isIncomplete"):
            continue
        job_id = str(posting.get("id") or "")
        title = norm(posting.get("title") or "")
        url = urljoin(ORIGIN, posting.get("url") or "")
        parsed = urlparse(url)
        if (not job_id or not title or parsed.scheme != "https" or parsed.hostname != "www.ycombinator.com"
                or not re.fullmatch(rf"/companies/{re.escape(slug)}/jobs/[A-Za-z0-9]+-[^/]+/?", parsed.path)
                or (posting.get("companyName") and _key(posting["companyName"]) != _key(employer.get("name")))
                or (posting.get("companyUrl") and urlparse(posting["companyUrl"]).path.rstrip("/") != f"/companies/{slug}")):
            errors.append("YC posting identity or public job URL could not be verified")
            continue
        if job_id in emitted:
            continue
        emitted.add(job_id)
        locations = _locations(posting.get("location") or "")
        exp = extract_experience(text=posting.get("minExperience") or "")
        job = {
            "title": title, "url": url, "ats_id": job_id,
            "source": "ycombinator", "apply_url": posting.get("applyUrl"),
            "location": "; ".join(locations),
            "location_raw": posting.get("location") or None,
            "locations_raw": locations,
            "remote_mode": "remote" if re.search(r"\bremote\b", posting.get("location") or "", re.I) else None,
            "employment_type": posting.get("type"), "employment_type_raw": posting.get("type"),
            "department": posting.get("prettyRole"), "skills": posting.get("skills") or [],
            "raw_data": {"yc_company_id": employer.get("id"), "employer_name": employer.get("name"),
                         "experience": posting.get("minExperience"),
                         "created_relative": posting.get("createdAt"), "last_active_relative": posting.get("lastActive")},
        }
        # Relative activity strings are not exact publication dates.
        if exp.get("min") is not None or exp.get("max") is not None:
            job.update(exp_min=exp["min"], exp_max=exp["max"], exp_label=exp["label"], exp_source="provider")
        jobs.append(job)
        existing = ((seen.get(make_id(company, url, title, "ycombinator", job_id))
                     or seen.get(make_id(company, url, title))) if isinstance(seen, dict) else None)
        if scrape.get("fetch_experience_details", True) and not (existing or {}).get("has_description"):
            targets.append(job)

    def fetch_detail(job):
        if scan_deadline_exceeded(scrape):
            return job, None
        try:
            values = _read_page(job["url"], scrape, 20)
            _verify_employer(values, slug, company, scrape.get("yc_company_id"))
            source = values.get("job") or {}
            if str(source.get("id")) != job["ats_id"]:
                return job, None
            return job, source.get("description")
        except Exception:
            return job, None

    if targets:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, int(scrape.get("detail_workers") or 2)))) as pool:
            for job, description in pool.map(fetch_detail, targets):
                if description:
                    job.update(description=description, description_raw=description)
    return jobs, "; ".join(sorted(set(errors))) or None
