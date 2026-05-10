from ..base import *
from ..oraclecloud import fetch_oraclecloud

def fetch_americanexpress(scrape, seen, company):
    return fetch_oraclecloud({
        **scrape,
        "mode": "oraclecloud",
        "list_url": "https://egug.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs",
        "site": "CX_1",
        "limit": 100,
        "hard_max_pages": scrape.get("hard_max_pages", 10000),
    }, seen, company)
