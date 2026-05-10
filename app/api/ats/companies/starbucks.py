from ..base import *
from ..eightfold import fetch_eightfold
from ..html import fetch_html

def fetch_starbucks(scrape, seen, company):
    jobs, err = fetch_eightfold({
        **scrape,
        "mode": "eightfold",
        "list_url": "https://apply.starbucks.com/careers",
        "domain": "starbucks.com",
        "hard_max_pages": scrape.get("hard_max_pages", 10000),
    }, seen, company)
    if jobs:
        return jobs, None
    return fetch_html({
        **scrape,
        "mode": "html",
        "list_url": "https://apply.starbucks.com/careers?domain=starbucks.com&hl=en&start=0&sort_by=timestamp",
        "include": "/careers/job/",
        "pagination": {"type": "none", "max_pages": 1},
    }, seen, company)
