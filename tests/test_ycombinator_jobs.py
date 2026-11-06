import html
import json
import unittest
from unittest.mock import patch

from app.api.ats.ycombinator import fetch_ycombinator
from app.api.geography import job_place_ids
from app.api.utils import normalize_locations


def response(props):
    class Page:
        text = '<div data-page="' + html.escape(json.dumps({"props": props}), quote=True) + '"></div>'

        def raise_for_status(self):
            pass
    return Page()


class YCombinatorJobsTests(unittest.TestCase):
    def setUp(self):
        pacing = patch("app.api.ats.ycombinator._REQUEST_INTERVAL", 0)
        pacing.start()
        self.addCleanup(pacing.stop)
        self.company = {"id": 42, "slug": "example", "name": "Example", "location": "New York, NY"}
        self.job = {"id": 7, "title": "Software Engineer", "companyName": "Example",
                    "companyUrl": "/companies/example", "url": "/companies/example/jobs/AbC1234-software-engineer",
                    "applyUrl": "https://account.ycombinator.com/authenticate?continue=application",
                    "location": "Toronto, ON, CA / Remote (US; CA)", "minExperience": "2+ years",
                    "type": "Full-time", "createdAt": "2 weeks", "lastActive": "3 days"}

    @patch("app.api.ats.ycombinator.requests.get")
    def test_uses_public_job_links_and_all_advertised_places(self, get):
        get.return_value = response({"company": self.company, "jobPostings": [self.job],
                                     "relatedJobs": [{**self.job, "id": 8, "companyName": "Other"}]})
        jobs, error = fetch_ycombinator({"yc_slug": "example", "fetch_experience_details": False}, {}, "Example")
        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual("https://www.ycombinator.com/companies/example/jobs/AbC1234-software-engineer", jobs[0]["url"])
        self.assertNotIn("authenticate", jobs[0]["url"])
        ids = job_place_ids(normalize_locations(jobs[0]["locations_raw"]))
        self.assertTrue({"country:US", "country:CA", "region:CA.08", "workplace:remote"} <= ids)
        self.assertNotIn("region:US.NY", ids)
        self.assertEqual(2, jobs[0]["exp_min"])
        self.assertEqual("provider", jobs[0]["exp_source"])
        self.assertNotIn("posted_at", jobs[0])

    @patch("app.api.ats.ycombinator.requests.get")
    def test_company_headquarters_never_fills_missing_job_location(self, get):
        get.return_value = response({"company": self.company, "jobPostings": [{**self.job, "location": ""}]})
        jobs, error = fetch_ycombinator({"yc_slug": "example", "fetch_experience_details": False}, {}, "Example")
        self.assertIsNone(error)
        self.assertEqual([], jobs[0]["locations_raw"])
        self.assertNotIn("region:US.NY", job_place_ids(normalize_locations(jobs[0]["locations_raw"])))

    @patch("app.api.ats.ycombinator.requests.get")
    def test_unrelated_employers_or_urls_produce_an_error(self, get):
        for override in ({"companyName": "Other"}, {"companyUrl": "/companies/other"},
                         {"url": "https://example.com/job/7"}, {"url": "/companies/other/jobs/AbC1234-engineer"},
                         {"url": "/companies/example/jobs/../other"}):
            with self.subTest(override=override):
                get.return_value = response({"company": self.company, "jobPostings": [{**self.job, **override}]})
                jobs, error = fetch_ycombinator({"yc_slug": "example", "fetch_experience_details": False}, {}, "Example")
                self.assertFalse(jobs)
                self.assertTrue(error)

    @patch("app.api.ats.ycombinator.requests.get")
    def test_malformed_page_cannot_be_mistaken_for_an_empty_board(self, get):
        for props in ({"company": self.company}, {"company": {**self.company, "slug": "other"}, "jobPostings": []}):
            get.return_value = response(props)
            jobs, error = fetch_ycombinator({"yc_slug": "example"}, {}, "Example")
            self.assertFalse(jobs)
            self.assertTrue(error)

    @patch("app.api.ats.ycombinator.requests.get")
    def test_confirmed_empty_list_and_stable_employer_id(self, get):
        get.return_value = response({"company": {**self.company, "name": "Renamed Example"}, "jobPostings": []})
        jobs, error = fetch_ycombinator({"yc_slug": "example", "yc_company_id": 42}, {}, "Example")
        self.assertEqual([], jobs)
        self.assertIsNone(error)
        _, error = fetch_ycombinator({"yc_slug": "example", "yc_company_id": 99}, {}, "Example")
        self.assertTrue(error)

    @patch("app.api.ats.ycombinator.requests.get")
    def test_detail_description_requires_the_same_job_and_employer(self, get):
        get.side_effect = [response({"company": self.company, "jobPostings": [self.job]}),
                           response({"company": self.company, "job": {**self.job, "description": "Build reliable systems."}})]
        jobs, error = fetch_ycombinator({"yc_slug": "example"}, {}, "Example")
        self.assertIsNone(error)
        self.assertEqual("Build reliable systems.", jobs[0]["description_raw"])
        get.side_effect = [response({"company": self.company, "jobPostings": [self.job]}),
                           response({"company": self.company, "job": {**self.job, "id": 99, "description": "Other posting"}})]
        jobs, _ = fetch_ycombinator({"yc_slug": "example"}, {}, "Example")
        self.assertNotIn("description_raw", jobs[0])

    @patch("app.api.ats.ycombinator.requests.get")
    def test_cached_description_and_duplicate_postings_do_not_refetch_details(self, get):
        get.return_value = response({"company": self.company, "jobPostings": [self.job, self.job]})
        from app.api.utils import make_id
        job_id = make_id("Example", "https://www.ycombinator.com" + self.job["url"], self.job["title"], "ycombinator", "7")
        jobs, error = fetch_ycombinator({"yc_slug": "example"}, {job_id: {"has_description": True}}, "Example")
        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual(1, get.call_count)


if __name__ == "__main__":
    unittest.main()
