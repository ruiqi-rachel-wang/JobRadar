import unittest
from unittest.mock import patch

from app.api.ats.jobsyn import fetch_jobsyn


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class JobSyncTests(unittest.TestCase):
    @patch("app.api.ats.jobsyn.requests.get")
    def test_fetches_all_pages_and_builds_canonical_detail_urls(self, get):
        source_job = {
            "title_exact": "Platform Engineer",
            "title_slug": "platform-engineer",
            "city_slab_exact": "new-york/ny/usa/jobs::New York, NY",
            "location_exact": "New York, NY",
            "guid": "ABC123",
            "reqid": "42",
            "date_new": "2026-08-04T12:00:00Z",
            "description": "Build trading systems.",
            "job_type": "Full-Time",
        }
        get.side_effect = [
            _Response({
                "jobs": [source_job],
                "pagination": {"total": 2, "has_more_pages": True},
            }),
            _Response({
                "jobs": [{**source_job, "guid": "DEF456", "reqid": "43"}],
                "pagination": {"total": 2, "has_more_pages": False},
            }),
        ]

        jobs, error = fetch_jobsyn(
            {"list_url": "https://example.jobs/jobs/"}, {}, "Example"
        )

        self.assertIsNone(error)
        self.assertEqual(2, len(jobs))
        self.assertEqual(
            "https://example.jobs/new-york-usa/platform-engineer/ABC123/job/",
            jobs[0]["url"],
        )
        self.assertEqual("42", jobs[0]["requisition_id"])
        self.assertEqual("New York, NY, US", jobs[0]["location"])
        self.assertEqual("example.jobs", get.call_args_list[0].kwargs["headers"]["x-origin"])

    @patch("app.api.ats.jobsyn.requests.get")
    def test_reports_incomplete_source(self, get):
        get.return_value = _Response({
            "jobs": [],
            "pagination": {"total": 10, "has_more_pages": False},
        })

        jobs, error = fetch_jobsyn(
            {"list_url": "https://example.jobs/jobs/"}, {}, "Example"
        )

        self.assertEqual([], jobs)
        self.assertEqual(
            "Example partial listing: fetched 0 of 10 source records", error
        )


if __name__ == "__main__":
    unittest.main()
