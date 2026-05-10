from .base import *

# ── Jobvite ───────────────────────────────────────────────────────────────────

def fetch_jobvite(scrape, seen, company):
    list_url = scrape.get("list_url", "")
    if not list_url:
        slug = scrape.get("jobvite_slug") or scrape.get("slug") or ""
        list_url = f"https://jobs.jobvite.com/{slug}/jobs" if slug else ""
    if not list_url:
        return [], "no list_url or jobvite_slug"
    try:
        r = requests.get(list_url, headers=HEADERS, timeout=25)
        r.raise_for_status()
    except Exception as e:
        return [], str(e)
    soup = BeautifulSoup(r.text, "lxml")
    jobs = {}
    for a in soup.find_all("a", href=True):
        href = a.get("href") or ""
        if "/job/" not in href:
            continue
        title = norm(a.get_text(" ", strip=True))
        if not title or title.lower() in ("apply", "view job"):
            continue
        jurl = urljoin(r.url, href)
        row = a.find_parent("tr") or a.find_parent("li") or a.find_parent("div")
        parent_text = norm(row.get_text(" ", strip=True)) if row else title
        loc = extract_location_from_text(parent_text.replace(title, " ", 1))
        posted = extract_posted_from_text(parent_text)
        jobs[jurl] = {"title": title, "url": jurl, "location": loc, "source": "jobvite", "posted": posted}
    return list(jobs.values()), None


