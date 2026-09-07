import unittest
from unittest.mock import patch

from app.api.apis import _enrich
from app.api.ats.oraclecloud import (
    _oraclecloud_cached_detail_is_sufficient,
    _oraclecloud_text_has_numeric_experience,
    fetch_oraclecloud,
)


class _Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class OracleCloudDetailSelectionTests(unittest.TestCase):
    @patch("app.api.ats.oraclecloud.requests.get")
    def test_configured_api_host_is_used_for_vanity_domain(self, get):
        get.return_value = _Response({
            "items": [{
                "TotalJobsCount": 1,
                "requisitionList": [{"Id": "42", "Title": "Risk Analyst"}],
            }],
        })

        jobs, error = fetch_oraclecloud(
            {
                "list_url": "https://careers.example.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs",
                "site": "CX_1",
                "api_host": "tenant.fa.us2.oraclecloud.com",
                "fetch_experience_details": False,
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertTrue(get.call_args.args[0].startswith(
            "https://tenant.fa.us2.oraclecloud.com/hcmRestApi/"
        ))

    @patch("app.api.ats.oraclecloud.requests.get")
    def test_large_board_merges_opposite_sort_windows(self, get):
        def response(url, **_kwargs):
            ascending = "sortBy=POSTING_DATES_ASC" in url
            offset_two = "offset=2" in url
            if offset_two:
                rows = []
            elif ascending:
                rows = [
                    {"Id": "2", "Title": "Second"},
                    {"Id": "3", "Title": "Third"},
                ]
            else:
                rows = [
                    {"Id": "1", "Title": "First"},
                    {"Id": "2", "Title": "Second"},
                ]
            return _Response({
                "items": [{"TotalJobsCount": 3, "requisitionList": rows}],
            })

        get.side_effect = response
        jobs, error = fetch_oraclecloud(
            {
                "list_url": "https://careers.example.com/hcmUI/CandidateExperience/en/sites/CX_1/jobs",
                "site": "CX_1",
                "limit": 2,
                "hard_max_pages": 2,
                "fetch_experience_details": False,
                "complete_over_10000": True,
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual({"1", "2", "3"}, {job["ats_id"] for job in jobs})

    def test_short_summary_without_years_needs_detail(self):
        text = "Carry out critical tech solutions across multiple technical areas."
        self.assertFalse(
            _oraclecloud_text_has_numeric_experience("Lead Software Engineer", text)
        )

    def test_long_summary_without_years_still_needs_detail(self):
        text = (
            "Design, develop, and troubleshoot secure mobile applications. "
            "Collaborate with product, design, and platform teams to deliver "
            "stable software across the development lifecycle. "
        ) * 6
        self.assertGreater(len(text), 500)
        self.assertFalse(
            _oraclecloud_text_has_numeric_experience(
                "iOS / Swift Software Engineer III",
                text,
            )
        )

    def test_numeric_search_text_can_skip_detail(self):
        text = "Required qualifications include 5+ years of software engineering experience."
        self.assertTrue(
            _oraclecloud_text_has_numeric_experience("Lead Software Engineer", text)
        )

    def test_legacy_short_summary_is_repaired_once(self):
        legacy = {
            "exp_min": None,
            "exp_max": None,
            "exp_label": "No minimum stated",
            "exp_source": "text",
        }
        repaired = {**legacy, "exp_source": "oracle_detail"}
        self.assertFalse(_oraclecloud_cached_detail_is_sufficient(legacy))
        self.assertTrue(_oraclecloud_cached_detail_is_sufficient(repaired))

    def test_full_detail_without_numeric_years_gets_cache_marker(self):
        jobs = [{
            "title": "Software Engineer",
            "url": "https://example.com/job/1",
            "location": "New York",
            "description": "Build and operate reliable services with demonstrated experience.",
            "description_is_detail": True,
        }]
        _enrich(jobs, {}, "Example", scan_type="fresh")
        self.assertEqual(jobs[0]["exp_source"], "oracle_detail")


if __name__ == "__main__":
    unittest.main()
