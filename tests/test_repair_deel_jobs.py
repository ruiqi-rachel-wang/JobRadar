import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.repair_deel_jobs import posting_id, repair


class RepairDeelJobsTests(unittest.TestCase):
    job_id = "db861458-ff15-422d-aaaf-f39f39b73680"
    old_url = f"https://www.deel.com/careers/position/?ashby_jid={job_id}"
    new_url = f"https://jobs.deel.com/deel/job-details/{job_id}/application"

    def test_matches_provider_identity_across_old_and_new_urls(self):
        self.assertEqual(self.job_id, posting_id(self.old_url))
        self.assertEqual(self.job_id, posting_id(self.new_url))
        self.assertIsNone(posting_id("https://www.deel.com/careers/"))
        self.assertIsNone(posting_id(self.new_url.replace("jobs.deel.com", "other.example")))
        self.assertIsNone(posting_id("https://www.deel.com/careers/position/?ashby_jid=invalid"))

    def test_repairs_links_closes_expired_and_preserves_ids_dates_and_tracker(self):
        conn, auth = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self.addCleanup(auth.close)
        conn.row_factory = auth.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, title TEXT, url TEXT, "
                     "apply_url TEXT, company TEXT, is_open INTEGER, first_seen TEXT)")
        conn.execute("CREATE VIRTUAL TABLE jobs_fts USING fts5(job_id UNINDEXED, title)")
        auth.execute("CREATE TABLE user_apply_list (id INTEGER PRIMARY KEY, job_id TEXT, "
                     "job_title TEXT, job_url TEXT, job_company TEXT, stage TEXT)")
        expired_url = self.old_url.replace(self.job_id, "00000000-0000-0000-0000-000000000000")
        rows = [
            ("current", "Devsecops Engineer", self.old_url, None, "Deel", 1, "2026-01-01"),
            ("expired", "Expired Engineer", expired_url, None, "Deel", 1, "2026-01-01"),
            ("closed", "Devsecops Engineer", self.old_url, None, "Deel", 0, "2026-01-01"),
            ("other", "Engineer", "https://example.com/job/1", None, "Other", 1, "2026-01-01"),
        ]
        conn.executemany("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT INTO jobs_fts SELECT id,title FROM jobs WHERE is_open=1")
        auth.execute("INSERT INTO user_apply_list VALUES (1,?,?,?,?,?)",
                     ("current", "Devsecops Engineer", self.old_url, "Deel", "interview"))
        source = [{"title": "Devsecops Engineer", "url": self.new_url, "apply_url": self.new_url}]

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                repair(conn, auth, directory, [])
            with self.assertRaises(ValueError):
                repair(conn, auth, directory, [{"url": self.old_url}])
            preview = repair(conn, auth, directory, source)
            self.assertEqual(1, preview["expired_results_closed"])
            self.assertEqual(self.old_url, conn.execute("SELECT url FROM jobs WHERE id='current'").fetchone()[0])
            self.assertEqual([], list(Path(directory).iterdir()))

            result = repair(conn, auth, directory, source, apply=True, sqlite=True)
            self.assertEqual(3, result["job_rows_updated"])
            self.assertEqual(1, result["saved_jobs_updated"])
            backup = json.loads(Path(result["backup"]).read_text())
            self.assertEqual(3, len(backup["jobs"]))
            self.assertEqual(1, len(backup["user_apply_list"]))
            self.assertEqual(
                ("current", self.new_url, self.new_url, "2026-01-01", 1),
                tuple(conn.execute("SELECT id,url,apply_url,first_seen,is_open FROM jobs WHERE id='current'").fetchone()),
            )
            self.assertEqual({"current", "other"}, {
                r[0] for r in conn.execute("SELECT id FROM jobs WHERE is_open=1")
            })
            self.assertEqual({"current", "other"}, {
                r[0] for r in conn.execute("SELECT job_id FROM jobs_fts")
            })
            self.assertEqual((self.new_url, "interview"), tuple(auth.execute(
                "SELECT job_url,stage FROM user_apply_list WHERE id=1"
            ).fetchone()))
            again = repair(conn, auth, directory, source, apply=True, sqlite=True)
            self.assertEqual(0, again["job_rows_updated"])
            self.assertEqual(0, again["saved_jobs_updated"])


if __name__ == "__main__":
    unittest.main()
