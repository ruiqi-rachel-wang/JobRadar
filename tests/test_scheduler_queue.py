import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_DATA = tempfile.TemporaryDirectory()
os.environ["JOBRADAR_DATA_DIR"] = _DATA.name
os.environ["JOBDROP_AUTH_DB_FILE"] = str(Path(_DATA.name) / "users.db")
os.environ["JOBRADAR_ENABLE_APP_SCHEDULER"] = "false"

from app.api import main
from app.api.database import connect_db
from app.scan_store import create_scan_run


class SchedulerQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.schedule_file = Path(self.temp.name) / "scan_schedule.json"
        self.queue_file = Path(self.temp.name) / "queued_scan.json"
        self.original_schedule_file = main._SCHEDULE_FILE
        self.original_queue_file = main._SCHEDULE_QUEUE_FILE
        self.original_schedule_cache = main._schedule_cache
        main._SCHEDULE_FILE = self.schedule_file
        main._SCHEDULE_QUEUE_FILE = self.queue_file
        main._schedule_cache = {}

    def tearDown(self):
        main._SCHEDULE_FILE = self.original_schedule_file
        main._SCHEDULE_QUEUE_FILE = self.original_queue_file
        main._schedule_cache = self.original_schedule_cache
        self.temp.cleanup()

    def test_only_newest_scheduled_scan_remains(self):
        main._queue_scheduled_scan("full")
        main._queue_scheduled_scan("fresh")
        self.assertEqual(main._pop_queued_scan_type(), "fresh")
        self.assertFalse(self.queue_file.exists())

    def test_queue_survives_until_popped(self):
        main._queue_scheduled_scan("full")
        self.assertTrue(self.queue_file.exists())
        self.assertEqual(main._pop_queued_scan_type(), "full")
        self.assertEqual(main._pop_queued_scan_type(), "")

    def test_queue_preserves_final_fresh_alert_metadata(self):
        main._queue_scheduled_scan(
            "fresh",
            scheduled_for="2026-07-14 19:00",
            send_alerts_after=True,
        )
        self.assertEqual(main._pop_queued_scan(), {
            "scan_type": "fresh",
            "scheduled_for": "2026-07-14 19:00",
            "send_alerts_after": True,
        })

    def test_only_last_fresh_slot_triggers_daily_alerts(self):
        cfg = {"enabled": True, "times": ["10:00", "13:00", "16:00", "19:00"]}
        self.assertFalse(main._is_final_fresh_schedule_slot("fresh", cfg, "16:00"))
        self.assertTrue(main._is_final_fresh_schedule_slot("fresh", cfg, "19:00"))
        self.assertFalse(main._is_final_fresh_schedule_slot("full", cfg, "19:00"))

    def test_final_slot_metadata_is_persisted_for_restart_resume(self):
        scan_run_id = create_scan_run(
            "fresh",
            10,
            scheduled_for="2026-07-14 19:00",
            send_alerts_after=True,
        )
        try:
            with connect_db(rows=True) as conn:
                row = conn.execute(
                    "SELECT scheduled_for, send_alerts_after FROM scan_runs WHERE id=?",
                    (scan_run_id,),
                ).fetchone()
            self.assertEqual(row["scheduled_for"], "2026-07-14 19:00")
            self.assertEqual(row["send_alerts_after"], 1)
        finally:
            with connect_db() as conn:
                conn.execute("DELETE FROM scan_runs WHERE id=?", (scan_run_id,))

    def test_final_fresh_scan_dispatches_even_when_that_scan_found_zero_new(self):
        result = {
            "new_count": 0,
            "scan_run_id": 123,
            "status": "partial_success",
        }
        with patch.object(main, "run_scan", return_value=result), patch.object(main, "_send_saved_search_alerts", return_value={"emails": 0}) as send:
            main.do_scan([], scan_type="fresh", send_alerts_after=True)
        send.assert_called_once_with()

    def test_schedule_cache_survives_database_contention(self):
        with patch.object(main, "get_app_state", return_value='{"fresh":{"enabled":true,"times":["10:00"]}}'):
            expected = main._get_schedule()
        with patch.object(main, "get_app_state", side_effect=RuntimeError("database is locked")):
            self.assertEqual(main._get_schedule(), expected)

    def test_schedule_file_can_be_saved_while_database_is_locked(self):
        expected = {
            "fresh": {"enabled": True, "times": ["10:00", "13:00"]},
            "full": {"enabled": True, "times": ["02:00"]},
        }
        with patch.object(main, "get_app_state", side_effect=RuntimeError("database is locked")):
            main._write_schedule_file(expected)
            main._schedule_cache = {}
            self.assertEqual(main._get_schedule(), expected)


if __name__ == "__main__":
    unittest.main()
