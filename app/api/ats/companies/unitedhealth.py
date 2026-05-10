from ..base import *
from ..html import fetch_html

def fetch_unitedhealth(scrape, seen, company):
    return fetch_html({
        **scrape,
        "mode": "html",
        "list_url": "https://careers.unitedhealthgroup.com/search-jobs",
        "include": "/job/",
        "pagination": {"type": "query_page", "param": "pg", "start": 1, "hard_max_pages": scrape.get("hard_max_pages", 10000)},
    }, seen, company)
