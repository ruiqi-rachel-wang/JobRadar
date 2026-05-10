from ..base import *
from ..avature import fetch_avature

def fetch_dhl(scrape, seen, company):
    return fetch_avature({
        **scrape,
        "mode": "avature",
        "list_url": "https://dpdhlgroup.avature.net/jobs/SearchJobs",
        "page_size": 20,
        "hard_max_pages": scrape.get("hard_max_pages", 10000),
    }, seen, company)
