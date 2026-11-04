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
from app.api.geography import init_place_index, sync_job_places


class LocationSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute("CREATE TABLE jobs (id TEXT, location TEXT, country_iso TEXT, locations TEXT)")
        init_place_index(self.conn)

    def add(self, name, location, **overrides):
        fields = {**normalize_locations(location), **overrides}
        self.conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?)", (
            name, fields["location"], fields["country_iso"],
            json.dumps(fields["locations"]),
        ))

        sync_job_places(self.conn, [{"id": name, **fields}])

    def matches(self, query):
        clauses, params = location_where(query)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return {row[0] for row in self.conn.execute("SELECT id FROM jobs" + where, params)}

    def test_missing_locations_and_countries_do_not_match_a_selected_place(self):
        for label in (None, "", "Not listed", "Not specified", "Multiple Locations", "Remote"):
            self.add(str(label), label)
        self.add("country missing", "Unlisted City, Unlisted Region")
        self.add("blank country", "Unlisted City", country_iso="  ")
        expected = {str(label) for label in (None, "", "Not listed", "Not specified", "Multiple Locations", "Remote")}
        expected.update(("country missing", "blank country"))
        for query in ("New York", "US", "United Kingdom", "France"):
            with self.subTest(query=query):
                self.assertEqual(set(), self.matches(query))

    def test_known_conflicts_and_multiple_known_countries_are_excluded(self):
        self.add("ny", "New York, NY, US")
        self.add("london", "London, UK")
        self.add("multiple", ["London, UK", "Paris, France"])
        self.add("country name", "Paris, France", country_iso=None, locations=[{"country": "France"}])
        self.assertEqual({"ny"}, self.matches("New York"))
        self.assertEqual({"london", "multiple"}, self.matches("United Kingdom"))
        self.assertEqual({"multiple", "country name"}, self.matches("France"))

    def test_unknown_secondary_country_does_not_create_an_unrelated_match(self):
        self.add("partly unknown", ["London, UK", "Unlisted City"])
        self.add("fully known", ["London, UK", "Paris, France"])
        self.assertEqual(set(), self.matches("US"))

    def test_country_restricted_remote_jobs_still_need_to_match(self):
        self.add("unrestricted", "Remote")
        self.add("us", "Remote, US")
        self.add("uk", "Remote, UK")
        self.assertEqual({"us"}, self.matches("USA"))

    def test_explicit_country_first_and_missing_iso_mappings_do_not_match_us(self):
        self.add("microsoft india", ["India, Karnataka, Bangalore", "India, Telangana, Hyderabad"])
        self.add("microsoft europe", ["Germany, Multiple Locations, Multiple Locations", "United Kingdom, Multiple Locations, Multiple Locations"])
        self.add("kraken", ["Romania", "Brazil", "Peru", "Cyprus", "Paraguay"])
        self.add("citi", "Pune Maharashtra India")
        self.add("us", "United States, Washington, Redmond")
        self.assertEqual({"us"}, self.matches("US"))
        self.assertEqual({"microsoft india", "citi"}, self.matches("India"))
        self.assertEqual({"microsoft europe"}, self.matches("UK"))

    def test_ny_keeps_new_york_offices_and_excludes_unknown_and_foreign_jobs(self):
        self.add("beta", ["Plattsburgh, NY, US", "Raleigh, NC, US", "South Burlington, VT, US"])
        self.add("chubb", ["Philadelphia, PA, US", "Jersey City, NJ, US", "New York, NY, US"])
        self.add("ny office", "New York Office")
        self.add("aircall", "Madrid Office")
        self.add("cummins uk", "Milton Keynes, GBR")
        self.add("cummins cz", "Trutnov, CZE")
        self.add("cummins india", "Pune, IND")
        self.add("unknown", None)
        self.add("unspecified country", "Unlisted City, Unlisted Region")
        self.assertEqual({"beta", "chubb", "ny office"}, self.matches("NY"))

    def test_iso_country_search_aliases_match_the_canonical_country(self):
        self.add("uk", "Milton Keynes, GBR")
        self.add("cz", "Trutnov, CZE")
        self.add("unknown", None)
        for query, name in [("GBR", "uk"), ("GB", "uk"), ("United Kingdom", "uk"),
                            ("CZE", "cz"), ("Czechia", "cz")]:
            with self.subTest(query=query):
                self.assertEqual({name}, self.matches(query))

    def test_country_georgia_and_us_state_georgia_are_disambiguated(self):
        self.add('atlanta', 'Atlanta, GA, US')
        self.add('tbilisi', 'GEO Tbilisi - Vake Plaza')
        self.assertEqual({'atlanta'}, self.matches('Georgia'))
        self.assertEqual({'tbilisi'}, self.matches('GEO'))
        self.assertEqual({'tbilisi'}, self.matches('GE'))

    def test_country_tokens_match_before_secondary_office_separator(self):
        self.add("multiple", ["New York, NY, US", "London, UK"])
        self.add("australia", "Sydney, Australia")
        for query in ("us", "United States", "usa", "UK", "London", "NY"):
            with self.subTest(query=query):
                self.assertEqual({"multiple"}, self.matches(query))

    def test_unusable_location_metadata_does_not_create_a_geographic_match(self):
        for index, value in enumerate((None, "[]", "null", "{}", "broken json", '["Unlisted City"]')):
            self.conn.execute("INSERT INTO jobs VALUES (?, 'Unlisted City', NULL, ?)", (str(index), value))
        self.assertEqual(set(), self.matches("New York"))

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
            ("experienced", "New York, NY, US", 1, 10),
            ("match-second", "Brooklyn, NY, US", 1, None),
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

            rows = conn.execute("SELECT id,location,locations,country_iso FROM jobs WHERE id LIKE ?", (self.prefix + "%",)).fetchall()
            names = ["id","location","locations","country_iso"]
            sync_job_places(conn, [dict(zip(names,row)) for row in rows])

    def tearDown(self):
        with connect_db() as conn:
            conn.execute("DELETE FROM jobs WHERE id LIKE ?", (self.prefix + "%",))
            conn.execute("DELETE FROM job_places WHERE job_id LIKE ?", (self.prefix + "%",))

    def test_get_and_post_filter_before_pagination_and_respect_other_filters(self):
        filters = {"company": "Location Test Co", "location": "New York", "experience": "2", "limit": 1}
        first = self.client.get("/api/jobs", query_string=filters)
        self.assertEqual(200, first.status_code)
        page = first.get_json()
        self.assertEqual([self.prefix + "match-second"], [j["id"] for j in page["jobs"]])
        self.assertTrue(page["has_more"])
        second = self.client.post("/api/jobs", json={**filters, "page": 2})
        self.assertEqual(200, second.status_code)
        page = second.get_json()
        self.assertEqual([self.prefix + "match"], [j["id"] for j in page["jobs"]])
        self.assertTrue(page["has_more"])
        self.assertFalse(page["total_exact"])
        self.assertEqual(3, page["total"])
        final = self.client.get('/api/jobs', query_string={**filters, 'page': 3}).get_json()
        self.assertEqual([], final['jobs'])
        self.assertEqual(2, final['total'])
        self.assertTrue(final['total_exact'])
        no_keyword_match = self.client.get("/api/jobs", query_string={**filters, "keyword": "zznomatchingtitlezz"})
        self.assertEqual([], no_keyword_match.get_json()["jobs"])

    def test_saved_search_uses_the_same_verified_place_rule(self):
        jobs = main._saved_search_alert_jobs(
            {"kw": "", "loc": "New York", "since": "any", "category": "All", "tag": "",
             "exp_yrs": "2", "role": "user"},
            "2026-11-02 00:00:00", "2026-11-02 23:59:59",
        )
        self.assertEqual({"Engineer match", "Engineer match-second"}, {job["title"] for job in jobs})

    def test_canonical_place_ids_survive_api_and_saved_search_serialization(self):
        expected = {self.prefix + 'match', self.prefix + 'match-second', self.prefix + 'experienced'}
        for value in ['region:US.NY', 'region:us.ny', 'city:5128581']:
            with self.subTest(value=value):
                response = self.client.get('/api/jobs',query_string={'location':value})
                self.assertEqual(200,response.status_code)
                self.assertEqual(expected,{j['id'] for j in response.get_json()['jobs']})


if __name__ == "__main__":
    unittest.main()
