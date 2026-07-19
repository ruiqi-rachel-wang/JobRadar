import unittest
from unittest.mock import patch

from app.api.ats.playwright import _fill_detail_titles, _fetch_reader_markdown


class _Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class ReaderPaginationTests(unittest.TestCase):
    @patch("app.api.ats.playwright.time.sleep", return_value=None)
    @patch("app.api.ats.playwright.requests.get")
    def test_query_pages_are_combined_and_stop_after_empty_pages(self, get, _sleep):
        def response(url, **_kwargs):
            if "page=1" in url:
                return _Response("[First Role](https://careers.example/jobs/101-first-role/)")
            if "page=2" in url:
                return _Response("[Second Role](https://careers.example/jobs/202-second-role/)")
            return _Response("No more jobs")

        get.side_effect = response
        jobs, error = _fetch_reader_markdown(
            {
                "list_url": "https://careers.example/search/jobs/",
                "include": "careers.example/jobs/",
                "reader_paginate": True,
                "pagination": {
                    "type": "query_page",
                    "param": "page",
                    "start": 1,
                    "hard_max_pages": 10,
                },
            },
            {},
            "Example",
            "playwright",
        )

        self.assertIsNone(error)
        self.assertEqual({"First Role", "Second Role"}, {job["title"] for job in jobs})
        self.assertEqual(4, get.call_count)

    @patch("app.api.ats.playwright.requests.get")
    def test_incomplete_reported_total_returns_error_to_protect_full_scan(self, get):
        get.return_value = _Response(
            "Showing 1-1 of 3 results\n"
            "[Only Role](https://careers.example/jobs/101-only-role/)"
        )

        jobs, error = _fetch_reader_markdown(
            {
                "list_url": "https://careers.example/search/jobs/",
                "include": "careers.example/jobs/",
            },
            {},
            "Example",
            "playwright",
        )

        self.assertEqual(1, len(jobs))
        self.assertEqual("partial listing: fetched 1 of 3 jobs", error)

    @patch("app.api.ats.playwright.requests.get")
    def test_reader_applies_source_title_regex_and_drops_cta_links(self, get):
        get.return_value = _Response(
            "[PREFIXSolutions Engineer--LOCATION]"
            "(https://careers.example/jobs/101/)\n"
            "[See details](https://careers.example/jobs/102/)"
        )

        jobs, error = _fetch_reader_markdown(
            {
                "list_url": "https://careers.example/search/jobs/",
                "include": "careers.example/jobs/",
                "title_regex": r"^PREFIX(.+?)--LOCATION$",
            },
            {},
            "Example",
            "playwright",
        )

        self.assertIsNone(error)
        self.assertEqual(["Solutions Engineer"], [job["title"] for job in jobs])

    @patch("app.api.ats.playwright.requests.get")
    def test_detail_page_title_replaces_concatenated_card_label(self, get):
        get.return_value = _Response(
            "<html><head><title>Solutions Engineer | Example Careers</title></head>"
            "<body></body></html>"
        )
        jobs = [{
            "title": "ENGINEERINGSolutions EngineerBoston, United StatesSee more",
            "url": "https://careers.example/jobs/101/",
        }]

        _fill_detail_titles(jobs, {})

        self.assertEqual("Solutions Engineer", jobs[0]["title"])


if __name__ == "__main__":
    unittest.main()
