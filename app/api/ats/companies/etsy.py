from ..base import *
from ..html import fetch_html

def fetch_etsy(scrape, seen, company):
    return fetch_html({
        **scrape,
        "mode": "html",
        "list_url": "https://careers.etsy.com/jobs/search",
        "include": "https://careers.etsy.com/jobs/",
        "pagination": {"type": "query_page", "param": "page", "start": 1, "hard_max_pages": scrape.get("hard_max_pages", 10000)},
    }, seen, company)
