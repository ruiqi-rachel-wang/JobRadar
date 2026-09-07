import unittest
from unittest.mock import patch

from app.api.ats.accenture import fetch_accenture


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class AccentureFetcherTests(unittest.TestCase):
    @patch("app.api.ats.accenture.requests.post")
    def test_paginates_first_party_us_catalog_and_preserves_raw_fields(self, post):
        first = {
            "data": [
                {
                    "guid": "guid-1",
                    "requisitionId": "R-1",
                    "title": "Software Engineer",
                    "jobDetailUrl": "https://www.accenture.com/{0}/careers/jobdetails?id=R-1",
                    "location": "New York, NY",
                    "country": "USA",
                    "postedDateText": "Posted 2 days ago",
                    "jobScheduleDescription": "Full-Time",
                    "remoteType": "Hybrid",
                    "jobDescriptionClean": "Build systems.",
                    "qualificationClean": "Five years of experience.",
                }
            ],
            "totalHits": {"total": 2, "overMaxHits": "False"},
        }
        second = {
            "data": [
                {
                    "guid": "guid-2",
                    "title": "Data Engineer",
                    "jobDetailUrl": "https://www.accenture.com/{0}/careers/jobdetails?id=R-2",
                    "locations": ["Atlanta, GA", "Remote"],
                    "country": "USA",
                }
            ],
            "totalHits": {"total": 2, "overMaxHits": "False"},
        }
        post.side_effect = [_Response(first), _Response(second)]

        jobs, error = fetch_accenture(
            {
                "api_url": "https://example.test/findjobs",
                "country": "USA",
                "country_site": "us-en",
                "page_size": 1,
            },
            {},
            "Accenture",
        )

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(
            post.call_args_list[1].kwargs["data"]["startIndex"],
            1,
        )
        self.assertEqual(jobs[0]["ats_id"], "guid-1")
        self.assertEqual(jobs[0]["location_raw"], "New York, NY")
        self.assertEqual(jobs[0]["locations_raw"], ["New York, NY"])
        self.assertEqual(jobs[0]["country_iso"], "US")
        self.assertEqual(jobs[0]["posted_raw"], "Posted 2 days ago")
        self.assertIsNone(jobs[0]["posted_at"])
        self.assertIn("## Job description", jobs[0]["description_raw"])
        self.assertIn("## Qualifications", jobs[0]["description_raw"])
        self.assertNotIn("salary", jobs[0])
        self.assertEqual(
            jobs[1]["url"],
            "https://www.accenture.com/us-en/careers/jobdetails?id=R-2",
        )
        self.assertEqual(
            jobs[1]["locations_raw"],
            ["Atlanta, GA", "Remote"],
        )


if __name__ == "__main__":
    unittest.main()
