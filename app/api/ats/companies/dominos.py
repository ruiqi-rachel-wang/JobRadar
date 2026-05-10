from ..base import *


def fetch_dominos(scrape, seen, company):
    """Domino's blocks normal HTTP, but its public jobs page is readable as markdown."""
    list_url = scrape.get("list_url") or "https://jobs.dominos.com/us/jobs/"
    reader_url = "https://r.jina.ai/http://r.jina.ai/http://" + list_url
    try:
        r = requests.get(reader_url, headers=HEADERS, timeout=35)
        r.raise_for_status()
    except Exception as e:
        from ..playwright import _fetch_playwright_with_source
        return _fetch_playwright_with_source(scrape, seen, company, "dominos")

    lines = r.text.splitlines()
    jobs = {}
    for idx, line in enumerate(lines):
        m = re.search(r"#+\s*\[([^\]]+)\]\((https://jobs\.dominos\.com/us/jobs/[^)]+)\)", line)
        if not m:
            continue
        title = norm(m.group(1))
        url = m.group(2).strip()
        location = ""
        posted = ""
        for nxt in lines[idx + 1:idx + 8]:
            loc_match = re.search(r"\*\s+\*\*([^*]+)\*\*", nxt)
            if loc_match and not location:
                location = normalize_location(norm(loc_match.group(1)))
            if not posted:
                posted = extract_posted_from_text(nxt)
        if title and url:
            jobs[url] = {
                "title": title,
                "url": url,
                "location": location,
                "source": "dominos",
                "posted": posted,
            }

    if jobs:
        return list(jobs.values()), None

    from ..playwright import _fetch_playwright_with_source
    return _fetch_playwright_with_source(scrape, seen, company, "dominos")
