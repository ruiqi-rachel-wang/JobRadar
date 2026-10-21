import unittest
from unittest.mock import patch

from app.api.ats.successfactors import _fetch_sf_detail, fetch_successfactors


class Response:
    def __init__(self, text, url='https://careers.example/search/'):
        self.text, self.url = text, url

    def raise_for_status(self):
        pass


def listing(location, title='Senior Executive Assistant', total=1):
    return f'''<span class="paginationLabel">Results 1 – 25 of {total}</span>
    <table><tr><td><a href="/job/Atlanta-Assistant/1234/">{title}</a></td>
    <td><span class="jobLocation">{location}</span></td></tr></table>'''


class SuccessFactorsLocationsTests(unittest.TestCase):
    @patch('app.api.ats.successfactors.requests.get')
    def test_posting_cities_are_read_outside_description_and_deduplicated(self, get):
        get.return_value = Response('''<p id="job-location">
          <span class="jobGeoLocation">Atlanta, GA, US</span>
          <span class="jobGeoLocation"><i></i> Austin, TX, US</span>
          <span class="jobGeoLocation">Atlanta, GA, US</span></p>
          <div class="jobdescription">''' + 'Description. ' * 20 + '</div>')
        detail = _fetch_sf_detail('https://careers.example/job/1234/')
        self.assertEqual('Atlanta, GA, US; Austin, TX, US', detail['location'])

    @patch('app.api.ats.successfactors._fetch_sf_detail')
    @patch('app.api.ats.successfactors.requests.get')
    def test_fresh_scans_expand_hidden_cities_even_without_description_enrichment(self, get, detail):
        get.side_effect = [Response(listing('Atlanta, GA, US <small>+1 more…</small>')), Response('')]
        detail.return_value = {'location': 'Atlanta, GA, US; Austin, TX, US'}
        jobs, error = fetch_successfactors({
            'list_url': 'https://careers.example/search/', 'stop_on_seen': True,
            'max_fresh_desc_details': 0, 'require_complete_locations': True,
        }, {}, 'Example')
        self.assertIsNone(error)
        self.assertEqual(['Atlanta, GA, US', 'Austin, TX, US'], jobs[0]['locations_raw'])
        self.assertNotIn('Assistant', jobs[0]['location'])

    @patch('app.api.ats.successfactors.requests.get')
    def test_page_cap_is_reported_as_partial(self, get):
        get.return_value = Response(listing('Atlanta, GA, US', total=629))
        jobs, error = fetch_successfactors({
            'list_url': 'https://careers.example/search/', 'hard_max_pages': 1,
            'max_desc_details': 0,
        }, {}, 'Example')
        self.assertEqual(1, len(jobs))
        self.assertEqual('partial SuccessFactors listing: fetched 1 of 629 jobs', error)

    @patch('app.api.ats.successfactors._fetch_sf_detail', return_value={})
    @patch('app.api.ats.successfactors.requests.get')
    def test_failed_location_expansion_is_not_reported_as_complete(self, get, detail):
        get.side_effect = [Response(listing('Atlanta, GA, US +37 more…')), Response('')]
        _, error = fetch_successfactors({
            'list_url': 'https://careers.example/search/', 'require_complete_locations': True,
        }, {}, 'Example')
        self.assertIn('incomplete SuccessFactors locations', error)
