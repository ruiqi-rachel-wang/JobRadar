import unittest
from unittest.mock import patch

from app.api.ats.comeet import fetch_comeet


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return [{
            "name": "ML Engineer",
            "department": "R&D",
            "employment_type": "Full-time",
            "uid": "27.348",
            "workplace_type": "On-site",
            "location": {
                "name": "Tel Aviv",
                "country": "IL",
                "is_remote": False,
            },
            "url_active_page": "https://www.comeet.com/jobs/example/17.008/ml-engineer/27.348",
        }]


class ComeetTests(unittest.TestCase):
    @patch("app.api.ats.comeet.requests.get", return_value=_Response())
    def test_fetches_public_positions(self, get):
        jobs, error = fetch_comeet(
            {"list_url": "https://www.comeet.co/careers-api/2.0/company/example/positions"},
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "ML Engineer")
        self.assertEqual(jobs[0]["ats_id"], "27.348")
        self.assertEqual(jobs[0]["source"], "comeet")
        self.assertEqual(jobs[0]["location"], "Tel Aviv, Israel")
        get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
