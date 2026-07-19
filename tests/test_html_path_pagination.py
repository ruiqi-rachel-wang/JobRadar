import unittest
import json
from unittest.mock import patch

from app.api.ats.html import fetch_html
from app.api.utils import extract_location_from_text


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class HtmlPathPaginationTests(unittest.TestCase):
    def test_abbvie_metadata_does_not_leak_into_location(self):
        text = (
            "Location Waco, TX Travel No Location Waco "
            "Work location type On-Site Brands AbbVie Job Type Full-time "
            "Function Operations Experience Level Entry Level Description This is our role"
        )

        self.assertEqual("Waco, TX, US", extract_location_from_text(text))

    @patch("app.api.ats.html.requests.get")
    def test_job_path_filter_rejects_navigation_links(self, get):
        get.return_value = _Response(
            "<a href='/privacy-policy'>Privacy policy</a>"
            "<a href='/job/operator-in-waco-tx-jid-123'>"
            "Operator Location Waco, TX Travel No Work location type On-Site "
            "Brands AbbVie Job Type Full-time Description Make medicine</a>"
        )

        jobs, error = fetch_html(
            {
                "list_url": "https://careers.abbvie.com/jobs",
                "include": "careers.abbvie.com",
                "require_job_path": True,
            },
            {},
            "AbbVie",
        )

        self.assertIsNone(error)
        self.assertEqual(["Operator"], [job["title"] for job in jobs])
        self.assertEqual(["Waco, TX, US"], [job["location"] for job in jobs])

    @patch("app.api.ats.html.requests.get")
    def test_job_path_filter_rejects_detail_cta_titles(self, get):
        get.return_value = _Response(
            "<a href='/jobs/101'>Read more</a>"
            "<a href='/jobs/102'>See details</a>"
            "<a href='/jobs/103'>Platform Engineer</a>"
        )

        jobs, error = fetch_html(
            {
                "list_url": "https://careers.example/jobs",
                "include": "careers.example/jobs/",
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(["Platform Engineer"], [job["title"] for job in jobs])

    @patch("app.api.ats.html.time.sleep", return_value=None)
    @patch("app.api.ats.html.requests.get")
    def test_path_pages_are_combined_until_empty(self, get, _sleep):
        def response(url, **_kwargs):
            if url.endswith("/jobs"):
                return _Response("<a href='/first/job/P25-100-1'>First Role</a>")
            if url.endswith("/jobs/page/2/"):
                return _Response("<a href='/second/job/P25-200-1'>Second Role</a>")
            return _Response("<p>No jobs</p>")

        get.side_effect = response
        jobs, error = fetch_html(
            {
                "list_url": "https://careers.example/jobs",
                "include": "careers.example/",
                "require_job_path": True,
                "pagination": {
                    "type": "path_page",
                    "segment": "page",
                    "start": 1,
                    "hard_max_pages": 10,
                },
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual({"First Role", "Second Role"}, {job["title"] for job in jobs})
        self.assertEqual(3, get.call_count)

    @patch("app.api.ats.html.time.sleep", return_value=None)
    @patch("app.api.ats.html.requests.get")
    def test_query_pages_honor_delay_and_clean_title_suffix(self, get, sleep):
        def response(url, **_kwargs):
            if url.endswith("page=1"):
                return _Response(
                    "<a href='/BU/External/jobs/316939'>"
                    "ACCOUNTS COORDINATOR BOSTON, MA</a>"
                )
            return _Response("<p>No jobs</p>")

        get.side_effect = response
        jobs, error = fetch_html(
            {
                "list_url": "https://jobs.silkroad.com/BU/External",
                "include": "jobs.silkroad.com/BU/External/jobs/",
                "require_job_path": True,
                "title_suffix_regex": r"\s+BOSTON, MA\s*$",
                "page_delay_s": 2.5,
                "pagination": {
                    "type": "query_page",
                    "param": "page",
                    "start": 1,
                    "hard_max_pages": 10,
                },
            },
            {},
            "Boston University",
        )

        self.assertIsNone(error)
        self.assertEqual(["ACCOUNTS COORDINATOR"], [job["title"] for job in jobs])
        sleep.assert_called_once_with(2.5)

    @patch("app.api.ats.html.requests.get")
    def test_embedded_next_jobs_are_decoded_without_browser(self, get):
        flight = (
            '3f:[{"attributes":'
            + json.dumps({
                "ashby_id": "abc-123",
                "title": "Platform Engineer",
                "location_name": "Remote - US",
                "employment_type": "Full-time",
                "is_listed": True,
                "ashby_published_date": "2026-07-13T12:00:00Z",
                "description": "Build the platform.",
                "slug": "/careers/position/?ashby_jid=abc-123",
            })
            + '}]'
        )
        payload = json.dumps([1, flight])
        get.return_value = _Response(
            "<p>1 open position</p>"
            f"<script>self.__next_f.push({payload})</script>"
        )

        jobs, error = fetch_html(
            {
                "list_url": "https://www.example.com/careers/",
                "include": "example.com/careers/position/",
                "embedded_next_jobs": True,
                "verify_reported_total": True,
            },
            {},
            "Example",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Platform Engineer", jobs[0]["title"])
        self.assertEqual("Remote, US", jobs[0]["location"])
        self.assertEqual("Full-time", jobs[0]["employment_type"])


if __name__ == "__main__":
    unittest.main()
