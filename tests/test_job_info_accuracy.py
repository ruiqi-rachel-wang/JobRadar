import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api.apis import _enrich, _remove_non_jobs
from app.api.ats.avature import fetch_avature
from app.api.ats.greenhouse import fetch_greenhouse
from app.api.ats.workday import fetch_workday
from app.api.utils import extract_experience


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    def __init__(self, text="", data=None, url="https://example.com"):
        self.text = text
        self._data = data
        self.url = url
        self.status_code = 200

    def json(self):
        return self._data

    def raise_for_status(self):
        return None

    def iter_content(self, _size):
        yield json.dumps(self._data).encode()

    def close(self):
        return None


class ExperienceAccuracyTests(unittest.TestCase):
    def test_point72_required_range_beats_company_history(self):
        exp = extract_experience(
            "Machine Learning Engineer, GenAI Technology",
            "What's required: 3-7 years of experience in AI/ML engineering, "
            "with a proven track record of successful project delivery. "
            "About Point72: Building on more than 30 years of investing "
            "experience, Point72 seeks to deliver superior returns.",
        )

        self.assertEqual(3, exp["min"])
        self.assertEqual(7, exp["max"])
        self.assertEqual("3-7 years", exp["label"])

    def test_disney_overall_minimum_beats_smaller_specialty_minimum(self):
        exp = extract_experience(
            "Sr Software Engineer",
            "Basic Qualifications: 5+ years related experience crafting backend "
            "architectures and delivering production applications. 3+ years of "
            "experience operating services in AWS.",
        )

        self.assertEqual(5, exp["min"])
        self.assertIsNone(exp["max"])
        self.assertEqual("5+ years", exp["label"])

    def test_jpmorgan_applied_experience_wording_is_numeric(self):
        exp = extract_experience(
            "iOS / Swift Software Engineer III",
            "Formal training or certification on software engineering concepts "
            "and 3+ years applied experience.",
        )

        self.assertEqual(3, exp["min"])
        self.assertIsNone(exp["max"])
        self.assertEqual("3+ years", exp["label"])

    def test_overall_requirement_wins_over_smaller_specialty_requirement(self):
        exp = extract_experience(
            "Staff Software Engineer, AI Tools",
            "Required: 7+ years of software engineering experience. "
            "You must also have 2+ years of experience building generative AI tools.",
        )

        self.assertEqual(7, exp["min"])
        self.assertEqual("7+ years", exp["label"])
        self.assertEqual("text", exp["source"])

    def test_titles_never_invent_numeric_years(self):
        exp = extract_experience("Staff Software Engineer", "")

        self.assertIsNone(exp["min"])
        self.assertEqual("", exp["label"])
        self.assertEqual("", exp["source"])


    def test_posting_without_numeric_years_is_not_listed(self):
        exp = extract_experience(
            "Software Development Engineer",
            "Hands-on experience building GenAI services and strong collaboration skills.",
        )

        self.assertIsNone(exp["min"])
        self.assertEqual("", exp["label"])
        self.assertEqual("text", exp["source"])

    def test_cached_company_peer_estimate_is_discarded(self):
        jobs = [{
            "title": "Portfolio Management Group Tech AI Engineer, Associate",
            "url": "https://example.com/jobs/blackrock-ai",
            "location": "Atlanta, GA",
        }]
        seen = {
            "fixed-id": {
                "exp_min": 4.75,
                "exp_max": None,
                "exp_label": "4.75+ years",
                "exp_source": "company_peer",
            }
        }

        with patch("app.api.apis.make_id", return_value="fixed-id"):
            _enrich(jobs, seen, "BlackRock", scan_type="fresh")

        self.assertIsNone(jobs[0]["exp_min"])
        self.assertIsNone(jobs[0]["exp_label"])
        self.assertIsNone(jobs[0]["exp_source"])


