import os
import tempfile
import unittest
import uuid
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

    def test_free_account_apply_list_is_limited_to_fifteen(self):
        self.assertEqual(15, main.FREE_APPLY_LIMIT)
        username = f"free-limit-{uuid.uuid4().hex}"
        with connect_auth_db() as conn:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, role, plan_status) "
                "VALUES (?, ?, 'user', 'free')",
                (username, "unused"),
            )
            user_id = cursor.lastrowid
            conn.executemany(
                "INSERT INTO user_apply_list (user_id, job_id, stage) VALUES (?, ?, ?)",
                [(user_id, f"saved-{index}", "to-apply") for index in range(15)],
            )

        client = main.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = user_id
            session["username"] = username

        response = client.post("/api/apply", json={"job_id": "saved-16"})

        self.assertEqual(402, response.status_code)
        self.assertEqual("apply_limit_reached", response.get_json()["code"])
        self.assertEqual(15, response.get_json()["limit"])

    def test_signup_copy_uses_configured_free_limit(self):
        response = self.client.get("/signup")

        self.assertEqual(200, response.status_code)
        self.assertIn(b"track up to 15 jobs", response.data)


if __name__ == "__main__":
    unittest.main()
