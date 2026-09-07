"""Mobileye careers adapter for the server-rendered Nuxt job payload."""

import json
import re

from .base import BeautifulSoup, HEADERS, norm, requests


def _dereference(payload, value):
    if isinstance(value, int) and 0 <= value < len(payload):
        return payload[value]
    return value


def _jobs_from_payload(payload, base_url="https://careers.mobileye.com"):
    """Convert Nuxt/devalue references into canonical JobDrop records."""
    summaries = []
    details = {}

    for value in payload:
        if isinstance(value, list) and len(value) > len(summaries):
            resolved = [
                _dereference(payload, ref)
                for ref in value
                if isinstance(ref, int)
            ]
            if resolved and all(
                isinstance(item, dict) and {"jobid", "text"}.issubset(item)
                for item in resolved
            ):
                summaries = resolved

        if not isinstance(value, dict) or not {"jobid", "text", "location"}.issubset(value):
            continue
        job_id = _dereference(payload, value["jobid"])
        if isinstance(job_id, str):
            details[job_id] = value

    jobs = []
    for item in summaries:
        job_id = _dereference(payload, item.get("jobid"))
        title = norm(_dereference(payload, item.get("text")) or "")
        if not job_id or not title:
            continue

        detail = details.get(str(job_id), {})
        location = norm(_dereference(payload, detail.get("location")) or "")
        department = norm(_dereference(payload, detail.get("department")) or "")
        commitment = norm(_dereference(payload, detail.get("commitment")) or "")
        description_parts = []
        for key in ("additionalPlain", "descriptionPlain"):
            text = _dereference(payload, detail.get(key))
            if text:
                description_parts.append(str(text))
        lists = _dereference(payload, detail.get("lists")) or []
        if isinstance(lists, list):
            for ref in lists:
                section = _dereference(payload, ref)
                if not isinstance(section, dict):
                    continue
                heading = norm(_dereference(payload, section.get("text")) or "")
                content = _dereference(payload, section.get("content"))
                if content:
                    description_parts.append(
                        (f"<h3>{heading}</h3>\n" if heading else "") + str(content)
                    )

        slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
        url = f"{base_url.rstrip('/')}/jobs/{slug}/{job_id}"
        description = "\n\n".join(description_parts) or None
        jobs.append({
            "title": title,
            "url": url,
            "ats_id": str(job_id),
            "location": location,
            "location_raw": location or None,
            "source": "mobileye",
            "description": description,
            "description_raw": description,
            "employment_type": commitment or None,
            "employment_type_raw": commitment or None,
            "department": department or None,
        })
    return jobs


def fetch_mobileye(scrape, seen, company):
    list_url = scrape.get("list_url") or "https://careers.mobileye.com/jobs"
    try:
        response = requests.get(list_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        script = BeautifulSoup(response.text, "lxml").find(
            "script", id="__NUXT_DATA__", type="application/json"
        )
        if not script:
            return [], "Mobileye Nuxt job payload not found"
        payload = json.loads(script.string or script.get_text() or "")
        if not isinstance(payload, list):
            return [], "unexpected Mobileye payload"
        jobs = _jobs_from_payload(payload)
        return (jobs, None) if jobs else ([], "no jobs found")
    except Exception as exc:
        return [], str(exc)
