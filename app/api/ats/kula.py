"""Kula careers-board adapter.

Kula's server-rendered cards expose useful job metadata, but the posting link
itself is labelled only ``Apply Now``.  Parse the surrounding card so JobDrop
stores the real title, location, department, and employment type.
"""

import re
from urllib.parse import urljoin

from .base import BeautifulSoup, HEADERS, norm, requests


def _parse_kula_cards(html, list_url, company_slug):
    soup = BeautifulSoup(html, "lxml")
    jobs = []
    seen_urls = set()
    posting_path = re.compile(
        rf"/{re.escape(company_slug.strip('/'))}/(\d+)/?$",
        re.I,
    )

    for link in soup.find_all("a", href=True):
        url = urljoin(list_url, link.get("href") or "")
        match = posting_path.search(url)
        if not match or url in seen_urls:
            continue

        card = link.find_parent(
            "div",
            class_=lambda value: value and "chakra-card" in value,
        )
        if not card:
            continue
        fields = [norm(node.get_text(" ", strip=True)) for node in card.select("p.chakra-text")]
        if len(fields) < 2 or not fields[0]:
            continue

        department_node = card.select_one("span.chakra-stack > span > span")
        department = norm(department_node.get_text(" ", strip=True)) if department_node else ""
        employment = fields[-1] if len(fields) >= 3 else ""
        location_raw = fields[1]
        locations_raw = [
            re.sub(r"\s*\+\s*\d+\s+locations?\s*$", "", value, flags=re.I).strip()
            for value in location_raw.split(";")
            if value.strip()
        ]

        jobs.append({
            "title": fields[0],
            "url": url,
            "ats_id": match.group(1),
            "location": location_raw,
            "location_raw": location_raw,
            "locations_raw": locations_raw,
            "source": "kula",
            "employment_type": employment or None,
            "employment_type_raw": employment or None,
            "department": department or None,
        })
        seen_urls.add(url)

    return jobs


def fetch_kula(scrape, seen, company):
    list_url = scrape.get("list_url") or ""
    company_slug = scrape.get("company_slug") or ""
    if not list_url:
        return [], "no list_url"
    if not company_slug:
        return [], "no company_slug"

    try:
        response = requests.get(list_url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        jobs = _parse_kula_cards(response.text, list_url, company_slug)
        return (jobs, None) if jobs else ([], "no jobs found")
    except Exception as exc:
        return [], str(exc)
