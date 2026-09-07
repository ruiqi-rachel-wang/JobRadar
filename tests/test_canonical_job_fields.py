import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.api.apis import _enrich
from app.api.ats.lever import fetch_lever
from app.api.ats.workday import fetch_workday
from app.api.utils import (
    normalize_datetime_utc,
    normalize_description,
    normalize_locations,
)


class _Response:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None

    status_code = 200


class CanonicalJobFieldTests(unittest.TestCase):
    def test_entity_escaped_html_preserves_canonical_structure(self):
        normalized = normalize_description(
            "&lt;h2&gt;Requirements&lt;/h2&gt;"
            "&lt;ul&gt;&lt;li&gt;Five years&lt;/li&gt;"
            "&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"
        )
        self.assertIn("## Requirements", normalized)
        self.assertIn("- Five years", normalized)
        self.assertIn("- Python", normalized)
        self.assertNotIn("<h2>", normalized)

    def test_multiple_locations_are_structured_before_display(self):
        result = normalize_locations("New York, NY / San Francisco, CA")

        self.assertEqual(
            "New York, NY, US; San Francisco, CA, US",
            result["location"],
        )
        self.assertEqual(2, len(result["locations"]))
        self.assertEqual(["New York", "San Francisco"], [
            location["city"] for location in result["locations"]
        ])
        self.assertEqual("US", result["country_iso"])

    def test_remote_and_physical_location_are_both_preserved(self):
        result = normalize_locations("Singapore / Remote")

        self.assertEqual("Singapore; Remote", result["location"])
        self.assertEqual(["SG", None], [
            location["country_iso"] for location in result["locations"]
        ])
        self.assertEqual("remote", result["remote_mode"])

    def test_city_dash_country_is_not_reversed_as_state_city(self):
        result = normalize_locations(
            ["New York - United States of America"],
            remote_mode="Hybrid (Remote/Office)",
        )

        self.assertEqual("New York, NY, US", result["location"])
        self.assertEqual("US", result["country_iso"])
        self.assertEqual("hybrid", result["remote_mode"])

    def test_us_facility_address_does_not_treat_state_as_country(self):
        result = normalize_locations(
            "Main Campus - Pavilion A - 777 Bannock Street, Denver, CO 80204"
        )

        self.assertEqual("Denver, CO, US", result["location"])
        self.assertEqual("US", result["country_iso"])
        self.assertEqual("Denver", result["locations"][0]["city"])
        self.assertEqual("CO", result["locations"][0]["region"])
        self.assertEqual(
            "Main Campus - Pavilion A - 777 Bannock Street, Denver, CO 80204",
            result["locations"][0]["raw"],
        )

    def test_explicit_colombia_country_code_is_not_rewritten_as_colorado(self):
        result = normalize_locations("Bogotá, DC, CO")

        self.assertEqual("Bogotá, DC, Colombia", result["location"])
        self.assertEqual("CO", result["country_iso"])

    def test_corporate_us_city_address_and_canadian_province_are_structured(self):
        nyc = normalize_locations("NYC - 1211 Ave of the Americas")
        dc = normalize_locations("Washington D.C.")
        ontario = normalize_locations("Ontario")

        self.assertEqual("New York, NY, US", nyc["location"])
        self.assertEqual("US", nyc["country_iso"])
        self.assertEqual("Washington, DC, US", dc["location"])
        self.assertEqual("US", dc["country_iso"])
        self.assertEqual("ON, Canada", ontario["location"])
        self.assertEqual("CA", ontario["country_iso"])

    def test_enrichment_keeps_raw_values_and_uses_null_for_unknowns(self):
        jobs = [{
            "title": "Platform Engineer",
            "url": "https://example.test/jobs/42",
            "location": "Boston, MA (Hybrid)",
            "posted": "2 days ago",
            "description": "<h2>Qualifications</h2><ul><li>Python</li></ul>",
        }]

        _enrich(jobs, {}, "Example")
        job = jobs[0]

        self.assertEqual("Boston, MA (Hybrid)", job["location_raw"])
        self.assertEqual("Boston, MA, US", job["location"])
        self.assertEqual("hybrid", job["remote_mode"])
        self.assertEqual("2 days ago", job["posted_raw"])
        self.assertIsNone(job["posted_at"])
        self.assertRegex(job["posted"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIsNone(job["employment_type"])
        self.assertIsNone(job["exp_min"])
        self.assertIsNone(job["exp_label"])

    def test_full_timestamp_is_converted_to_utc_without_losing_time(self):
        self.assertEqual(
            "2026-07-25T14:30:45Z",
            normalize_datetime_utc("2026-07-25T10:30:45-04:00"),
        )
        self.assertEqual(
            "2026-07-25T14:30:45Z",
            normalize_datetime_utc(
                datetime(2026, 7, 25, 14, 30, 45, tzinfo=timezone.utc)
            ),
        )
        self.assertIsNone(normalize_datetime_utc("2 days ago"))

    def test_description_preserves_heading_and_list_boundaries(self):
        result = normalize_description(
            "<h2>Requirements</h2><ul><li>7+ years</li><li>Python</li></ul>"
        )

        self.assertEqual(
            "## Requirements\n\n- 7+ years\n- Python",
            result,
        )

    @patch("app.api.ats.lever.requests.get")
    def test_lever_list_content_is_not_split_into_characters(self, get):
        get.return_value = _Response([{
            "id": "lever-42",
            "text": "Platform Engineer",
            "hostedUrl": "https://jobs.lever.co/example/lever-42",
            "categories": {"location": "New York, NY"},
            "lists": [{
                "text": "Requirements",
                "content": "<ul><li>7+ years</li><li>Python</li></ul>",
            }],
        }])

        jobs, error = fetch_lever({"lever_slug": "example"}, {}, "Example")

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertIn("<h3>Requirements</h3>", jobs[0]["description_raw"])
        self.assertIn("<li>7+ years</li>", jobs[0]["description_raw"])
        self.assertNotIn("7\n+\n ", jobs[0]["description_raw"])

    @patch("app.api.ats.workday.requests.post")
    def test_workday_capped_result_is_subdivided_by_facets(self, post):
        def response_for(_url, **kwargs):
            payload = kwargs["json"]
            facets = payload.get("appliedFacets") or {}
            if not facets:
                return _Response({
                    "total": 2000,
                    "jobPostings": [],
                    "facets": [{
                        "facetParameter": "locations",
                        "values": [
                            {"id": "new-york", "count": 1000},
                            {"id": "california", "count": 1000},
                        ],
                    }],
                })
            location = facets["locations"][0]
            title = "New York Engineer" if location == "new-york" else "California Engineer"
            return _Response({
                "total": 1,
                "jobPostings": [{
                    "title": title,
                    "externalPath": f"/job/{location}",
                    "locationsText": location,
                    "postedOn": "2026-07-25",
                    "bulletFields": [f"REQ-{location}"],
                }],
                "facets": [],
            })

        post.side_effect = response_for
        jobs, error = fetch_workday({
            "list_url": "https://example.wd5.myworkdayjobs.com/careers",
            "fetch_experience_details": False,
            "fetch_location_details": False,
            "hard_max_pages": 200,
        }, {}, "Example")

        self.assertIsNone(error)
        self.assertEqual(
            {"New York Engineer", "California Engineer"},
            {job["title"] for job in jobs},
        )
        self.assertEqual(3, post.call_count)

    @patch("app.api.ats.workday.requests.get")
    @patch("app.api.ats.workday.requests.post")
    def test_workday_multiple_locations_fetches_and_preserves_every_city(
        self, post, get
    ):
        post.return_value = _Response({
            "total": 1,
            "jobPostings": [{
                "title": "Full Stack Software Engineer (Python)",
                "externalPath": "/job/Irving-TX/Software-Engineer_108775-1",
                "locationsText": "2 Locations",
                "postedOn": "2026-08-05",
                "bulletFields": ["108775"],
            }],
        })
        get.return_value = _Response({
            "jobPostingInfo": {
                "location": "Irving, TX",
                "jobRequisitionLocation": {
                    "descriptor": "Irving - 6011 Connection",
                    "country": {
                        "descriptor": "United States of America",
                        "alpha2Code": "US",
                    },
                },
                "additionalLocations": ["Stamford, CT"],
                "country": {"descriptor": "United States of America"},
            },
        })

        jobs, error = fetch_workday({
            "list_url": "https://gartner.wd5.myworkdayjobs.com/ext",
            "fetch_experience_details": False,
            "fetch_location_details": False,
            "hard_max_pages": 1,
        }, {}, "Gartner")
        _enrich(jobs, {}, "Gartner")

        self.assertIsNone(error)
        self.assertEqual(
            "Irving, TX, US; Stamford, CT, US",
            jobs[0]["location"],
        )
        self.assertEqual(
            ["Irving, TX, US", "Stamford, CT, US"],
            jobs[0]["locations_raw"],
        )
        self.assertEqual(2, len(jobs[0]["locations"]))
        get.assert_called_once()

    @patch("app.api.ats.workday.time.sleep", return_value=None)
    @patch("app.api.ats.workday.requests.post")
    def test_workday_retries_malformed_json_page(self, post, _sleep):
        malformed = _Response({})
        malformed.json = lambda: (_ for _ in ()).throw(
            ValueError("malformed JSON")
        )
        post.side_effect = [
            malformed,
            _Response({
                "total": 1,
                "jobPostings": [{
                    "title": "Recovered Engineer",
                    "externalPath": "/job/recovered",
                    "locationsText": "New York, NY",
                    "postedOn": "2026-07-29",
                    "bulletFields": ["REQ-1"],
                }],
            }),
        ]

        jobs, error = fetch_workday({
            "list_url": "https://example.wd5.myworkdayjobs.com/careers",
            "fetch_experience_details": False,
            "fetch_location_details": False,
        }, {}, "Example")

        self.assertIsNone(error)
        self.assertEqual(["Recovered Engineer"], [job["title"] for job in jobs])
        self.assertEqual(2, post.call_count)


if __name__ == "__main__":
    unittest.main()
