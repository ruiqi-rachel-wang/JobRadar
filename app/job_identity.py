"""Stable job identity helpers shared by scanners and storage."""
import hashlib
import re


def normalize_text(value):
    if not value:
        return ""
    value = str(value).lower().strip()
    return re.sub(r"\s+", " ", value)


def make_job_hash(company_name, title, location=None, job_url=""):
    """
    Full stable hash for future storage or external integrations.

    Prefer URL when present because ATS URLs often contain requisition IDs;
    retain company/title/location as fallback context.
    """
    raw = "|".join([
        normalize_text(company_name),
        normalize_text(title),
        normalize_text(location),
        normalize_text(job_url),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_job_id(company_name, job_url, title):
    """
    Backward-compatible JobRadar ID.

    Existing scans and rows use this 20-character prefix, so keep it stable
    while centralizing the hashing logic in one module.
    """
    raw = f"{company_name}|{job_url}|{title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
