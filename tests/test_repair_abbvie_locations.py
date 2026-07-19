import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.repair_abbvie_locations import repair


class RepairAbbVieLocationsTests(unittest.TestCase):
    def test_repairs_location_closes_navigation_and_updates_fts(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "jobs.db"
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE jobs (id TEXT PRIMARY KEY, title TEXT, url TEXT, "
                "company TEXT, location TEXT, is_open INTEGER)"
            )
            conn.execute(
                "CREATE VIRTUAL TABLE jobs_fts USING fts5("
                "job_id UNINDEXED, title, company, location)"
            )
            conn.executemany(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
                [
                    (
                        "job",
                        "Operator",
                        "https://careers.abbvie.com/job/operator-in-waco-tx-jid-123",
                        "AbbVie",
                        "Waco, TX Travel No Location Waco Work location type On-Site "
                        "Brands AbbVie Job Type Full-time Description Make medicine",
                        1,
                    ),
                    (
                        "nav",
                        "Privacy policy",
                        "https://careers.abbvie.com/privacy-policy",
                        "AbbVie",
                        "",
                        1,
                    ),
                ],
            )
            conn.execute(
                "INSERT INTO jobs_fts SELECT id,title,company,location FROM jobs"
            )
            conn.commit()
            conn.close()

            result = repair(db)

            conn = sqlite3.connect(db)
            self.assertEqual(
                ("Waco, TX, US", 1),
                conn.execute("SELECT location,is_open FROM jobs WHERE id='job'").fetchone(),
            )
            self.assertEqual(
                0,
                conn.execute("SELECT is_open FROM jobs WHERE id='nav'").fetchone()[0],
            )
            self.assertEqual(
                [("job", "Waco, TX, US")],
                conn.execute("SELECT job_id,location FROM jobs_fts").fetchall(),
            )
            conn.close()
            self.assertEqual(1, result["locations_repaired"])
            self.assertEqual(1, result["non_job_rows_closed"])
            self.assertTrue(Path(result["backup"]).exists())


if __name__ == "__main__":
    unittest.main()
