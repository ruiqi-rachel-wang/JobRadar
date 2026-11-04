import concurrent.futures
import json
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests as _real_requests
from bs4 import BeautifulSoup

from ..common import BASE_DIR, HEADERS, REQUESTS_VERIFY_SSL, http_session


class _SessionRequests:
    """Drop-in for the `requests` module that routes get/post through a
    per-thread Session, so repeated calls to the same host reuse the TLS
    connection instead of handshaking every time. Anything else
    (exceptions, codes, …) falls through to the real module."""

    @staticmethod
    def get(url, **kwargs):
        return http_session().get(url, **kwargs)

    @staticmethod
    def post(url, **kwargs):
        return http_session().post(url, **kwargs)

    @staticmethod
    def head(url, **kwargs):
        return http_session().head(url, **kwargs)

    def __getattr__(self, name):
        return getattr(_real_requests, name)


# Shadows the real module for every fetcher doing `from .base import *`.
requests = _SessionRequests()
from ..utils import (
    _parse_posted, _posted_from_json, extract_location_from_text,
    extract_posted_from_html, extract_posted_from_text, get_param_title,
    has_include, make_id, make_paged_url, norm, normalize_description,
    normalize_location, _COUNTRY_CODES, _US_STATES,
)


def _jobposting_objects(data):
    """Visit JobPosting objects in JSON-LD arrays and @graph containers."""
    if isinstance(data, list):
        for item in data:
            yield from _jobposting_objects(item)
    elif isinstance(data, dict):
        types = data.get("@type") or []
        if isinstance(types, str):
            types = [types]
        if "JobPosting" in types:
            yield data
        yield from _jobposting_objects(data.get("@graph"))


def _jobposting_locations(posting):
    """All explicitly published job places, excluding employer headquarters."""
    values = posting.get("jobLocation") or []
    if not isinstance(values, list):
        values = [values]
    locations = []
    for value in values:
        if isinstance(value, str):
            raw = value
        elif isinstance(value, dict):
            address = value.get("address") or {}
            if isinstance(address, str):
                raw = address
            elif isinstance(address, dict):
                country = address.get("addressCountry") or ""
                if isinstance(country, dict):
                    country = country.get("name") or country.get("identifier") or ""
                # Country is an explicit schema field: DE is Germany here,
                # rather than the US state Delaware in an ambiguous label.
                country_code = str(country).upper()
                country = _COUNTRY_CODES.get(country_code, country)
                state = address.get("addressRegion") or ""
                if str(state).upper() in {"UNAVAILABLE", "N/A", "NA", "NONE", "NULL"}:
                    state = ""
                city = address.get("addressLocality") or ""
                # Some iCIMS feeds put several coded locations in one
                # addressLocality, e.g. VA-Hopewell\nUS-VA-Richmond.
                if (re.match(r'^[A-Z]{2}-(?:[A-Z]{2,3}|UNAVAILABLE)-', str(city))
                        or (country_code == 'US' and str(city).split('-', 1)[0] in _US_STATES)):
                    coded = str(city)
                    if not coded.startswith(country_code + "-"):
                        coded = country_code + "-" + coded
                    for part in re.split(r'\s+(?=[A-Z]{2}-(?:[A-Z]{2,3}|UNAVAILABLE)-)', coded):
                        pieces = part.split('-', 2)
                        if len(pieces) == 3:
                            code, region, locality = pieces
                            label = ", ".join(x for x in (locality, region if region != 'UNAVAILABLE' else '',
                                                          _COUNTRY_CODES.get(code, code)) if x)
                            if norm(label) not in locations:
                                locations.append(norm(label))
                    continue
                raw = ", ".join(str(part) for part in (city, state, country) if part)
                raw = raw or value.get("name") or ""
            else:
                raw = ""
        else:
            continue
        if raw and norm(raw) not in locations:
            locations.append(norm(raw))
    if not locations and str(posting.get('jobLocationType') or '').upper() == 'TELECOMMUTE':
        eligibility = posting.get('applicantLocationRequirements') or []
        if isinstance(eligibility, dict):
            eligibility = [eligibility]
        for area in eligibility:
            if isinstance(area, dict) and area.get('name'):
                label = 'Remote, ' + str(area['name'])
                if label not in locations:
                    locations.append(label)
    return locations


def fetch_detail_description(url, *, verify=True):
    """Extract structured job detail content from an official posting page.

    Several boards expose a complete description only on the detail URL.  Keep
    this deliberately conservative: prefer JobPosting JSON-LD, then Comeet's
    embedded POSITION_DATA, and finally clearly-labelled description elements.
    The returned value is source HTML/text and is normalized exactly once by
    the enrichment layer.
    """
    if not url:
        return {}
    try:
        response = requests.get(
            url,
            headers={**HEADERS, "Accept": "text/html,application/xhtml+xml"},
            timeout=25,
            verify=verify,
        )
        response.raise_for_status()
        html = response.text or ""
    except Exception:
        return {}

    return parse_detail_description(html)


