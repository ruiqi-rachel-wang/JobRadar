import unittest
from unittest.mock import patch

from app.api.ats.barnes_noble import fetch_barnes_noble


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class BarnesNobleTests(unittest.TestCase):
    @patch("app.api.ats.barnes_noble.requests.get")
    def test_reads_complete_job_family_feeds_and_preserves_source_fields(self, get):
        get.side_effect = [
            _Response({
                "totalElements": 1,
                "content": [{
                    "id": "92614BR",
                    "title": "Software Engineer",
                    "location": "NY-Westbury",
                    "city": "Westbury",
                    "state": "New York",
                    "category": "Information Systems & Technology",
                    "employmentType": "Full-Time",
                    "publishedDate": "2026-07-29T01:00:00",
                    "description": "Build systems.",
                    "jobDetailLink": "https://xjobs.brassring.com/apply/92614BR",
                }],
            }),
            _Response({"totalElements": 0, "content": []}),
            _Response({"totalElements": 0, "content": []}),
        ]
        jobs, error = fetch_barnes_noble({}, {}, "Barnes & Noble")

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["ats_id"], "92614BR")
        self.assertEqual(jobs[0]["location_raw"], "NY-Westbury")
        self.assertEqual(jobs[0]["location"], "Westbury, NY, US")
        self.assertEqual(jobs[0]["posted_raw"], "2026-07-29T01:00:00")
        self.assertIn("Job summary", jobs[0]["description_raw"])
        self.assertNotIn("salary", jobs[0])


if __name__ == "__main__":
    unittest.main()
