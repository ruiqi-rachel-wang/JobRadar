import sqlite3
import unittest

from app.api.search import (
    JOB_DATE_ORDER_SQL,
    keyword_groups,
    keyword_matches,
    keyword_where,
)


class JobSearchTests(unittest.TestCase):
    def assert_matches(self, query, title, keywords=None):
        self.assertTrue(
            keyword_matches(title, "Example Co", keywords or [], keyword_groups(query)),
            f"{query!r} should match {title!r}",
        )

    def test_software_engineer_includes_python_application_developer(self):
        groups = keyword_groups("software engineer")
        clauses, params = keyword_where(groups)

        self.assertEqual(len(clauses), 1)
        self.assertIn("%application developer%", params)
        self.assert_matches("software engineer", "Python Application Developer")

    def test_broad_role_families_cover_unrelated_occupations(self):
        examples = [
            ("finance", "Senior Tax Specialist"),
            ("operations", "Logistics Coordinator"),
            ("quality", "Automation Test Engineer"),
            ("legal", "Commercial Contracts Manager"),
            ("healthcare", "Registered Nurse"),
            ("education", "Curriculum Specialist"),
            ("content", "Technical Writer"),
            ("admin", "Executive Assistant"),
        ]
        for query, title in examples:
            with self.subTest(query=query, title=title):
                self.assert_matches(query, title)

    def test_generic_keywords_search_stored_job_keywords(self):
        groups = keyword_groups("python")
        clauses, _ = keyword_where(groups)

        self.assertTrue(any("LOWER(keywords)" in clause for clause in clauses))
        self.assert_matches("python", "Application Developer", ["Django", "Python"])

    def test_role_family_does_not_match_unrelated_title(self):
        self.assertFalse(
            keyword_matches(
                "Retail Store Manager",
                "Example Co",
                [],
                keyword_groups("software engineer"),
            )
        )

    def test_software_engineer_abbreviations_require_word_boundaries(self):
        groups = keyword_groups("software engineer")
        clauses, params = keyword_where(groups)

        self.assertFalse(keyword_matches("Retail Sportswear Ambassador", "Example Co", [], groups))
        self.assert_matches("software engineer", "Senior SWE - Backend")

        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE jobs (title TEXT)")
        conn.executemany(
            "INSERT INTO jobs VALUES (?)",
            [("Retail Sportswear Ambassador",), ("Senior SWE - Backend",)],
        )
        rows = conn.execute(
            f"SELECT title FROM jobs WHERE {' AND '.join(clauses)}",
            params,
        ).fetchall()

        self.assertEqual([row[0] for row in rows], ["Senior SWE - Backend"])

    def test_jobs_order_by_posted_date_then_first_seen_fallback(self):
        self.assertEqual(
            JOB_DATE_ORDER_SQL,
            "COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) DESC, first_seen DESC",
        )

    def test_sql_results_keep_broad_matches_in_effective_posted_order(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.execute(
            "CREATE TABLE jobs (title TEXT, company TEXT, keywords TEXT, "
            "first_seen TEXT, posted TEXT, is_open INTEGER)"
        )
        conn.executemany(
            "INSERT INTO jobs VALUES (?, 'Example Co', '[]', ?, ?, 1)",
            [
                ("Software Engineer", "2026-07-17 10:00:00", "2026-07-15"),
                ("Python Application Developer", "2026-07-16 10:00:00", ""),
            ],
        )
        clauses, params = keyword_where(keyword_groups("software engineer"))
        rows = conn.execute(
            f"SELECT title FROM jobs WHERE is_open = 1 AND {' AND '.join(clauses)} "
            f"ORDER BY {JOB_DATE_ORDER_SQL}",
            params,
        ).fetchall()

        self.assertEqual(
            [row[0] for row in rows],
            ["Python Application Developer", "Software Engineer"],
        )


if __name__ == "__main__":
    unittest.main()
