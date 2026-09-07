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
    normalize_location,
)


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

    soup = BeautifulSoup(html, "lxml")
    result = {}

    # Most modern ATS detail pages publish an authoritative JobPosting object.
    for node in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(node.string or node.get_text() or "")
        except Exception:
            continue
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
