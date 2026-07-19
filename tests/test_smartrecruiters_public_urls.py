import unittest
from unittest.mock import Mock, patch

from app.api.ats.smartrecruiters import fetch_smartrecruiters
from app.job_identity import make_job_id


class SmartRecruitersPublicUrlTests(unittest.TestCase):
    def test_public_url_keeps_legacy_api_url_identity(self):
        api_url = (
            "https://api.smartrecruiters.com/v1/companies/"
            "NBCUniversal3/postings/744000138201707"
        )
        public_url = (
            "https://jobs.smartrecruiters.com/"
            "NBCUniversal3/744000138201707-campaign-manager"
        )
        self.assertEqual(
            make_job_id("NBCUniversal", api_url, "Campaign Manager"),
            make_job_id("NBCUniversal", public_url, "Campaign Manager"),
        )

    @patch("app.api.ats.smartrecruiters.requests.get")
    def test_fetcher_replaces_api_ref_with_public_job_page(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "content": [{
                "id": "744000138201707",
                "name": "Campaign Manager",
                "ref": (
                    "https://api.smartrecruiters.com/v1/companies/"
                    "NBCUniversal3/postings/744000138201707"
                ),
                "location": {"city": "New York", "region": "NY", "country": "US"},
                "releasedDate": "2026-07-16",
            }],
            "totalFound": 1,
        }
        get.return_value = response

        jobs, error = fetch_smartrecruiters(
            {
                "smartrecruiters_company": "NBCUniversal3",
                "max_desc_details": 0,
            },
            {},
            "NBCUniversal",
        )

        self.assertIsNone(error)
        self.assertEqual(
            jobs[0]["url"],
            "https://jobs.smartrecruiters.com/NBCUniversal3/744000138201707",
        )


if __name__ == "__main__":
    unittest.main()
