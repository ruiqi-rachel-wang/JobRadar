import unittest
from unittest.mock import Mock, patch

from app.api.ats.dover import fetch_dover


class DoverTests(unittest.TestCase):
    @patch("app.api.ats.dover.requests.get")
    def test_fetches_published_non_sample_jobs(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "next": None,
            "results": [{
                "id": "posting-id",
                "title": "Manager - Strategy and Operations",
                "locations": [{"name": "United States"}, {"name": "Europe"}],
                "workplace_type": "REMOTE",
                "is_published": True,
                "is_sample": False,
            }],
        }
        get.return_value = response

        jobs, error = fetch_dover(
            {"careers_page_id": "page-id", "company_slug": "Vial"}, {}, "Vial"
        )

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["url"], "https://app.dover.com/Vial/careers/posting-id")
        self.assertEqual(jobs[0]["locations_raw"], ["United States", "Europe"])
        self.assertEqual(jobs[0]["remote_mode"], "remote")
        self.assertEqual(jobs[0]["source"], "dover")


if __name__ == "__main__":
    unittest.main()
