from .base import *


def fetch_careyaya(scrape, seen, company):
    """Parse CareYaya's first-party career cards, whose links say only 'I'm Interested'."""
    list_url = scrape.get("list_url") or "https://www.careyaya.org/careers"
    try:
        response = requests.get(list_url, headers=HEADERS, timeout=25)
        response.raise_for_status()
    except Exception as exc:
        return [], str(exc)

    soup = BeautifulSoup(response.text, "lxml")
    jobs = []
    visited = set()
    for anchor in soup.select('a[href*="/careers/"]'):
        href = anchor.get("href") or ""
        url = urljoin(list_url, href)
        if url in visited:
            continue
        card = anchor.find_parent("div", class_="py-4")
        if not card:
            continue
        title_node = card.select_one("p.font-bold")
        meta_nodes = card.select("p.text-gray-500")
        title = norm(title_node.get_text(" ", strip=True) if title_node else "")
        if not title:
            continue
        visited.add(url)
        location_raw = norm(meta_nodes[0].get_text(" ", strip=True)) if meta_nodes else ""
        is_remote = "remote" in location_raw.lower()
        is_in_person = location_raw.lower().startswith("in-person")
        location = re.sub(r"^in-person\s*,?\s*", "", location_raw, flags=re.I)
        slug_parts = [part for part in urlparse(url).path.split("/") if part]
        ats_id = slug_parts[-2] if len(slug_parts) >= 2 else url
        jobs.append({
            "title": title,
            "url": url,
            "ats_id": ats_id,
            "location": normalize_location(location),
            "location_raw": location or None,
            "locations_raw": [location] if location else [],
            "source": "careyaya",
            "workplace_type": "On-site" if is_in_person else ("Remote" if is_remote else None),
            "remote_mode": "remote" if is_remote else None,
            "raw_data": {"workplace_label": location_raw} if location_raw else None,
        })
    return jobs, None