def parse_detail_description(html):
    """Parse an official job document without issuing a second HTTP request."""

    soup = BeautifulSoup(html, "lxml")
    result = {}

    # PeopleAdmin and similar boards publish the work location in a labelled
    # table, separately from the description and the institution's address.
    # Read only explicit job-location fields; ignore footer/contact addresses.
    labelled_locations = []
    source_labels = []
    for row in soup.select('tr'):
        heading, value = row.find('th'), row.find('td')
        if not heading or not value:
            continue
        label = norm(heading.get_text(' ', strip=True)).lower().rstrip(':')
        if label not in {'location of position', 'position location', 'job location', 'job locations', 'work location'}:
            continue
        raw = value.get_text('\n', strip=True).strip()
        if not raw:
            continue
        # A city/state line may be followed by a ZIP and street address. Those
        # are source evidence, but must not become part of the state name.
        places = []
        for line in raw.splitlines():
            match = re.match(r'^([^,;]+?)(?:,\s*|\s+)(' + '|'.join(_US_STATES) + r')\b(?:\s+\d{5}(?:-\d{4})?)?\s*,?$', line)
            if match:
                places.append(match.group(1) + ', ' + match.group(2))
        for place in places or [norm(raw)]:
            if place not in labelled_locations:
                labelled_locations.append(place)
        source_labels.append(raw)
    if labelled_locations:
        result.update(locations_raw=labelled_locations,
                      location_raw='; '.join(source_labels),
                      location=normalize_location('; '.join(labelled_locations)))

    if not labelled_locations:
        for heading in soup.select('dt'):
            if norm(heading.get_text(' ', strip=True)).lower().rstrip(':') not in {
                    'location', 'locations', 'job location', 'job locations', 'work location'}:
                continue
            value = heading.find_next_sibling('dd')
            if value and norm(value.get_text(' ', strip=True)):
                labelled_locations.append(norm(value.get_text(' ', strip=True)))
        if labelled_locations:
            result.update(locations_raw=labelled_locations,
                          location_raw='; '.join(labelled_locations),
                          location=normalize_location('; '.join(labelled_locations)))

    # Avature publishes every advertised office in explicit location lists.
    # These containers are separate from employer contact/footer addresses.
    avature_locations = []
    for node in soup.select('.article__header--locations p.paragraph, .tf_locations .list__item'):
        place = norm(node.get_text(' ', strip=True))
        if place and place not in avature_locations:
            avature_locations.append(place)
    if avature_locations:
        result.update(locations_raw=avature_locations,
                      location_raw='; '.join(avature_locations),
                      location=normalize_location('; '.join(avature_locations)))

    # Ashby's official job document embeds a JSON jobPosting object, including
    # locationName even when its JSON-LD PostalAddress has no fields.
    app_data = re.search(r'window\.__appData\s*=\s*', html)
    if app_data:
        try:
            data, _ = json.JSONDecoder().raw_decode(html[app_data.end():].lstrip())
            posting = data.get('posting') or data.get('jobPosting') or {}
            label = posting.get('locationName')
            if label and not result.get('locations_raw'):
                result.update(locations_raw=[label],location_raw=label,
                              location=normalize_location(label))
        except (ValueError, TypeError, AttributeError):
            pass

    # Most modern ATS detail pages publish an authoritative JobPosting object.
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(node.string or node.get_text() or "")
        except Exception:
            continue
        candidates = list(_jobposting_objects(data))
        if not candidates:
            candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if not isinstance(item, dict):
                continue
            is_job_posting = item.get("@type") == "JobPosting"
            has_job_signals = any(
                item.get(key)
                for key in ("datePosted", "employmentType", "hiringOrganization", "jobLocation")
            )
            if is_job_posting or (item.get("description") and has_job_signals):
                if item.get("description"):
                    result["description"] = item["description"]
                if item.get("employmentType"):
                    result["employment_type_raw"] = item["employmentType"]
                if item.get("datePosted"):
                    result["posted_at"] = item["datePosted"]
                locations = _jobposting_locations(item)
                if locations:
                    result["locations_raw"] = locations
                    result["location_raw"] = "; ".join(locations)
                    result["location"] = normalize_location(result["location_raw"])
                if result.get("description"):
                    return result

    # Comeet renders the posting in an embedded JavaScript object even when the
    # visible page is hydrated by Angular.
    marker = re.search(r"(?:POSITION_DATA|positionData)\s*=\s*", html)
    if marker:
        try:
            data, _ = json.JSONDecoder().raw_decode(html[marker.end():].lstrip())
            details = ((data.get("custom_fields") or {}).get("details") or [])
            parts = []
            for field in details:
                if not isinstance(field, dict) or not field.get("value"):
                    continue
                name = norm(str(field.get("name") or ""))
                value = field["value"]
                parts.append(f"<h3>{name}</h3>\n{value}" if name else str(value))
            if parts:
                result["description"] = "\n\n".join(parts)
            if data.get("employment_type"):
                result["employment_type_raw"] = data["employment_type"]
            if data.get("department"):
                result["department"] = data["department"]
            if result:
                return result
        except Exception:
            pass

    # Last-resort selectors are constrained to description-like containers so
    # navigation/footer text never becomes a fake job description.
    for selector in (
        "[data-qa*='description']", ".job-description", ".jobDescription",
        ".job-details", ".description", ".jv-job-detail-description",
        "[class*='job-description']",
    ):
        node = soup.select_one(selector)
        if node:
            text = str(node)
            if len(norm(node.get_text(" ", strip=True))) >= 100:
                result["description"] = text
                return result
    return result


def scan_deadline_exceeded(scrape):
    """Cooperative deadline shared by paginated/detail-heavy adapters."""
    deadline = scrape.get("_deadline_monotonic") if isinstance(scrape, dict) else None
    return bool(deadline and time.monotonic() >= float(deadline))

# ATS modules intentionally use `from .base import *` to keep individual source
# files compact. Export underscore helpers explicitly; Python star imports skip
# them unless __all__ is defined.
__all__ = [name for name in globals() if not name.startswith("__")]
