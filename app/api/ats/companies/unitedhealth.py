from ..base import *
from ..html import fetch_html


def _title_from_unitedhealth_url(url):
    slug = urlparse(url).path.strip("/").split("/")
    if len(slug) < 3:
        return ""
    raw = slug[-3]
    title = re.sub(r"[-_]+", " ", raw).strip()
    return norm(title).title() if title else ""


def fetch_unitedhealth(scrape, seen, company):
    jobs, err = fetch_html({
        **scrape,
        "mode": "html",
        "list_url": "https://careers.unitedhealthgroup.com/search-jobs",
        "include": "/job/",
        "verify_ssl": False,
        "pagination": {"type": "query_page", "param": "pg", "start": 1, "hard_max_pages": scrape.get("hard_max_pages", 10000)},
    }, seen, company)
    for job in jobs or []:
        title = norm(job.get("title") or "")
        if re.fullmatch(r"\d{6,}", title):
            clean = _title_from_unitedhealth_url(job.get("url") or "")
            if clean:
                job["title"] = clean
    return jobs, err
