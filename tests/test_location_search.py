import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


_DATA = tempfile.TemporaryDirectory()
os.environ.setdefault("JOBRADAR_DATA_DIR", _DATA.name)
os.environ.setdefault("JOBDROP_AUTH_DB_FILE", str(Path(_DATA.name) / "users.db"))
os.environ.setdefault("JOBRADAR_ENABLE_APP_SCHEDULER", "false")

from app.api import main
from app.api.database import connect_db
from app.api.search import location_where
from app.api.utils import normalize_locations


class LocationSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE jobs (id TEXT, location TEXT, country_iso TEXT, locations TEXT)")

    def add(self, name, location, **overrides):
        fields = {**normalize_locations(location), **overrides}
        self.conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?)", (
            name, fields["location"], fields["country_iso"],
            json.dumps(fields["locations"]),
        ))

    def matches(self, query):
        clauses, params = location_where(query)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return {row[0] for row in self.conn.execute("SELECT id FROM jobs" + where, params)}

    def test_missing_location_and_country_survive_location_searches(self):
        for label in (None, "", "Not listed", "Not specified", "Multiple Locations", "Remote"):
            self.add(str(label), label)
        self.add("country missing", "Unlisted City, Unlisted Region")
        self.add("blank country", "Unlisted City", country_iso="  ")
        expected = {str(label) for label in (None, "", "Not listed", "Not specified", "Multiple Locations", "Remote")}
        expected.update(("country missing", "blank country"))
        for query in ("New York", "US", "United Kingdom", "France"):
            with self.subTest(query=query):
                self.assertEqual(expected, self.matches(query))

    def test_known_conflicts_and_multiple_known_countries_are_excluded(self):
        self.add("ny", "New York, NY, US")
        self.add("london", "London, UK")
        self.add("multiple", ["London, UK", "Paris, France"])
        self.add("country name", "Paris, France", country_iso=None, locations=[{"country": "France"}])
        self.assertEqual({"ny"}, self.matches("New York"))
        self.assertEqual({"london", "multiple"}, self.matches("United Kingdom"))
        self.assertEqual({"multiple", "country name"}, self.matches("France"))

    def test_unknown_secondary_country_is_kept_even_with_a_known_primary(self):
        self.add("partly unknown", ["London, UK", "Unlisted City"])
        self.add("fully known", ["London, UK", "Paris, France"])
        self.assertEqual({"partly unknown"}, self.matches("US"))

    def test_country_restricted_remote_jobs_still_need_to_match(self):
        self.add("unrestricted", "Remote")
        self.add("us", "Remote, US")
        self.add("uk", "Remote, UK")
        self.assertEqual({"unrestricted", "us"}, self.matches("USA"))

    def test_country_tokens_match_before_secondary_office_separator(self):
        self.add("multiple", ["New York, NY, US", "London, UK"])
        self.add("australia", "Sydney, Australia")
        for query in ("us", "United States", "usa", "UK", "London", "NY"):
            with self.subTest(query=query):
                self.assertEqual({"multiple"}, self.matches(query))

    def test_missing_country_metadata_and_unusable_arrays_are_permissive(self):
        for index, value in enumerate((None, "[]", "null", "{}", "broken json", '["Unlisted City"]')):
            self.conn.execute("INSERT INTO jobs VALUES (?, 'Unlisted City', NULL, ?)", (str(index), value))
        self.assertEqual({str(i) for i in range(6)}, self.matches("New York"))

    def test_empty_query_and_literal_wildcards(self):
        self.add("known", "New York, NY, US")
        self.assertEqual({"known"}, self.matches(" "))
        self.assertEqual(set(), self.matches("%"))
        self.assertEqual(set(), self.matches("_"))


class LocationSearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.prefix = "location-search-test-"
        self.client = main.app.test_client()
        companies = [{"name": "Location Test Co"}]
        for target in ("current_companies", "_public_companies"):
            patcher = patch.object(main, target, return_value=companies)
            patcher.start()
            self.addCleanup(patcher.stop)
        fixtures = [
            ("unknown", None, 1, None),
            ("partial", "Unlisted City, Unlisted Region", 1, None),
            ("match", "New York, NY, US", 1, None),
            ("conflict", "London, UK", 1, None),
            ("closed", None, 0, None),
            ("experienced", None, 1, 10),
        ]
        with connect_db() as conn:
            for index, (name, location, is_open, exp_min) in enumerate(fixtures):
                fields = normalize_locations(location)
                conn.execute(
                    "INSERT INTO jobs (id, title, company, location, country_iso, locations, "
                    "first_seen, posted, is_open, exp_min) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)",
                    (self.prefix + name, "Engineer " + name, "Location Test Co", fields["location"],
                     fields["country_iso"], json.dumps(fields["locations"]),
                     f"2026-11-02 01:00:{index:02d}", is_open, exp_min),
                )

    def tearDown(self):
        with connect_db() as conn:
            conn.execute("DELETE FROM jobs WHERE id LIKE ?", (self.prefix + "%",))

    def test_get_and_post_filter_before_pagination_and_respect_other_filters(self):
        filters = {"company": "Location Test Co", "location": "New York", "experience": "2", "limit": 2}
        first = self.client.get("/api/jobs", query_string=filters)
        self.assertEqual(200, first.status_code)
        page = first.get_json()
        self.assertEqual([self.prefix + "match", self.prefix + "partial"], [j["id"] for j in page["jobs"]])
        self.assertTrue(page["has_more"])
        second = self.client.post("/api/jobs", json={**filters, "page": 2})
        self.assertEqual(200, second.status_code)
        page = second.get_json()
        self.assertEqual([self.prefix + "unknown"], [j["id"] for j in page["jobs"]])
        self.assertFalse(page["has_more"])
        self.assertTrue(page["total_exact"])
        self.assertEqual(3, page["total"])
        no_keyword_match = self.client.get("/api/jobs", query_string={**filters, "keyword": "zznomatchingtitlezz"})
        self.assertEqual([], no_keyword_match.get_json()["jobs"])

    def test_saved_search_uses_the_same_unknown_location_rule(self):
        jobs = main._saved_search_alert_jobs(
            {"kw": "", "loc": "New York", "since": "any", "category": "All", "tag": "",
             "exp_yrs": "2", "role": "user"},
            "2026-11-02 00:00:00", "2026-11-02 23:59:59",
        )
        self.assertEqual({"Engineer unknown", "Engineer partial", "Engineer match"}, {job["title"] for job in jobs})


if __name__ == "__main__":
    unittest.main()
