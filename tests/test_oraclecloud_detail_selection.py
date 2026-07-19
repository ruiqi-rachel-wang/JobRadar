import unittest

from app.api.apis import _enrich
from app.api.ats.oraclecloud import (
    _oraclecloud_cached_detail_is_sufficient,
    _oraclecloud_text_has_numeric_experience,
)


class OracleCloudDetailSelectionTests(unittest.TestCase):
    def test_short_summary_without_years_needs_detail(self):
        text = "Carry out critical tech solutions across multiple technical areas."
        self.assertFalse(
            _oraclecloud_text_has_numeric_experience("Lead Software Engineer", text)
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
