import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.repair_loom_jobs import canonical_detail_url, repair


class RepairLoomJobsTests(unittest.TestCase):
    def test_only_recognized_job_details_get_canonicalized(self):
        self.assertEqual(
            "https://www.atlassian.com/company/careers/details/123",
            canonical_detail_url("https://www.useloom.com/company/careers/details/123?x=1"),
        )
        for url in [
            "https://www.useloom.com/software/loom",
            "https://www.useloom.com/company/careers/all-jobs?search=Loom",
            "https://unrelated.example/company/careers/details/123",
        ]:
            self.assertIsNone(canonical_detail_url(url))

    def test_repair_preserves_ids_dates_and_saved_jobs_and_is_repeatable(self):
        conn = sqlite3.connect(":memory:")
        auth = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        self.addCleanup(auth.close)
        conn.row_factory = auth.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, title TEXT, url TEXT, "
                     "apply_url TEXT, company TEXT, is_open INTEGER, first_seen TEXT)")
        conn.execute("CREATE VIRTUAL TABLE jobs_fts USING fts5(job_id UNINDEXED, title)")
        auth.execute("CREATE TABLE user_apply_list (id INTEGER PRIMARY KEY, job_id TEXT, "
                     "job_title TEXT, job_url TEXT, job_company TEXT, stage TEXT)")
        rows = [
            ("loom", "Engineer, Loom", "https://www.useloom.com/company/careers/details/123",
             None, "Loom", 1, "2026-01-01"),
            ("jira", "Jira Designer", "https://www.useloom.com/company/careers/details/456",
             None, "Loom", 1, "2026-01-01"),
            ("nav", "Loom async video", "https://www.useloom.com/software/loom",
             None, "Loom", 1, "2026-01-01"),
            ("expired", "Loom Engineer", "https://www.useloom.com/company/careers/details/789",
             None, "Loom", 0, "2026-01-01"),
            ("other", "Engineer", "https://example.com/job/123",
             None, "Other", 1, "2026-01-01"),
        ]
        conn.executemany("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)", rows)
        conn.execute("INSERT INTO jobs_fts SELECT id,title FROM jobs WHERE is_open=1")
        auth.executemany("INSERT INTO user_apply_list VALUES (?,?,?,?,?,?)", [
            (1, "loom", "Engineer, Loom", rows[0][2], "Loom", "interview"),
            (2, "jira", "Jira Designer", rows[1][2], "Loom", "applied"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            preview = repair(conn, auth, directory)
            self.assertEqual(2, preview["incorrect_results_closed"])
            self.assertEqual(rows[0][2], conn.execute("SELECT url FROM jobs WHERE id='loom'").fetchone()[0])
            self.assertEqual([], list(Path(directory).iterdir()))

            result = repair(conn, auth, directory, apply=True, sqlite=True)
            backup = json.loads(Path(result["backup"]).read_text())
            self.assertEqual(4, len(backup["jobs"]))
            self.assertEqual(2, len(backup["user_apply_list"]))
            self.assertEqual(2, result["incorrect_results_closed"])
            self.assertEqual({"loom", "other"}, {
                r[0] for r in conn.execute("SELECT id FROM jobs WHERE is_open=1")
            })
            self.assertEqual({"loom", "other"}, {
                r[0] for r in conn.execute("SELECT job_id FROM jobs_fts")
            })
            self.assertEqual("2026-01-01", conn.execute("SELECT first_seen FROM jobs WHERE id='loom'").fetchone()[0])
            self.assertEqual(
                ("https://www.atlassian.com/company/careers/details/456", "Atlassian", "applied"),
                tuple(auth.execute("SELECT job_url,job_company,stage FROM user_apply_list WHERE id=2").fetchone()),
            )
            again = repair(conn, auth, directory, apply=True, sqlite=True)
            self.assertEqual(0, again["job_rows_updated"])
            self.assertEqual(0, again["saved_jobs_updated"])


if __name__ == "__main__":
    unittest.main()
