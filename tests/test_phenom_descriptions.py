import json
import unittest
from unittest.mock import patch

from app.api.apis import fetch_jobs
from app.api.ats.playwright import fetch_phenom


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class PhenomDescriptionTests(unittest.TestCase):
    @patch("app.api.ats.playwright.requests.get")
    def test_enabled_description_fetch_adds_experience_to_native_phenom_jobs(self, mock_get):
        data = {
            "eagerLoadRefineSearch": {
                "totalHits": 1,
                "data": {
                    "jobs": [{
                        "title": "Platform Engineer",
                        "reqId": "R-4217",
                        "cityStateCountry": "Chicago, Illinois, United States",
                        "postedDate": "2026-07-16",
                    }]
                },
            }
        }
        listing = f"phApp.ddo = {json.dumps(data)}; phApp"
        description = (
            "Job description and qualifications. "
            "Qualifications: 10+ years of professional engineering experience. "
            "You will design, operate, and improve reliable market technology while "
            "working closely with product, infrastructure, and security teams."
        )
        mock_get.side_effect = [_Response(listing), _Response(description)]

        company = {
            "name": "Example Markets",
            "scrape": {
                "mode": "phenom",
                "list_url": "https://careers.example.com/us/en/search-results",
                "prefix": "https://careers.example.com/us/en/job/",
                "fetch_descriptions": True,
            },
        }
        with patch("app.api.apis.robots_allowed", return_value=True):
            jobs, error = fetch_jobs(company, {}, scan_type="full")

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["description"], description)
        self.assertEqual(jobs[0]["exp_min"], 10)
        self.assertEqual(jobs[0]["exp_label"], "10+ years")
        self.assertEqual(jobs[0]["exp_source"], "text")
        self.assertEqual(mock_get.call_count, 2)

    @patch("app.api.ats.playwright.requests.get")
    def test_existing_description_is_not_refetched(self, mock_get):
        data = {
            "eagerLoadRefineSearch": {
                "totalHits": 1,
                "data": {"jobs": [{"title": "Platform Engineer", "reqId": "R-4217"}]},
            }
        }
        mock_get.return_value = _Response(f"phApp.ddo = {json.dumps(data)}; phApp")
        seen = {
            "example-markets:r-4217": {"has_description": True},
        }

        with patch("app.api.ats.playwright.make_id", return_value="example-markets:r-4217"):
            jobs, error = fetch_phenom(
                {
                    "list_url": "https://careers.example.com/us/en/search-results",
                    "prefix": "https://careers.example.com/us/en/job/",
                    "fetch_descriptions": True,
                },
                seen,
                "Example Markets",
            )

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(mock_get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