class NonJobPostingTests(unittest.TestCase):
    def test_generic_talent_and_future_interest_forms_are_removed(self):
        jobs = [
            {"title": "Senior Software Engineer", "url": "https://example/jobs/1"},
            {"title": "Join Our Talent Network!", "url": "https://example/jobs/2"},
            {"title": "General Talent", "url": "https://example/jobs/3"},
            {"title": "Future Opportunities", "url": "https://example/jobs/4"},
        ]

        _remove_non_jobs(jobs)

        self.assertEqual(["Senior Software Engineer"], [job["title"] for job in jobs])


class DeloitteLocationTests(unittest.TestCase):
    @patch("app.api.ats.avature.requests.get")
    def test_avature_uses_location_span_not_deloitte_legal_entity(self, get):
        listing = """
        <article class="article--result">
          <h3><a href="/en_US/careers/JobDetail/Software-Engineer/123">
            Software Engineer
          </a></h3>
          <div class="article__header__text__subtitle">
            <span>Deloitte US</span> |
            <span>Deloitte Consulting LLP</span> |
            <span>Princeton, New Jersey, United States</span>
          </div>
        </article>
        """
        get.side_effect = [
            _Response(listing, url="https://apply.deloitte.com/en_US/careers/SearchJobs"),
            _Response("<main>Short job description.</main>"),
        ]

        jobs, error = fetch_avature(
            {
                "list_url": "https://apply.deloitte.com/en_US/careers/SearchJobs",
                "hard_max_pages": 1,
            },
            {},
            "Deloitte",
        )

        self.assertIsNone(error)
        self.assertEqual("Software Engineer", jobs[0]["title"])
        self.assertEqual("Princeton, NJ, US", jobs[0]["location"])

    @patch("app.api.ats.avature.make_id", return_value="deloitte-job")
    @patch("app.api.ats.avature.requests.get")
    def test_multiple_locations_is_refreshed_even_with_cached_description(self, get, _make_id):
        listing = """
        <article class="article--result">
          <h3><a href="/en_US/careers/JobDetail/Software-Engineer/123">
            Software Engineer
          </a></h3>
          <div class="article__header__text__subtitle">
            <span>Deloitte US</span> |
            <span>Deloitte Consulting LLP</span> |
            <span>Multiple Locations</span>
          </div>
        </article>
        """
        detail = (
            "<main>Location(s) Princeton, New Jersey, United States "
            "Position Summary Build reliable services for clients.</main>"
        )
        get.side_effect = [
            _Response(listing, url="https://apply.deloitte.com/en_US/careers/SearchJobs"),
            _Response(detail),
        ]

        jobs, error = fetch_avature(
            {
                "list_url": "https://apply.deloitte.com/en_US/careers/SearchJobs",
                "hard_max_pages": 1,
            },
            {"deloitte-job": {"has_description": True}},
            "Deloitte",
        )

        self.assertIsNone(error)
        self.assertEqual("Princeton, NJ, US", jobs[0]["location"])
        self.assertEqual(2, get.call_count)


