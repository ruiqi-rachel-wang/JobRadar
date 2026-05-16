from ..base import *


_NIKE_JOB_RE = re.compile(
    r'\*\s+### \[([^\]]+)\]\((https://careers\.nike\.com/[^)\s]+/job/[^)\s]+)\)'
    r'([A-Z]+-\d+)\s+(.+?)\s{2,}([^\n]+?)\s{2,}\[View Job\]',
    re.S,
)


def fetch_nike(scrape, seen, company):
    """Nike's rendered jobs pages are reliably available through reader markdown."""
    max_pages = int(scrape.get("hard_max_pages") or 10000)
    jobs_by_url = {}

    for page in range(1, max_pages + 1):
        url = f"https://careers.nike.com/jobs/page/{page}"
        try:
            r = requests.get("https://r.jina.ai/" + url, headers=HEADERS, timeout=35)
            r.raise_for_status()
        except Exception as e:
            if jobs_by_url:
                break
            return [], str(e)

        found = 0
        for m in _NIKE_JOB_RE.finditer(r.text):
            title = norm(m.group(1))
            jurl = m.group(2)
            loc = normalize_location(norm(m.group(4)))
            if not title or not jurl:
                continue
            jid = make_id(company, jurl, title)
            if scrape.get("stop_on_seen") and jid in seen:
                return list(jobs_by_url.values()), None
            jobs_by_url[jurl] = {
                "title": title,
                "url": jurl,
                "location": loc,
                "source": "nike",
                "posted": "",
            }
            found += 1

        if found == 0:
            break

    return list(jobs_by_url.values()), None
