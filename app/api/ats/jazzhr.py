from .base import *

# ── JazzHR ────────────────────────────────────────────────────────────────────

def fetch_jazzhr(scrape, seen, company):
    list_url = scrape.get("list_url", "")
    if not list_url:
        return [], "no list_url"
    try:
        r = requests.get(list_url.rstrip("/") + "/jobs/details", headers=HEADERS, timeout=25)
        if r.status_code == 404:
            r = requests.get(list_url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        return [], str(e)
    soup = BeautifulSoup(r.text, "lxml")
    jobs = {}
    for tr in soup.find_all("tr"):
        cells = [norm(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        a = tr.find("a", href=True)
        if not a or len(cells) < 2:
            continue
        href = (a.get("href") or "").strip()
        if "/apply/" not in href:
            continue
        title = norm(a.get_text(" ", strip=True)) or cells[0]
        location_raw = cells[-1]
        loc = extract_location_from_text(location_raw) or normalize_location(location_raw)
        if title and title.lower() not in ("position", "search"):
            jurl = urljoin(r.url, href)
            ats_match = re.search(r"/apply/([^/?#]+)", jurl)
            jobs[urljoin(r.url, href)] = {
                "title": title,
                "url": jurl,
                "ats_id": ats_match.group(1) if ats_match else None,
                "location": loc,
                "location_raw": location_raw or None,
                "locations_raw": [location_raw] if location_raw else [],
                "source": "jazzhr",
                "posted": extract_posted_from_text(norm(tr.get_text(" ", strip=True))) or None,
            }
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if "/apply/" not in href:
            continue
        jurl = urljoin(r.url, href)
        if jurl in jobs:
            continue
        title = norm(a.get_text(" ", strip=True))
        if not title or title.lower() in ("apply", "view job", "details"):
            parent = a.parent
            title = norm(parent.get_text(" ", strip=True)) if parent else title
        parent_text = norm(a.parent.get_text(" ", strip=True)) if a.parent else ""
        title = re.sub(r"\s+Apply\s*$", "", title, flags=re.I).strip()
        if len(title) >= 4:
            location_raw = extract_location_from_text(parent_text)
            ats_match = re.search(r"/apply/([^/?#]+)", jurl)
            jobs[jurl] = {
                "title": title,
                "url": jurl,
                "ats_id": ats_match.group(1) if ats_match else None,
                "location": location_raw,
                "location_raw": location_raw or None,
                "locations_raw": [location_raw] if location_raw else [],
                "source": "jazzhr",
                "posted": extract_posted_from_text(parent_text) or None,
            }
    return list(jobs.values()), None