class SiemensCompletenessTests(unittest.TestCase):
    @patch("app.api.ats.avature.requests.get")
    def test_avature_follows_source_next_offset_and_uses_location_span(self, get):
        first_page = """
        <article class="article article--result">
          <h3><a href="/en_US/externaljobs/JobDetail/515700">
            Earlier Role
          </a></h3>
          <div class="article__header__text__subtitle">
            <span class="list-item-location">Berlin, Germany</span>
            <span class="separator">•</span>
            <span class="list-item-jobId">Job ID: 515700</span>
            <span class="separator">•</span>
            <span class="list-item-family">Engineering</span>
          </div>
        </article>
        <a href="/en_US/externaljobs/SearchJobs/?folderRecordsPerPage=6&amp;folderOffset=6">
          Next &gt;&gt;
        </a>
        """
        second_page = """
        <article class="article article--result">
          <h3><a href="/en_US/externaljobs/JobDetail/515728">
            Software Engineer
          </a></h3>
          <div class="article__header__text__subtitle">
            <span class="list-item-location">Multiple Locations</span>
            <span class="separator">•</span>
            <span class="list-item-jobId">Job ID: 515728</span>
            <span class="separator">•</span>
            <span class="list-item-family">Research &amp; Development</span>
          </div>
        </article>
        """
        get.side_effect = [
            _Response(
                first_page,
                url="https://jobs.siemens.com/en_US/externaljobs/SearchJobs/"
                    "?folderRecordsPerPage=18&folderOffset=0",
            ),
            _Response(
                second_page,
                url="https://jobs.siemens.com/en_US/externaljobs/SearchJobs/"
                    "?folderRecordsPerPage=6&folderOffset=6",
            ),
        ]

        jobs, error = fetch_avature(
            {
                "list_url": "https://jobs.siemens.com/en_US/externaljobs/SearchJobs/"
                    "?folderRecordsPerPage=18&folderOffset=0",
                "offset_param": "folderOffset",
                "size_param": "folderRecordsPerPage",
                "page_size": 18,
                "hard_max_pages": 2,
                "max_desc_details": 0,
            },
            {},
            "Siemens",
        )

        self.assertIsNone(error)
        target = next(job for job in jobs if job["ats_id"] == "515728")
        self.assertEqual("Software Engineer", target["title"])
        self.assertEqual("Multiple Locations", target["location"])
        self.assertEqual("Multiple Locations", target["location_raw"])
        self.assertIn("folderOffset=6", get.call_args_list[1].args[0])


class RequestedCompanySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        companies = json.loads(
            (ROOT / "app" / "companies.json").read_text(encoding="utf-8")
        )
        cls.by_name = {company["name"]: company for company in companies}

    def test_all_requested_sources_are_configured(self):
        expected = {
            "Formation Bio": ("greenhouse", "board_token", "formationbio"),
            "Auctor": ("ashby", "ashby_slug", "auctor"),
            "CLEAR": ("greenhouse", "board_token", "clear"),
            "Eulerity": ("greenhouse", "board_token", "eulerity"),
            "Assembled": ("ashby", "ashby_slug", "assembledhq"),
            "MadHive": ("ashby", "ashby_slug", "madhive"),
            "GovWell": ("ashby", "ashby_slug", "govwell"),
            "Norm AI": ("ashby", "ashby_slug", "norm-ai"),
            "Revin": ("ashby", "ashby_slug", "revin"),
            "Savvy Wealth": ("ashby", "ashby_slug", "savvy"),
            "SimpleClosure": ("ashby", "ashby_slug", "simpleclosure"),
            "Whatnot": ("ashby", "ashby_slug", "whatnot"),
        }
        for company, (mode, key, value) in expected.items():
            with self.subTest(company=company):
                scrape = self.by_name[company]["scrape"]
                self.assertEqual(mode, scrape["mode"])
                self.assertEqual(value, scrape[key])

        fanduel = self.by_name["FanDuel"]["scrape"]
        self.assertEqual("html", fanduel["mode"])
        self.assertEqual(
            "https://www.fanduel.careers/open-positions/",
            fanduel["list_url"],
        )

        patientnow = self.by_name["PatientNow"]["scrape"]
        self.assertEqual("rippling", patientnow["mode"])
        self.assertEqual(
            "https://ats.rippling.com/patientnow/jobs",
            patientnow["list_url"],
        )

    def test_etsy_uses_paginated_search_not_homepage_features(self):
        scrape = self.by_name["Etsy"]["scrape"]

        self.assertEqual("https://careers.etsy.com/jobs/search", scrape["list_url"])
        self.assertIs(scrape["reader"], True)
        self.assertEqual("query_page", scrape["pagination"]["type"])

    def test_problem_workday_and_greenhouse_sources_fetch_real_requirements(self):
        for company in ("Adobe", "BlackRock", "NetApp", "Pinterest"):
            with self.subTest(company=company):
                scrape = self.by_name[company]["scrape"]
                self.assertIs(scrape["fetch_experience_details"], True)
                self.assertIs(scrape["refresh_experience"], True)

    def test_unitedhealth_config_separates_card_metadata(self):
        scrape = self.by_name["UnitedHealth Group"]["scrape"]

        self.assertEqual(r"\s*(?:\d+\s*)?\|.*$", scrape["title_suffix_regex"])
        self.assertEqual(r"\|\s*([^|]+?)(?:\s*\||$)", scrape["location_regex"])
        self.assertEqual(r"/(\d+)$", scrape["ats_id_regex"])

    def test_nutanix_fetches_jobvite_requirements(self):
        scrape = self.by_name["Nutanix"]["scrape"]

        self.assertIs(scrape["fetch_experience_details"], True)
        self.assertIs(scrape["refresh_experience"], True)

    def test_fresh_defaults_fetch_bounded_missing_experience_details(self):
        from app.api.apis import _apply_scan_defaults

        scrape = _apply_scan_defaults({"mode": "workday"}, "fresh")
        self.assertIs(scrape["fetch_experience_details"], True)
        self.assertIs(scrape["fetch_new_experience_only"], False)
        self.assertEqual(scrape["max_experience_details"], 100)

    def test_reported_experience_regressions_fetch_authoritative_requirements(self):
        point72 = self.by_name["Point72"]["scrape"]
        self.assertIs(point72["fetch_experience_details"], True)

        for company in ("Disney", "JPMorgan Chase"):
            with self.subTest(company=company):
                scrape = self.by_name[company]["scrape"]
                self.assertIs(scrape["fetch_experience_details"], True)
                self.assertIs(scrape["refresh_experience"], True)

    @patch("app.api.ats.greenhouse.requests.get")
    def test_c3_greenhouse_keeps_each_location_specific_opening(self, get):
        data = {
            "jobs": [
                {
                    "title": "Forward Deployed Engineer (New York)",
                    "absolute_url": "https://c3.ai/job-description/8581327002",
                    "location": {"name": "New York, New York, United States"},
                },
                {
                    "title": "Forward Deployed Engineer (Redwood City)",
                    "absolute_url": "https://c3.ai/job-description/8581330002",
                    "location": {"name": "Redwood City, California, United States"},
                },
            ]
        }
        get.return_value = _Response(data=data)

        jobs, error = fetch_greenhouse(
            {"board_token": "c3iot", "fetch_experience_details": True},
            {},
            "C3 AI",
        )

        self.assertIsNone(error)
        self.assertEqual(2, len(jobs))
        self.assertIn(
            "Forward Deployed Engineer (New York)",
            {job["title"] for job in jobs},
        )

    @patch("app.api.ats.workday.make_id", return_value="adobe-job")
    @patch("app.api.ats.workday._fetch_workday_detail")
    @patch("app.api.ats.workday.requests.post")
    def test_workday_refresh_rechecks_cached_description(
        self, post, fetch_detail, _make_id
    ):
        post.return_value = _Response(data={
            "total": 1,
            "jobPostings": [{
                "title": "Software Development Engineer",
                "externalPath": "/job/San-Jose/Software-Engineer_R123",
                "locationsText": "San Jose, California, United States",
                "postedOn": "2026-07-24",
            }],
        })
        fetch_detail.return_value = {
            "posted": "2026-07-24",
            "location": "San Jose, CA, US",
            "description": "Hands-on experience building GenAI services.",
        }
        seen = {
            "adobe-job": {
                "exp_min": 5,
                "exp_label": "5+ years",
                "exp_source": "text",
                "has_description": True,
            }
        }

        jobs, error = fetch_workday(
            {
                "list_url": "https://adobe.wd5.myworkdayjobs.com/external_experienced",
                "fetch_experience_details": True,
                "fetch_new_experience_only": True,
                "refresh_experience": True,
                "hard_max_pages": 1,
            },
            seen,
            "Adobe",
        )

        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual(
            "Hands-on experience building GenAI services.",
            jobs[0]["description"],
        )
        fetch_detail.assert_called_once()


if __name__ == "__main__":
    unittest.main()
