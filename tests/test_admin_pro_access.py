import os
import tempfile
import unittest
from pathlib import Path


_DATA = tempfile.TemporaryDirectory()
os.environ["JOBRADAR_DATA_DIR"] = _DATA.name
os.environ["JOBDROP_AUTH_DB_FILE"] = str(Path(_DATA.name) / "users.db")
os.environ["JOBRADAR_ENABLE_APP_SCHEDULER"] = "false"
os.environ["ADMIN_PASSWORD"] = "test-admin-password"

from app.api import main
from app.api.database import connect_auth_db


class AdminProAccessTests(unittest.TestCase):
    def setUp(self):
        main.app.config.update(TESTING=True, SECRET_KEY="admin-pro-test")
        self.client = main.app.test_client()
        with self.client.session_transaction() as session:
            # Simulate an admin cookie created before persistent admin accounts.
            session["admin_ok"] = True

    def test_existing_admin_session_gets_persistent_pro_features(self):
        response = self.client.get("/app")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin \xc2\xb7 Pro access", response.data)
        self.assertIn(b"const HAS_APPLY_ACCESS = true", response.data)
        self.assertIn(b"const IS_PRO       = true", response.data)

        with self.client.session_transaction() as session:
            admin_id = session.get("user_id")
        self.assertIsNotNone(admin_id)
        with connect_auth_db(rows=True) as conn:
            admin = conn.execute(
                "SELECT username, role FROM users WHERE id=?",
                (admin_id,),
            ).fetchone()
        self.assertEqual(admin["username"], "admin")
        self.assertEqual(admin["role"], "admin")

        original_limit = main.FREE_APPLY_LIMIT
        main.FREE_APPLY_LIMIT = 0
        try:
            add_apply = self.client.post("/api/apply", json={
                "job_id": "admin-test-job",
                "job_title": "Test Role",
                "job_company": "Test Company",
            })
        finally:
            main.FREE_APPLY_LIMIT = original_limit
        self.assertEqual(add_apply.status_code, 200)
        apply_rows = self.client.get("/api/apply").get_json()
        self.assertEqual([row["job_id"] for row in apply_rows], ["admin-test-job"])

        saved_search = self.client.post("/api/searches", json={
            "name": "Admin search",
            "kw": "engineer",
        })
        self.assertEqual(saved_search.status_code, 200)
        self.assertEqual(saved_search.get_json()["name"], "Admin search")


if __name__ == "__main__":
    unittest.main()
