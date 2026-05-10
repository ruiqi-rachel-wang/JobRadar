from ..base import *
from ..playwright import fetch_playwright

def fetch_vimeo(scrape, seen, company):
    # Vimeo's old Greenhouse board token now returns 404, and the current
    # careers page does not expose listing links in static HTML.
    return fetch_playwright({
        **scrape,
        "mode": "playwright",
        "list_url": "https://vimeo.com/careers",
        "prefix": "https://vimeo.com/careers/jobs/",
        "scroll_rounds": 20,
        "validate_prefix": False,
    }, seen, company)
