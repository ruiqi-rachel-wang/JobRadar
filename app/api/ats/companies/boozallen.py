from ..base import *
from ..workday import fetch_workday

def fetch_boozallen(scrape, seen, company):
    return fetch_workday({
        **scrape,
        "mode": "workday",
        "list_url": "https://bah.wd1.myworkdayjobs.com/BAH_Jobs",
    }, seen, company)
