import unittest
from unittest.mock import Mock, patch

from app.api.ats.gem import fetch_gem


class GemTests(unittest.TestCase):
    @patch("app.api.ats.gem.requests.post")
    def test_fetches_structured_public_postings(self, post):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "oatsExternalJobPostings": {
                    "jobPostings": [{
                        "id": "internal-posting",
                        "extId": "public-id",
                        "title": "Principal Product Designer",
                        "locations": [{
                            "name": "US - Remote",
                            "isRemote": True,
                        }],
                        "job": {
                            "id": "internal-job",
                            "department": {"name": "Product Design"},
                            "locationType": "REMOTE",
                            "employmentType": "FULL_TIME",
                        },
                    }],
                },
                "jobBoardExternal": {"teamDisplayName": "Function Health"},
            },
        }
        post.return_value = response

        jobs, error = fetch_gem({"gem_board": "function-health"}, {}, "Function Health")

        self.assertIsNone(error)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Principal Product Designer")
        self.assertEqual(jobs[0]["url"], "https://jobs.gem.com/function-health/public-id")
        self.assertEqual(jobs[0]["department"], "Product Design")
        self.assertEqual(jobs[0]["remote_mode"], "remote")
        self.assertEqual(jobs[0]["source"], "gem")


if __name__ == "__main__":
    unittest.main()
