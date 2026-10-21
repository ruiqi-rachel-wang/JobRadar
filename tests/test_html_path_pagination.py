import unittest
import json
from unittest.mock import patch

from app.api.ats.html import fetch_html
from app.api.utils import extract_location_from_text, normalize_location


class _Response:
    def __init__(self, text, url=None):
        self.text = text
        self.url = url

    def raise_for_status(self):
        return None


class HtmlPathPaginationTests(unittest.TestCase):
    @patch("app.api.ats.html.requests.get")
    def test_card_attributes_preserve_location_without_adjacent_card_text(self, get):
        get.return_value = _Response(
            '<a href="/job-opening/engineer/" data-gtm-jn="Senior Engineer" '
            'data-gtm-jl="Lakeland, FL, US" data-gtm-jdt="Oct 20, 2026">'
            'Senior Engineer Finance Hybrid Department Apply Now</a>'
            '<a href="/jobs/?pg=2">Next page</a>'
        )
        jobs, error = fetch_html({
            "list_url": "https://careers.example/jobs/",
            "include": "https://careers.example/job-opening/",
            "title_attribute": "data-gtm-jn", "location_attribute": "data-gtm-jl",
            "posted_attribute": "data-gtm-jdt",
        }, {}, "Example")
        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Senior Engineer", jobs[0]["title"])
        self.assertEqual(["Lakeland, FL, US"], jobs[0]["locations_raw"])
        self.assertEqual("Oct 20, 2026", jobs[0]["posted"])

    @patch("app.api.ats.html.requests.get")
    def test_apply_buttons_do_not_create_duplicate_jobs(self, get):
        get.return_value = _Response(
            '<a href="/jobs/123">Apply Now</a>'
            '<a href="/jobs/456">Software Engineer</a>'
        )
        jobs, error = fetch_html({
            "list_url": "https://careers.example/", "include": "careers.example/jobs/",
        }, {}, "Example")
        self.assertIsNone(error)
        self.assertEqual(["Software Engineer"], [job["title"] for job in jobs])

    @patch("app.api.ats.html.time.sleep", return_value=None)
    @patch("app.api.ats.html.requests.get")
    def test_redirected_document_is_base_for_jobs_and_next_page(self, get, _sleep):
        get.side_effect = [
            _Response(
                "<a href='/company/careers/details/123'>Platform Engineer</a>"
                "<a rel='next' href='?page=2'>Next</a>",
                "https://careers.example/company/careers/all-jobs",
            ),
            _Response(
                "<a href='/company/careers/details/456'>Data Engineer</a>",
                "https://careers.example/company/careers/all-jobs?page=2",
            ),
        ]

        jobs, error = fetch_html({
            "list_url": "https://old.example/careers",
            "include": "https://careers.example/company/careers/details/",
            "pagination": {"type": "next_link"},
        }, {}, "Example")

        self.assertIsNone(error)
        self.assertEqual([
            "https://careers.example/company/careers/details/123",
            "https://careers.example/company/careers/details/456",
        ], [job["url"] for job in jobs])
        self.assertEqual(
            "https://careers.example/company/careers/all-jobs?page=2",
            get.call_args_list[1].args[0],
        )

    @patch("app.api.ats.html.fetch_detail_description", return_value={})
    @patch("app.api.ats.html.requests.get")
    def test_loom_config_keeps_only_loom_postings_before_fetching_details(self, get, detail):
        from pathlib import Path
        companies = json.loads((Path(__file__).parents[1] / "app/companies.json").read_text())
        scrape = next(c["scrape"] for c in companies if c["name"] == "Loom")
        get.return_value = _Response(
            "<a href='/company/careers/details/26575'>Principal Data Scientist, Loom</a>"
            "<a href='/company/careers/details/27431'>Software Engineer, LOOM Billing</a>"
            "<a href='/company/careers/details/24872'>Senior Director, Jira Design</a>"
            "<a href='/software/loom'>Loom Quick, async video updates</a>",
            scrape["list_url"],
        )

        jobs, error = fetch_html({**scrape, "fetch_experience_details": True}, {}, "Loom")

        self.assertIsNone(error)
        self.assertEqual([
            "https://www.atlassian.com/company/careers/details/26575",
            "https://www.atlassian.com/company/careers/details/27431",
        ], [job["url"] for job in jobs])
        self.assertEqual(2, detail.call_count)

    @patch("app.api.ats.html.requests.get")
    def test_gem_next_payload_jobs_are_decoded(self, get):
        postings = [{
            "id": "4003629005",
            "title": "Software Engineer",
            "location": "San Francisco, United States",
            "department": "Engineering",
            "link": "https://jobs.gem.com/retool/4003629005",
            "updatedAt": "2026-03-18T17:43:11.448Z",
        }]
        flight_chunk = json.dumps({"jobs": postings}, separators=(",", ":"))
        get.return_value = _Response(
            "<script>self.__next_f.push(" + json.dumps([1, flight_chunk]) + ")</script>"
        )

        jobs, error = fetch_html(
            {
                "list_url": "https://retool.com/careers",
                "include": "jobs.gem.com/retool/",
                "embedded_gem_jobs": True,
            },
            {},
            "Retool",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Software Engineer", jobs[0]["title"])
        self.assertEqual("4003629005", jobs[0]["ats_id"])
        self.assertEqual("San Francisco, CA, US", jobs[0]["location"])

    @patch("app.api.ats.html.requests.get")
    def test_kula_next_payload_jobs_are_decoded(self, get):
        postings = [{
            "id": 51437,
            "title": "Senior Scientist",
            "listed": True,
            "launch_at": "2026-06-22T18:44:32.000Z",
            "account_id": 1514,
            "ats_job": {
                "employment_type": "full_time",
                "ats_department": {"name": "Chemistry"},
                "offices": [{"location": "Pleasanton, California, United States"}],
            },
        }]
        flight_chunk = json.dumps({"jobs": postings}, separators=(",", ":"))
        get.return_value = _Response(
            "<script>self.__next_f.push(" + json.dumps([1, flight_chunk]) + ")</script>"
        )

        jobs, error = fetch_html(
            {
                "list_url": "https://careers.kula.ai/10xgenomics",
                "include": "careers.kula.ai/10xgenomics/",
                "embedded_kula_jobs": True,
            },
            {},
            "10x Genomics",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("Senior Scientist", jobs[0]["title"])
        self.assertEqual("51437", jobs[0]["ats_id"])
        self.assertEqual("Pleasanton, CA, US", jobs[0]["location"])

    def test_indian_city_region_pairs_include_country(self):
        self.assertEqual(
            "Noida, Uttar Pradesh, India",
            normalize_location("Noida, Uttar Pradesh"),
        )
        self.assertEqual(
            "Hyderabad, Telangana, India",
            normalize_location("Hyderabad, Telangana"),
        )

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

    @patch("app.api.ats.html.requests.get")
    def test_configured_location_regex_stops_department_leak(self, get):
        get.return_value = _Response(
            "<div class='irongforce-job-listing'>"
            "<h2><a href='/jobs/fanduel/software-engineer/'>Software Engineer</a></h2>"
            "<p>Build reliable services.</p>"
            "<p>Location: Atlanta, New York City</p>"
            "<p>Department: Software Engineering</p>"
            "</div>"
        )

        jobs, error = fetch_html(
            {
                "list_url": "https://www.fanduel.careers/open-positions/",
                "include": "fanduel.careers/jobs/fanduel/",
                "location_regex": r"Location:\s*(.+?)\s+Department:",
            },
            {},
            "FanDuel",
        )

        self.assertIsNone(error)
        self.assertEqual("Atlanta, New York City", jobs[0]["location"])
        self.assertNotIn("Department", jobs[0]["location"])

    @patch("app.api.ats.html.requests.get")
    def test_unitedhealth_card_separates_title_requisition_and_location(self, get):
        get.return_value = _Response(
            "<div class='job-card'>"
            "<a href='/job/noida/devops-engineer/34088/98726908256'>"
            "DevOps Engineer 2375311 | Noida, Uttar Pradesh | Optum"
            "</a>"
            "</div>"
            "<div class='job-card'>"
            "<a href='/job/sumter/certified-medical-assistant-colonial-healthcare/34088/98803641008'>"
            "98803641008"
            "</a>"
            "</div>"
        )

        jobs, error = fetch_html(
            {
                "list_url": "https://careers.unitedhealthgroup.com/search-jobs",
                "include": "/job/",
                "title_suffix_regex": r"\s+\d+\s*\|.*$",
                "location_regex": r"\|\s*([^|]+?)(?:\s*\||$)",
                "ats_id_regex": r"/(\d+)$",
            },
            {},
            "UnitedHealth Group",
        )

        self.assertIsNone(error)
        self.assertEqual("DevOps Engineer", jobs[0]["title"])
        self.assertEqual("Noida, Uttar Pradesh, India", jobs[0]["location"])
        self.assertEqual("98726908256", jobs[0]["ats_id"])
        self.assertEqual(
            "certified medical assistant colonial healthcare",
            jobs[1]["title"],
        )
        self.assertEqual("98803641008", jobs[1]["ats_id"])

    @patch("app.api.ats.html.requests.get")
    def test_reader_uses_structured_markdown_location_bullet(self, get):
        get.return_value = _Response(
            "### [Software Engineer II](https://careers.etsy.com/jobs/software-engineer-ii)\n"
            "\n"
            "* Hybrid\n"
            "\n"
            "* Brooklyn, New York, United States\n"
            "\n"
            "* Software Engineer\n"
            "\n"
            "* Full-time\n"
        )

        jobs, error = fetch_html(
            {
                "list_url": "https://careers.etsy.com/jobs/search",
                "include": "careers.etsy.com/jobs/",
                "reader": True,
                "url_exclude": ["careers.etsy.com/jobs/search"],
            },
            {},
            "Etsy",
        )

        self.assertIsNone(error)
        self.assertEqual(["Software Engineer II"], [job["title"] for job in jobs])
        self.assertEqual(["Brooklyn, NY, US"], [job["location"] for job in jobs])

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
    def test_deel_uses_published_external_link_instead_of_stale_slug(self, get):
        from pathlib import Path
        companies = json.loads((Path(__file__).parents[1] / "app/companies.json").read_text())
        scrape = next(c["scrape"] for c in companies if c["name"] == "Deel")
        posting = {
            "title": "Devsecops Engineer",
            "slug": "/careers/position/?ashby_jid=abc-123",
            "external_link": "https://jobs.deel.com/deel/job-details/abc-123/application",
            "is_listed": True,
        }
        hidden = {**posting, "title": "Hidden Engineer", "is_listed": False}
        flight = '3f:' + json.dumps([
            {"attributes": posting}, {"attributes": hidden}
        ], separators=(",", ":"))
        get.return_value = _Response(
            "<p>1 open position</p><script>self.__next_f.push("
            + json.dumps([1, flight]) + ")</script>"
        )

        jobs, error = fetch_html(scrape, {}, "Deel")

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual(posting["external_link"], jobs[0]["url"])
        self.assertEqual(posting["external_link"], jobs[0]["apply_url"])

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
