import json
import unittest
from unittest.mock import patch

from app.api.ats.bamboohr import fetch_bamboohr
from app.api.ats.detector import _parse_url
from app.api.ats.greenhouse import fetch_greenhouse
from app.api.ats.playwright import fetch_phenom
from app.api.ats.remote_ats import fetch_remote_ats
from scripts.audit_job_links import classify_response, structural_issue, url_family


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data

    def iter_content(self, _size):
        yield json.dumps(self.data).encode()


class SourceHealthRegressionTests(unittest.TestCase):
    @patch("app.api.ats.remote_ats.requests.get")
    def test_remote_ats_uses_public_identity_and_excludes_closed_applications(self, get):
        posting = {
            "public_id": "a0b3d7b3-7a31-4a69-b8a4-cc6a455af46f", "title": "Payroll Specialist",
            "status": "open", "accepting_applications": True, "description": "-",
            "requirements": "Payroll experience", "opened_at": "2026-10-19T19:24:17Z",
        }
        get.return_value = Response({"jobs": [posting, {**posting, "status": "closed"},
                                             {**posting, "accepting_applications": False}]})
        jobs, error = fetch_remote_ats({}, {}, "Remote")
        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("https://apply.remote.com/jobs/" + posting["public_id"], jobs[0]["url"])
        self.assertEqual("Payroll experience", jobs[0]["description"])
        self.assertEqual(posting["opened_at"], jobs[0]["posted_at"])

    @patch("app.api.ats.remote_ats.requests.get")
    def test_remote_ats_schema_errors_cannot_look_like_an_empty_board(self, get):
        get.return_value = Response({"message": "temporarily unavailable"})
        jobs, error = fetch_remote_ats({}, {}, "Remote")
        self.assertEqual([], jobs)
        self.assertTrue(error)

    @patch("app.api.ats.bamboohr.requests.get")
    def test_bamboohr_uses_current_public_posting_route(self, get):
        get.return_value = Response({"result": [{"id": 271, "jobOpeningName": "Salesforce Developer"}]})
        jobs, error = fetch_bamboohr({"bamboohr_slug": "fullbay"}, {}, "Fullbay")
        self.assertIsNone(error)
        self.assertEqual("https://fullbay.bamboohr.com/careers/271", jobs[0]["url"])

    @patch("app.api.ats.greenhouse.requests.get")
    def test_greenhouse_can_use_hosted_pages_when_custom_routes_break(self, get):
        get.return_value = Response({"jobs": [{
            "id": 8148720, "title": "ABM Manager",
            "absolute_url": "https://jobs.elastic.co/jobs?gh_jid=8148720&gh_jid=8148720",
        }]})
        jobs, error = fetch_greenhouse({"board_token": "elastic", "use_hosted_job_urls": True}, {}, "Elastic")
        self.assertIsNone(error)
        self.assertEqual("https://job-boards.greenhouse.io/elastic/jobs/8148720", jobs[0]["url"])
        self.assertEqual("8148720", jobs[0]["ats_id"])

    @patch("app.api.ats.greenhouse.requests.get")
    def test_greenhouse_embedded_application_avoids_broken_custom_redirect(self, get):
        get.return_value = Response({"jobs": [{
            "id": 8425540002, "title": "Senior Business Analyst - US Payments",
            "absolute_url": "https://www.form3.tech/careers/vacancies/8425540002?gh_jid=8425540002",
        }]})
        jobs, error = fetch_greenhouse({"board_token": "form3", "use_embedded_job_urls": True}, {}, "Form3")
        self.assertIsNone(error)
        self.assertEqual("https://job-boards.greenhouse.io/embed/job_app?for=form3&token=8425540002", jobs[0]["url"])

    def test_ats_detection_strips_json_quote_escapes_from_board_names(self):
        self.assertEqual({"mode": "ashby", "ashby_slug": "amplitude"},
                         _parse_url("https://jobs.ashbyhq.com/amplitude\\\\"))
        self.assertEqual({"mode": "workday", "list_url": "https://ally.wd1.myworkdayjobs.com/Ally"},
                         _parse_url("https://ally.wd1.myworkdayjobs.com/Ally\\\\\\"))

    @patch("app.api.ats.playwright.time.sleep", return_value=None)
    @patch("app.api.ats.playwright.requests.get")
    def test_phenom_decodes_json_without_a_following_phapp_statement_and_flags_caps(self, get, _sleep):
        response = Response({})
        response.text = "phApp.ddo = " + json.dumps({
            "eagerLoadRefineSearch": {"totalHits": 20, "data": {
                "jobs": [{"title": "Platform Engineer", "reqId": "123"}],
            }},
        }) + "; anotherFunction();"
        get.return_value = response
        jobs, error = fetch_phenom({
            "list_url": "https://careers.example/search-results", "hard_max_pages": 1,
            "allow_browser_fallback": False,
        }, {}, "Example")
        self.assertEqual("https://careers.example/job/123", jobs[0]["url"])
        self.assertEqual("partial Phenom listing: fetched 1 of 20 jobs", error)

    def test_http_audit_does_not_treat_challenges_or_spa_shells_as_verified_jobs(self):
        url = "https://careers.example/jobs/123"
        self.assertEqual("inconclusive_http", classify_response(url, url, 403, "Forbidden", "", "Engineer"))
        self.assertEqual("reachable_unverified", classify_response(url, url, 200, "Careers", "Enable JavaScript", "Engineer"))
        self.assertEqual("redirected_to_landing", classify_response(url, "https://careers.example/company", 200, "About us", "", "Engineer"))
        self.assertEqual("verified_job_title", classify_response(url, url, 200, "Platform Engineer @ Example", "", "Platform Engineer"))
        self.assertEqual("broken_page", classify_response(url, url, 200, "Job not found", "", "Engineer"))

    @patch("app.api.ats.playwright.requests.get")
    def test_phenom_uses_public_posting_ids_when_requisition_ids_are_shared(self, get):
        response = Response({})
        response.text = "phApp.ddo = " + json.dumps({
            "eagerLoadRefineSearch": {"totalHits": 2, "data": {"jobs": [
                {"title": "Engineer France", "reqId": "shared", "jobId": "53800"},
                {"title": "Engineer Germany", "reqId": "shared", "jobId": "53801"},
            ]}},
        }) + ";"
        get.return_value = response
        jobs, error = fetch_phenom({
            "list_url": "https://careers.example/search-results", "job_url_id_field": "jobId",
            "allow_browser_fallback": False,
        }, {}, "Example")
        self.assertIsNone(error)
        self.assertEqual(["https://careers.example/job/53800", "https://careers.example/job/53801"],
                         [job["url"] for job in jobs])

    def test_url_inventory_flags_generic_links_and_groups_hosted_postings(self):
        self.assertEqual("generic_landing_url", structural_issue({"url": "https://example.com/careers"}))
        self.assertIsNone(structural_issue({"url": "https://example.com/careers?gh_jid=123", "title": "Engineer"}))
        self.assertEqual(url_family("https://jobs.ashbyhq.com/employer/uuid-one"),
                         url_family("https://jobs.ashbyhq.com/employer/uuid-two"))


if __name__ == "__main__":
    unittest.main()
