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
    def test_parallel_reader_fetches_only_pages_implied_by_total(self, get):
        def response(url, **_kwargs):
            if "page=1" in url:
                return _Response(
                    "Showing 1-1 of 3 results\n"
                    "[First Role](https://careers.example/jobs/101-first-role/)"
                )
            if "page=2" in url:
                return _Response(
                    "[Second Role](https://careers.example/jobs/202-second-role/)"
                )
            return _Response(
                "[Third Role](https://careers.example/jobs/303-third-role/)"
            )

        get.side_effect = response
        jobs, error = _fetch_reader_markdown(
            {
                "list_url": "https://careers.example/search/jobs/",
                "include": "careers.example/jobs/",
                "reader_paginate": True,
                "reader_parallel_pages": True,
                "pagination": {
                    "type": "query_page",
                    "param": "page",
                    "start": 1,
                    "hard_max_pages": 20,
                },
            },
            {},
            "Example",
            "playwright",
        )

        self.assertIsNone(error)
        self.assertEqual(
            {"First Role", "Second Role", "Third Role"},
            {job["title"] for job in jobs},
        )
        self.assertEqual(3, get.call_count)

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
        self.assertEqual(
            "partial listing: fetched 1 of 3 result rows",
            error,
        )

    @patch("app.api.ats.playwright.requests.get")
    def test_duplicate_location_rows_merge_without_false_partial_error(self, get):
        get.return_value = _Response(
            "Showing 1-3 of 3 results\n"
            "[Claims Adjuster](https://careers.example/jobs/101-adjuster/) "
            "Location: Clive, IA Date Posted: Jul 29, 2026\n"
            "[Claims Adjuster](https://careers.example/jobs/101-adjuster/) "
            "Location: Des Moines, IA Date Posted: Jul 29, 2026\n"
            "[Data Analyst](https://careers.example/jobs/202-analyst/) "
            "Location: Cleveland, OH Date Posted: Jul 29, 2026"
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

        self.assertIsNone(error)
        self.assertEqual(2, len(jobs))
        adjuster = next(job for job in jobs if job["title"] == "Claims Adjuster")
        self.assertEqual(
            ["Clive, IA, US", "Des Moines, IA, US"],
            adjuster["locations_raw"],
        )

    @patch("app.api.ats.playwright.requests.get")
    def test_seen_page_early_stop_is_not_reported_as_partial(self, get):
        get.return_value = _Response(
            "Showing 1-1 of 50 results\n"
            "[Known Role](https://careers.example/jobs/101-known-role/)"
        )
        seen = {
            # The helper uses the legacy company+URL+title identity here.
            __import__(
                "app.api.utils",
                fromlist=["make_id"],
            ).make_id(
                "Example",
                "https://careers.example/jobs/101-known-role/",
                "Known Role",
            ): {},
        }

        jobs, error = _fetch_reader_markdown(
            {
                "list_url": "https://careers.example/search/jobs/",
                "include": "careers.example/jobs/",
                "stop_on_seen": True,
            },
            seen,
            "Example",
            "playwright",
        )

        self.assertEqual(1, len(jobs))
        self.assertIsNone(error)

    @patch("app.api.ats.playwright.time.sleep", return_value=None)
    @patch("app.api.ats.playwright.requests.get")
    def test_incomplete_parallel_snapshot_retries_sequentially(self, get, _sleep):
        calls = {}

        def response(url, **_kwargs):
            calls[url] = calls.get(url, 0) + 1
            if "page=1" in url:
                return _Response(
                    "Showing 1-1 of 3 results\n"
                    "[First Role](https://careers.example/jobs/101-first-role/)"
                )
            if "page=2" in url and calls[url] == 1:
                return _Response(
                    "[First Role](https://careers.example/jobs/101-first-role/)"
                )
            if "page=2" in url:
                return _Response(
                    "[Second Role](https://careers.example/jobs/202-second-role/)"
                )
            return _Response(
                "[Third Role](https://careers.example/jobs/303-third-role/)"
            )

        get.side_effect = response
        jobs, error = _fetch_reader_markdown(
            {
                "list_url": "https://careers.example/search/jobs/",
                "include": "careers.example/jobs/",
                "reader_paginate": True,
                "reader_parallel_pages": True,
                "pagination": {
                    "type": "query_page",
                    "param": "page",
                    "start": 1,
                    "hard_max_pages": 3,
                },
            },
            {},
            "Example",
            "playwright",
        )

        self.assertIsNone(error)
        self.assertEqual(
            {"First Role", "Second Role", "Third Role"},
            {job["title"] for job in jobs},
        )

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



class ReaderPathPaginationTests(unittest.TestCase):
    @patch('app.api.ats.playwright.time.sleep')
    @patch('app.api.ats.playwright.requests.get')
    def test_path_pages_follow_advertised_last_page_without_fetching_404(self, get, sleep):
        class Response:
            def __init__(self, text): self.text = text
            def raise_for_status(self): pass
        get.side_effect = [Response('Viewing 1 of 2\n[Engineer](https://example.com/careers/details/one/)\n'
                                   '[2](https://example.com/careers/open/page/2/)'),
                           Response('Viewing 2 of 2\n[Analyst](https://example.com/careers/details/two/)')]
        jobs, error = _fetch_reader_markdown({'list_url': 'https://example.com/careers/open/',
            'include': 'example.com/careers/details/', 'require_id': False, 'reader_paginate': True,
            'pagination': {'type': 'path_page'}, 'hard_max_pages': 100}, {}, 'Example', 'playwright')
        self.assertIsNone(error)
        self.assertEqual(2, len(jobs))
        self.assertEqual(2, get.call_count)
        self.assertTrue(get.call_args.args[0].endswith('/page/2/'))

    @patch('app.api.ats.playwright.time.sleep')
    @patch('app.api.ats.playwright.requests.get')
    def test_viewing_count_exposes_repeated_partial_list(self, get, sleep):
        class Response:
            text = ('Viewing 1 of 80\n[Engineer](https://example.com/careers/details/one/)\n'
                    '[8](https://example.com/careers/open/page/8/)')
            def raise_for_status(self): pass
        get.return_value = Response()
        jobs, error = _fetch_reader_markdown({'list_url': 'https://example.com/careers/open/',
            'include': 'example.com/careers/details/', 'require_id': False, 'reader_paginate': True,
            'pagination': {'type': 'path_page'}}, {}, 'Example', 'playwright')
        self.assertEqual(1, len(jobs))
        self.assertIn('partial listing', error)


class CareerCardLocationTests(unittest.TestCase):
    @patch('app.api.ats.playwright.requests.get')
    def test_career_cards_use_their_own_complete_location_suffix(self, get):
        get.return_value = _Response('Office Locations\n- [x] Americas\n- [x] New York\n'
            '- [x] Miami\n- [x] London\nClear Selection\nViewing 2 of 2\n'
            '## [Engineer New York, London Apply Now](https://example.com/careers/details/one/)'
            '## [Analyst Miami Apply Now](https://example.com/careers/details/two/)')
        jobs, error = _fetch_reader_markdown({'list_url': 'https://example.com/careers/open/',
            'include': 'example.com/careers/details/', 'require_id': False,
            'reader_office_locations': True}, {}, 'Example', 'playwright')
        self.assertIsNone(error)
        self.assertEqual('Engineer', jobs[0]['title'])
        self.assertEqual(['New York', 'London'], jobs[0]['locations_raw'])
        self.assertEqual(['Miami'], jobs[1]['locations_raw'])

if __name__ == "__main__":
    unittest.main()
