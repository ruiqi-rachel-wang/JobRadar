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

    status_code = 200


class RipplingAlgoliaTests(unittest.TestCase):
    @patch("app.api.ats.rippling.requests.get")
    def test_public_api_merges_duplicate_ids_and_all_locations(self, get):
        get.side_effect = [
            _Response(data=[
                {
                    "uuid": "job-1",
                    "name": "Medical Director",
                    "url": "https://ats.rippling.com/example/jobs/job-1",
                    "workLocation": {"label": "Pasadena, CA", "id": "pasadena"},
                },
                {
                    "uuid": "job-1",
                    "name": "Medical Director",
                    "url": "https://ats.rippling.com/example/jobs/job-1",
                    "workLocation": {"label": "Long Beach, CA", "id": "long-beach"},
                },
            ]),
            _Response(data={}),
        ]

        jobs, error = fetch_rippling(
            {"rippling_slug": "example"},
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual(
            ["Pasadena, CA", "Long Beach, CA"],
            jobs[0]["locations_raw"],
        )
        self.assertEqual(2, len(
            jobs[0]["raw_data"]["workLocationVariants"]
        ))

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
