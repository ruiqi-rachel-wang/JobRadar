import unittest

from app.api.database import connect_db
from app.api.job_data import delete_expired_jobs


class EmptyFullScanExpiryTests(unittest.TestCase):
    company = "empty-expiry-test-company"
    job_id = "empty-expiry-test-job"

    def setUp(self):
        with connect_db() as conn:
            conn.execute(
                "INSERT INTO jobs "
                "(id,title,url,company,location,source,categories,first_seen,posted,is_open) "
                "VALUES (?,?,?,?,?,'test','[]','2026-07-17 00:00:00','',1) "
                "ON CONFLICT(id) DO UPDATE SET is_open=1",
                (self.job_id, "Test Job", "https://example.com/job", self.company, ""),
            )

    def tearDown(self):
        with connect_db() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (self.job_id,))

    def _is_open(self):
        with connect_db(rows=True) as conn:
            return conn.execute(
                "SELECT is_open FROM jobs WHERE id=?", (self.job_id,)
            ).fetchone()["is_open"]

    def test_empty_result_is_non_destructive_by_default(self):
        delete_expired_jobs(self.company, [])
        self.assertEqual(self._is_open(), 1)

    def test_verified_empty_result_can_close_existing_jobs(self):
        delete_expired_jobs(self.company, [], allow_empty=True)
        self.assertEqual(self._is_open(), 0)


if __name__ == "__main__":
    unittest.main()
