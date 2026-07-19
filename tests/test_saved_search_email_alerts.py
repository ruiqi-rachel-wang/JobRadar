import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


_DATA = tempfile.TemporaryDirectory()
os.environ.setdefault("JOBRADAR_DATA_DIR", _DATA.name)
os.environ.setdefault("JOBDROP_AUTH_DB_FILE", str(Path(_DATA.name) / "users.db"))
os.environ.setdefault("JOBRADAR_ENABLE_APP_SCHEDULER", "false")

from app.api import main
from app.api.database import connect_auth_db, connect_db


class SavedSearchEmailAlertTests(unittest.TestCase):
    def setUp(self):
        self.prefix = "email-alert-test-"
        with connect_auth_db() as conn:
            conn.execute(
                "INSERT INTO users "
                "(username, email, password_hash, role, plan_status, email_alerts_enabled) "
                "VALUES (?, ?, ?, 'user', 'pro', 1)",
                (self.prefix + "user", "alerts@example.com", "unused"),
            )
            self.user_id = conn.execute(
                "SELECT id FROM users WHERE username=?", (self.prefix + "user",)
            ).fetchone()[0]

    def tearDown(self):
        with connect_db() as conn:
            conn.execute("DELETE FROM jobs WHERE id LIKE ?", (self.prefix + "%",))
        with connect_auth_db() as conn:
            conn.execute("DELETE FROM user_saved_searches WHERE user_id=?", (self.user_id,))
            conn.execute("DELETE FROM users WHERE id=?", (self.user_id,))

    def _add_search(self, name="Engineering", cursor="2026-07-14 18:00:00"):
        with connect_auth_db() as conn:
            conn.execute(
                "INSERT INTO user_saved_searches "
                "(user_id, name, kw, loc, exp_yrs, since, category, "
                "email_alert_enabled, alert_cursor_at, created_at) "
                "VALUES (?, ?, 'engineer', '', '', 'any', 'All', 1, ?, ?)",
                (self.user_id, name, cursor, cursor),
            )
            return conn.execute(
                "SELECT id FROM user_saved_searches WHERE user_id=? ORDER BY id DESC LIMIT 1",
                (self.user_id,),
            ).fetchone()[0]

    def _add_job(self, number, first_seen):
        with connect_db() as conn:
            conn.execute(
                "INSERT INTO jobs "
                "(id, title, url, company, location, source, categories, first_seen, "
                "posted, is_open, exp_label, skills, keywords, employment_type) "
                "VALUES (?, ?, ?, '3M', 'New York, NY, US', 'test', '[]', ?, '', 1, '', '[]', '[]', '')",
                (
                    f"{self.prefix}{number}",
                    f"Software Engineer {number}",
                    f"https://example.com/jobs/{number}",
                    first_seen,
                ),
            )

    def test_sends_every_matching_job_once_and_advances_cursor(self):
        search_id = self._add_search()
        for number in range(20):
            self._add_job(number, f"2026-07-14 18:{number + 1:02d}:00")
        self._add_job(99, "2026-07-14 19:01:00")
        deliveries = []

        def capture(to_email, subject, text_body, html_body):
            deliveries.append((to_email, subject, text_body, html_body))
            return True

        with patch.object(main, "EMAIL_ALERTS_CONFIGURED", True), patch.object(main, "_send_email", side_effect=capture):
            summary = main._send_saved_search_alerts(datetime(2026, 7, 14, 19, 0, 0))
            repeat = main._send_saved_search_alerts(datetime(2026, 7, 14, 19, 0, 0))

        self.assertEqual(summary["emails"], 1)
        self.assertEqual(summary["jobs"], 20)
        self.assertEqual(repeat["emails"], 0)
        self.assertEqual(len(deliveries), 1)
        self.assertIn("20 new jobs", deliveries[0][1])
        self.assertIn("Software Engineer 19", deliveries[0][2])
        self.assertNotIn("Software Engineer 99", deliveries[0][2])
        self.assertIn(
            '<a href="https://example.com/jobs/19">Software Engineer 19</a>',
            deliveries[0][3],
        )
        self.assertNotIn(">Open job</a>", deliveries[0][3])
        self.assertIn(
            "Software Engineer 19 (https://example.com/jobs/19) at 3M",
            deliveries[0][2],
        )
        with connect_auth_db(rows=True) as conn:
            row = conn.execute(
                "SELECT alert_cursor_at, last_alert_sent_at FROM user_saved_searches WHERE id=?",
                (search_id,),
            ).fetchone()
        self.assertEqual(row["alert_cursor_at"], "2026-07-14 19:00:00")
        self.assertEqual(row["last_alert_sent_at"], "2026-07-14 19:00:00")

    def test_each_saved_search_gets_its_own_email(self):
        self._add_search("Backend")
        self._add_search("Platform")
        self._add_job(1, "2026-07-14 18:30:00")

        with patch.object(main, "EMAIL_ALERTS_CONFIGURED", True), patch.object(main, "_send_email", return_value=True) as send:
            summary = main._send_saved_search_alerts(datetime(2026, 7, 14, 19, 0, 0))

        self.assertEqual(summary["emails"], 2)
        self.assertEqual(send.call_count, 2)

    def test_first_email_contains_only_jobs_from_today(self):
        self._add_search(cursor="2026-07-10 12:00:00")
        self._add_job(1, "2026-07-13 23:59:59")
        self._add_job(2, "2026-07-14 00:00:00")
        self._add_job(3, "2026-07-14 18:30:00")
        deliveries = []

        def capture(to_email, subject, text_body, html_body):
            deliveries.append(text_body)
            return True

        with patch.object(main, "EMAIL_ALERTS_CONFIGURED", True), patch.object(main, "_send_email", side_effect=capture):
            summary = main._send_saved_search_alerts(datetime(2026, 7, 14, 19, 0, 0))

        self.assertEqual(summary["jobs"], 2)
        self.assertNotIn("Software Engineer 1", deliveries[0])
        self.assertIn("Software Engineer 2", deliveries[0])
        self.assertIn("Software Engineer 3", deliveries[0])

    def test_later_email_uses_last_successful_email_not_cursor(self):
        search_id = self._add_search(cursor="2026-07-14 18:45:00")
        with connect_auth_db() as conn:
            conn.execute(
                "UPDATE user_saved_searches SET last_alert_sent_at=? WHERE id=?",
                ("2026-07-14 18:00:00", search_id),
            )
        self._add_job(1, "2026-07-14 17:59:59")
        self._add_job(2, "2026-07-14 18:30:00")

        with patch.object(main, "EMAIL_ALERTS_CONFIGURED", True), patch.object(main, "_send_email", return_value=True) as send:
            summary = main._send_saved_search_alerts(datetime(2026, 7, 14, 19, 0, 0))

        self.assertEqual(summary["jobs"], 1)
        self.assertIn("Software Engineer 2", send.call_args.args[2])
        self.assertNotIn("Software Engineer 1", send.call_args.args[2])

    def test_failed_delivery_keeps_cursor_for_next_retry(self):
        search_id = self._add_search()
        self._add_job(1, "2026-07-14 18:30:00")

        with patch.object(main, "EMAIL_ALERTS_CONFIGURED", True), patch.object(main, "_send_email", side_effect=RuntimeError("smtp unavailable")):
            summary = main._send_saved_search_alerts(datetime(2026, 7, 14, 19, 0, 0))

        self.assertEqual(summary["failed"], 1)
        with connect_auth_db(rows=True) as conn:
            row = conn.execute(
                "SELECT alert_cursor_at, last_alert_sent_at FROM user_saved_searches WHERE id=?",
                (search_id,),
            ).fetchone()
        self.assertEqual(row["alert_cursor_at"], "2026-07-14 18:00:00")
        self.assertEqual(row["last_alert_sent_at"], "")


if __name__ == "__main__":
    unittest.main()
