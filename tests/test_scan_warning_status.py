import unittest
from unittest.mock import patch

from app.scan_store import finish_scan_run_from_statuses
from app.scanner_service import _is_unexplained_empty_full_scan, _make_summary


class ScanWarningStatusTests(unittest.TestCase):
    def test_unverified_empty_full_scan_is_a_warning(self):
        self.assertTrue(_is_unexplained_empty_full_scan(
            is_fresh=False,
            raw=[],
            err=None,
            allow_empty=False,
        ))
        summary = _make_summary(
            "Example",
            "html",
            [],
            None,
            1.0,
            0,
            warning_message="Full scan returned no jobs; existing jobs were preserved.",
        )
        self.assertEqual(summary["status"], "warning")
        self.assertIn("preserved", summary["error"])

    def test_expected_or_non_authoritative_results_do_not_warn(self):
        cases = (
            {"is_fresh": True, "raw": [], "err": None, "allow_empty": False},
            {"is_fresh": False, "raw": [], "err": None, "allow_empty": True},
            {"is_fresh": False, "raw": [{"title": "Job"}], "err": None, "allow_empty": False},
            {"is_fresh": False, "raw": [], "err": "HTTP 500", "allow_empty": False},
        )
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(_is_unexplained_empty_full_scan(**case))

    def test_warning_makes_an_otherwise_successful_run_partial(self):
        totals = {
            "companies_scanned": 1,
            "total_jobs_found": 0,
            "new_jobs_found": 0,
            "updated_jobs_found": 0,
            "failed_companies": 0,
            "warning_companies": 1,
        }
        with patch("app.scan_store.scan_run_totals", return_value=totals), patch(
            "app.scan_store.finish_scan_run"
        ) as finish:
            finish_scan_run_from_statuses(42, status="success")
        finish.assert_called_once_with(
            42,
            status="partial_success",
            error_message=None,
            companies_scanned=1,
            total_jobs_found=0,
            new_jobs_found=0,
            updated_jobs_found=0,
            failed_companies=0,
        )


if __name__ == "__main__":
    unittest.main()
