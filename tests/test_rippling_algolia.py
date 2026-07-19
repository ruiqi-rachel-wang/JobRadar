import unittest
from unittest.mock import patch

from app.api.ats.rippling import fetch_rippling


class _Response:
    def __init__(self, *, text="", data=None):
        self.text = text
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class RipplingAlgoliaTests(unittest.TestCase):
    @patch("app.api.ats.rippling.requests.post")
    @patch("app.api.ats.rippling.requests.get")
    def test_public_index_rows_merge_into_one_job_with_all_locations(self, get, post):
        get.return_value = _Response(text="<html><body>Open roles</body></html>")
        post.return_value = _Response(data={
            "nbHits": 2,
            "nbPages": 1,
            "hits": [
                {
                    "name": "Platform Engineer",
                    "url": "https://ats.example/jobs/job-1",
                    "locationNames": ["New York, NY"],
                },
                {
                    "name": "Platform Engineer",
                    "url": "https://ats.example/jobs/job-1",
                    "locationNames": ["Remote (US)"],
                },
            ],
        })

        jobs, error = fetch_rippling({
            "list_url": "https://www.example.com/careers/open-roles",
            "algolia_app_id": "APPID",
            "algolia_search_key": "search-only-key",
            "algolia_index": "careers",
            "fetch_experience_details": False,
        }, {}, "Example")

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Platform Engineer", jobs[0]["title"])
        self.assertIn("New York, NY", jobs[0]["location"])
        self.assertIn("Remote", jobs[0]["location"])


if __name__ == "__main__":
    unittest.main()
