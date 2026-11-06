import unittest
from unittest.mock import patch

from app.api.apis import fetch_jobs
from app.api.ats.bamboohr import fetch_bamboohr
from scripts.audit_import_quality import _source_family


class Page:
    def __init__(self, text):
        self.text = text
        self.url = 'https://employer.example/careers'

    def raise_for_status(self):
        pass


class CompanyExpansionSourceTests(unittest.TestCase):
    @patch('app.api.apis.robots_allowed', return_value=True)
    @patch('app.api.ats.html.requests.get')
    def test_scoped_cards_keep_read_more_links_and_separate_locations(self, get, _robots):
        get.return_value = Page('''<a href="/news">News</a>
            <div class="box-job"><h2>Clinical Scientist</h2><p>Location: Natick, MA</p>
              <a href="/clinical-scientist">Read More</a></div>
            <div class="box-job"><h2>Software Engineer</h2>
              <a href="/software-engineer">Read More</a></div>''')
        jobs, error = fetch_jobs({'name': 'Example', 'scrape': {
            'mode': 'html', 'list_url': 'https://employer.example/careers',
            'include': 'employer.example/', 'job_card_selector': '.box-job',
            'job_title_selector': 'h2', 'job_location_selector': 'p',
            'job_location_prefix_regex': r'^Location:\s*',
            'title_from_slug_on_generic': True, 'fetch_experience_details': False,
        }}, {}, scan_type='full')
        self.assertIsNone(error)
        self.assertEqual(['Clinical Scientist', 'Software Engineer'], [j['title'] for j in jobs])
        self.assertEqual('Natick, MA', jobs[0]['location_raw'])
        self.assertFalse(jobs[1]['location_raw'])

    @patch('app.api.apis.robots_allowed', return_value=True)
    @patch('app.api.ats.html.requests.get')
    def test_anchor_cards_strip_department_from_explicit_location(self, get, _robots):
        get.return_value = Page('''<a class="rl-row" href="/careers/open-roles/engineer">
            <span class="title">Software Engineer</span><span class="place">Aviation / Boston, USA</span>
            <span>Onsite</span></a>''')
        jobs, error = fetch_jobs({'name': 'Example', 'scrape': {
            'mode': 'html', 'list_url': 'https://employer.example/careers',
            'include': '/careers/open-roles/', 'job_card_selector': 'a.rl-row',
            'job_title_selector': '.title', 'job_location_selector': '.place',
            'job_location_prefix_regex': r'^[^/]+/\s*', 'fetch_experience_details': False,
        }}, {}, scan_type='full')
        self.assertIsNone(error)
        self.assertEqual('Software Engineer', jobs[0]['title'])
        self.assertEqual('Boston, USA', jobs[0]['location_raw'])

    @patch('app.api.ats.bamboohr.requests.get')
    def test_bamboohr_uses_explicit_ats_country_without_headquarters_guess(self, get):
        get.return_value.json.return_value = {'result': [{
            'id': 90, 'jobOpeningName': 'Forward Deployed Engineer',
            'location': {'city': None, 'state': None},
            'atsLocation': {'city': None, 'country': 'United States', 'state': 'California'},
        }]}
        jobs, error = fetch_bamboohr({'bamboohr_slug': 'example'}, {}, 'Example')
        self.assertIsNone(error)
        self.assertEqual('California, United States', jobs[0]['location_raw'])

    def test_native_yc_companies_have_distinct_source_families(self):
        a = {'mode': 'ycombinator', 'yc_slug': 'alpha', 'list_url': 'https://www.ycombinator.com/companies/alpha/jobs'}
        b = {**a, 'yc_slug': 'beta', 'list_url': 'https://www.ycombinator.com/companies/beta/jobs'}
        self.assertEqual('ycombinator:alpha', _source_family(a))
        self.assertNotEqual(_source_family(a), _source_family(b))
