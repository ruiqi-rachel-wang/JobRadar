import unittest
from unittest.mock import patch

from app.api.ats.mckinsey import fetch_mckinsey


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class McKinseyTests(unittest.TestCase):
    @patch("app.api.ats.mckinsey.requests.get")
    def test_public_search_api_paginates_and_builds_canonical_urls(self, get):
        def response(_url, params, **_kwargs):
            if params["start"] == 1:
                return _Response({
                    "numFound": 2,
                    "docs": [{
                        "jobID": "110292",
                        "title": "AI Engineer",
                        "friendlyURL": "aiengineer-110292",
                        "cities": ["Berlin"],
                        "countries": ["Germany"],
                        "postedToLinkedInDate": "2026-08-20",
                    }],
                })
            if params["start"] == 2:
                return _Response({
                    "numFound": 2,
                    "docs": [{
                        "jobID": "111044",
                        "title": "HR Administrator",
                        "friendlyURL": "hradministrator-111044",
                        "cities": ["New York City"],
                        "countries": ["United States"],
                    }],
                })
            return _Response({"numFound": 2, "docs": []})

        get.side_effect = response
        jobs, error = fetch_mckinsey({}, {}, "McKinsey")

        self.assertIsNone(error)
        self.assertEqual(2, len(jobs))
        self.assertEqual(
            "https://www.mckinsey.com/careers/search-jobs/jobs/aiengineer-110292",
            jobs[0]["url"],
        )
        self.assertEqual("Berlin, Germany", jobs[0]["location"])


if __name__ == "__main__":
    unittest.main()
