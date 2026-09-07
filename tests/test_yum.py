import unittest
from unittest.mock import patch

from app.api.ats.yum import fetch_yum


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "jobs": [{
                "jobs": [{
                    "id": 99,
                    "unique_identifier": "5270",
                    "title": "Digital Strategy Manager",
                    "url": "technology/plano-tx/digital-strategy-manager/5270",
                    "application": "https://oracle.example/apply/5270",
                    "address": "Plano, TX, United States",
                    "date": "2026-06-10 13:20:22",
                    "content": "Lead digital strategy.",
                    "requirements": "Five years of experience.",
                    "category": "Technology",
                    "subdivision": "Pizza Hut",
                    "job_type": "Hybrid",
                    "live": 1,
                }],
            }],
        }


class YumTests(unittest.TestCase):
    @patch("app.api.ats.yum.requests.get", return_value=_Response())
    def test_reads_grouped_public_job_api(self, get):
        jobs, error = fetch_yum(
            {"list_url": "https://jobs.yum.com/jobs"}, {}, "Yum Brands"
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("5270", jobs[0]["ats_id"])
        self.assertEqual("Plano, TX, US", jobs[0]["location"])
        self.assertEqual(
            "https://jobs.yum.com/jobs/technology/plano-tx/digital-strategy-manager/5270",
            jobs[0]["url"],
        )
        self.assertIn("Five years of experience.", jobs[0]["description_raw"])
        self.assertEqual("Pizza Hut", jobs[0]["team"])


if __name__ == "__main__":
    unittest.main()
