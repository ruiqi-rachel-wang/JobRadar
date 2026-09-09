import unittest
from unittest.mock import MagicMock, patch

from app.api.main import app


class LargeCompanySearchTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    @patch("app.api.main.connect_db")
    @patch("app.api.main.jobs_from_seen_fallback", return_value=([], 0))
    @patch("app.api.main._public_companies")
    @patch("app.api.main.current_companies")
    def test_json_post_accepts_more_than_300_companies(
        self, current_companies, public_companies, _fallback, connect_db
    ):
        companies = [{"name": f"Company {number}"} for number in range(350)]
        current_companies.return_value = companies
        public_companies.return_value = companies

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        connect_db.return_value.__enter__.return_value = conn

        response = self.client.post(
            "/api/jobs",
            json={"companies": [c["name"] for c in companies], "limit": 100, "page": 1},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["jobs"], [])
        _fallback.assert_not_called()
        sql, params = conn.execute.call_args.args
        self.assertIn("company IN", sql)
        self.assertEqual(len(params), 352)

    @patch("app.api.main.connect_db")
    @patch("app.api.main._public_companies")
    @patch("app.api.main.current_companies")
    def test_tag_search_uses_positive_company_filter(
        self, current_companies, public_companies, connect_db
    ):
        companies = [
            {"name": "Tagged Co", "tags": ["Curated List"]},
            {"name": "Other Co", "tags": []},
        ]
        current_companies.return_value = companies
        public_companies.return_value = companies

        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        connect_db.return_value.__enter__.return_value = conn

        response = self.client.get("/api/jobs?tag=Curated+List&limit=100")

        self.assertEqual(response.status_code, 200)
        sql, params = conn.execute.call_args.args
        self.assertIn("company IN", sql)
        self.assertNotIn("company NOT IN", sql)
        self.assertEqual(params, ["Tagged Co", 100, 0])


if __name__ == "__main__":
    unittest.main()
