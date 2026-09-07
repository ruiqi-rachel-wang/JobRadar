import unittest

from app.scanner_service import _find_existing_seen_job, _should_expire_jobs


class ScannerIdentityAndExpiryTests(unittest.TestCase):
    def test_corrected_title_reuses_existing_job_with_same_company_and_url(self):
        existing = {
            "_stored_id": "old-title-id",
            "company": "UnitedHealth Group",
            "url": "https://example.test/job/123",
        }
        result = _find_existing_seen_job(
            {"old-title-id": existing},
            {(existing["company"], existing["url"]): existing},
            stable_id="new-ats-id",
            legacy_id="new-title-id",
            company=existing["company"],
            url=existing["url"],
        )

        self.assertIs(result, existing)

    def test_partial_source_can_preserve_unseen_jobs_during_full_scan(self):
        self.assertFalse(
            _should_expire_jobs(
                is_fresh=False,
                err=None,
                raw=[{"title": "Newest page only"}],
                allow_empty=False,
                preserve_unseen=True,
            )
        )
        self.assertTrue(
            _should_expire_jobs(
                is_fresh=False,
                err=None,
                raw=[{"title": "Complete source"}],
                allow_empty=False,
                preserve_unseen=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
