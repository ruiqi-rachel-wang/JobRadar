"""Stable job identity helpers shared by scanners and storage."""
import hashlib
import re


def normalize_text(value):
    if not value:
        return ""
    value = str(value).lower().strip()
    return re.sub(r"\s+", " ", value)


def _identity_job_url(job_url):
    """Normalize equivalent ATS display/API URLs for stable job identity."""
    value = str(job_url or "")
    public_sr = re.match(
        r"https?://jobs\.smartrecruiters\.com/([^/?#]+)/([^/?#]+)",
        value,
        re.I,
    )
    if public_sr:
        company_slug = public_sr.group(1)
        posting_id = public_sr.group(2).split("-", 1)[0]
        if posting_id:
            return (
                "https://api.smartrecruiters.com/v1/companies/"
                f"{company_slug}/postings/{posting_id}"
            )
    return value


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


def make_job_id(company_name, job_url, title, source="", ats_id=""):
    """
    Stable JobRadar ID.

    Prefer the provider's immutable posting id. Adapters without one retain
    the historical company+URL+title hash so existing databases do not report
    every old job as newly discovered after this upgrade.
    """
    stable_ats_id = normalize_text(ats_id)
    stable_source = normalize_text(source)
    normalized_url = _identity_job_url(job_url)
    if stable_ats_id:
        raw = f"ats|{stable_source}|{normalize_text(company_name)}|{stable_ats_id}"
    elif normalized_url:
        # Preserve the historical hash for adapters that have not yet exposed
        # an ATS id, avoiding a one-time "every job is new" migration.
        raw = f"{company_name}|{normalized_url}|{title}"
    else:
        raw = f"legacy|{normalize_text(company_name)}|{normalize_text(title)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
