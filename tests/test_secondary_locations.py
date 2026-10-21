import json
import unittest
from unittest.mock import patch

from app.api.apis import _enrich
from app.api.ats.base import fetch_detail_description
from app.api.ats.icims import _icims_location_from_detail
from app.api.ats.jobsapi import fetch_jobsapi
from app.api.ats.lever import fetch_lever
from app.api.ats.oraclecloud import fetch_oraclecloud
from app.api.ats.playwright import fetch_phenom
from app.api.ats.workday import _workday_detail_location, fetch_workday
from app.api.utils import normalize_locations, make_id


class Response:
    status_code = 200
    headers = {'Content-Type': 'application/json'}
    def __init__(self, data=None, text=''):
        self.data, self.text = data, text
    def json(self):
        return self.data
    def raise_for_status(self):
        pass


def place(city, region, country='US'):
    return {'@type': 'Place', 'address': {'addressLocality': city,
            'addressRegion': region, 'addressCountry': {'@type': 'Country', 'name': country}}}


class SecondaryLocationsTests(unittest.TestCase):
    @patch('app.api.ats.lever.requests.get')
    def test_lever_keeps_all_locations_and_primary_without_duplicates(self, get):
        get.return_value = Response([{'text': 'Engineer', 'id': 'abc',
            'hostedUrl': 'https://jobs.lever.co/employer/abc', 'categories': {
                'location': 'Charlotte, NC', 'allLocations': ['New York, NY', 'Charlotte, NC']}}])
        jobs, error = fetch_lever({'lever_slug': 'employer'}, {}, 'Employer')
        _enrich(jobs, {}, 'Employer')
        self.assertIsNone(error)
        self.assertEqual('Charlotte, NC, US; New York, NY, US', jobs[0]['location'])
        self.assertEqual(2, len(jobs[0]['locations']))

    @patch('app.api.ats.jobsapi.requests.get')
    def test_jobsapi_preserves_full_location_and_additional_offices(self, get):
        get.return_value = Response({'jobs': [{'data': {'title': 'Engineer', 'req_id': '42',
            'city': 'Palo Alto', 'country': 'United States',
            'full_location': 'Palo Alto, California; Irvine, California',
            'additional_locations': [{'city': 'Irvine', 'state': 'California', 'country': 'United States'},
                                     {'city': 'New York', 'state': 'New York', 'country': 'United States'}]}}]})
        jobs, error = fetch_jobsapi({'api_url': 'https://example.com/api/jobs',
            'job_url_prefix': 'https://example.com/jobs/'}, {}, 'Employer')
        _enrich(jobs, {}, 'Employer')
        self.assertIsNone(error)
        self.assertEqual('Palo Alto, CA, US; Irvine, CA, US; New York, NY, US', jobs[0]['location'])
        self.assertEqual(3, len(jobs[0]['locations']))

    @patch('app.api.ats.oraclecloud.requests.get')
    def test_oracle_requests_secondary_geographies_and_offices(self, get):
        get.return_value = Response({'items': [{'TotalJobsCount': 1, 'requisitionList': [{
            'Id': '42', 'Title': 'Engineer', 'PrimaryLocation': 'Charlotte, NC, United States',
            'secondaryLocations': [{'Name': 'New York, NY, United States'}],
            'otherWorkLocations': [{'TownOrCity': 'New York', 'Region2': 'NY', 'Country': 'US'},
                {'TownOrCity': 'Chicago', 'Region2': 'IL', 'Country': 'US'},
                {'LocationName': 'A building is not a city'}]}]}]})
        jobs, error = fetch_oraclecloud({'list_url': 'https://example.com/en/sites/CX_1/jobs',
            'fetch_experience_details': False}, {}, 'Employer')
        _enrich(jobs, {}, 'Employer')
        self.assertIsNone(error)
        self.assertIn('requisitionList.secondaryLocations', get.call_args.args[0])
        self.assertEqual('Charlotte, NC, US; New York, NY, US; Chicago, IL, US', jobs[0]['location'])
        self.assertEqual(3, len(jobs[0]['locations']))

    @patch('app.api.ats.playwright.requests.get')
    def test_phenom_keeps_all_offices(self, get):
        payload = {'eagerLoadRefineSearch': {'totalHits': 1, 'data': {'jobs': [{
            'title': 'Engineer', 'reqId': '42', 'cityStateCountry': 'Boston, Massachusetts, United States',
            'multi_location_array': [{'location': 'Boston, Massachusetts, United States'},
                {'cityStateCountry': 'New York, New York, United States'},
                {'location': 'London, United Kingdom'}]}]}}}
        get.return_value = Response(text='phApp.ddo = ' + json.dumps(payload) + ';')
        jobs, error = fetch_phenom({'list_url': 'https://example.com/search-results'}, {}, 'Employer')
        _enrich(jobs, {}, 'Employer')
        self.assertIsNone(error)
        self.assertEqual('Boston, MA, US; New York, NY, US; London, UK', jobs[0]['location'])

    @patch('app.api.ats.icims.requests.get')
    def test_icims_jsonld_adds_secondary_locations_despite_visible_primary(self, get):
        posting = {'@type': 'JobPosting', 'jobLocation': [place('Charlotte', 'NC'), place('New York', 'NY')]}
        get.return_value = Response(text='<dl><dt>Location</dt><dd>US-NC-Charlotte</dd></dl>'
            '<script type="application/ld+json">' + json.dumps({'@graph': [posting]}) + '</script>')
        loc, _, _, raw = _icims_location_from_detail('https://example.com/jobs/42/engineer/job')
        self.assertEqual('Charlotte, NC, US; New York, NY, US', loc)
        self.assertEqual(2, len(normalize_locations(raw['locations_raw'])['locations']))

    @patch('app.api.ats.icims.requests.get')
    def test_icims_all_visible_locations_without_structured_data(self, get):
        get.return_value = Response(text='<dl><dt>Job Locations</dt>'
            '<dd>US-NC-Charlotte | US-NY-New York</dd></dl>')
        loc, _, _, _ = _icims_location_from_detail('https://example.com/jobs/42/engineer/job')
        self.assertEqual('Charlotte, NC, US; New York, NY, US', loc)

    @patch('app.api.ats.base.requests.get')
    def test_generic_detail_retains_all_job_places_and_ignores_headquarters(self, get):
        posting = {'@type': ['Thing', 'JobPosting'], 'description': 'Build things.',
            'hiringOrganization': {'address': place('Seattle', 'WA')['address']},
            'jobLocation': [place('Charlotte', 'NC'), place('New York', 'NY')]}
        get.return_value = Response(text='<script type="application/ld+json">' +
            json.dumps({'@graph': [posting]}) + '</script>')
        detail = fetch_detail_description('https://example.com/jobs/42')
        self.assertEqual('Charlotte, NC, US; New York, NY, US', detail['location'])
        self.assertNotIn('Seattle', detail['location'])

    def test_workday_primary_country_does_not_overwrite_secondary_country(self):
        loc = _workday_detail_location({'location': 'Getafe Area',
            'country': {'descriptor': 'Spain'},
            'additionalLocations': ['Hamburg Area', 'Toulouse Area',
                {'descriptor': 'London', 'country': {'descriptor': 'United Kingdom'}}]})
        self.assertEqual('Getafe Area, Spain; Hamburg Area; Toulouse Area; London, UK', loc)

    @patch('app.api.ats.workday.requests.get')
    @patch('app.api.ats.workday.requests.post')
    def test_workday_cached_multiple_locations_survive_enrichment(self, post, get):
        path = '/job/Charlotte/Engineer_42'; url = 'https://example.wd1.myworkdayjobs.com/Jobs' + path
        post.return_value = Response({'total': 1, 'jobPostings': [{'title': 'Engineer',
            'externalPath': path, 'locationsText': '2 Locations',
            'postedOn': '2026-10-20', 'bulletFields': ['42']}]})
        locs = normalize_locations(['Charlotte, NC', 'New York, NY'])
        existing = {**locs, 'has_description': True, 'exp_label': '3+ years',
                    'exp_source': 'text', 'posted': '2026-10-20'}
        seen = {make_id('Employer', url, 'Engineer', 'workday', '42'): existing}
        jobs, error = fetch_workday({'list_url': 'https://example.wd1.myworkdayjobs.com/Jobs',
            'fetch_experience_details': False, 'fetch_location_details': False}, seen, 'Employer')
        _enrich(jobs, seen, 'Employer')
        self.assertIsNone(error)
        self.assertEqual(locs['location'], jobs[0]['location'])
        self.assertEqual(2, len(jobs[0]['locations']))
        get.assert_not_called()

    @patch('app.api.ats.workday.requests.get')
    @patch('app.api.ats.workday.requests.post')
    def test_workday_location_expansion_is_not_limited_by_description_budget(self, post, get):
        post.return_value = Response({'total': 2, 'jobPostings': [
            {'title': 'Engineer', 'externalPath': '/job/Engineer_42', 'locationsText': '2 Locations',
             'postedOn': '2026-10-20', 'bulletFields': ['42']},
            {'title': 'Analyst', 'externalPath': '/job/Analyst_43', 'locationsText': '2 Locations',
             'postedOn': '2026-10-20', 'bulletFields': ['43']}]})
        get.return_value = Response({'jobPostingInfo': {'location': 'Charlotte, NC',
            'additionalLocations': ['New York, NY']}})
        jobs, error = fetch_workday({'list_url': 'https://example.wd1.myworkdayjobs.com/Jobs',
            'fetch_experience_details': False, 'max_experience_details': 1}, {}, 'Employer')
        _enrich(jobs, {}, 'Employer')
        self.assertIsNone(error)
        self.assertEqual(2, get.call_count)
        self.assertTrue(all(len(j['locations']) == 2 for j in jobs))

    def test_greenhouse_hybrid_uses_advertised_job_locations(self):
        from app.api.ats.greenhouse import _greenhouse_locations
        locations = _greenhouse_locations({'location': {'name': 'Hybrid'}, 'metadata': [
            {'name': 'Cost Center', 'value': 'Professional Services'},
            {'name': 'Job Posting Location', 'value': ['Sydney, Australia', 'Melbourne, Australia']}],
            'offices': [{'name': 'Remote', 'location': 'New York, NY'}]})
        self.assertEqual('Sydney, Australia; Melbourne, Australia', normalize_locations(locations)['location'])

    def test_greenhouse_keeps_specific_public_location_over_office_assignments(self):
        from app.api.ats.greenhouse import _greenhouse_locations
        locations = _greenhouse_locations({'location': {'name': 'London, UK'}, 'metadata': [
            {'name': 'Primary Location', 'value': 'Dublin Office'}],
            'offices': [{'name': 'Remote', 'location': 'New York, NY'}]})
        self.assertEqual(['London, UK'], locations)

    def test_greenhouse_country_only_uses_all_posting_locations(self):
        from app.api.ats.greenhouse import _greenhouse_locations
        locations = _greenhouse_locations({'location': {'name': 'United States'}, 'metadata': [
            {'name': 'All Locations', 'value': 'NC - Charlotte; NY - New York City'}]})
        self.assertEqual('Charlotte, NC, US; New York City, NY, US', normalize_locations(locations)['location'])

    def test_structured_country_is_not_confused_with_us_state(self):
        from app.api.ats.base import _jobposting_locations
        locations = _jobposting_locations({'jobLocation': [place('Frankfurt', '', 'DE')]})
        self.assertEqual('Frankfurt, Germany', normalize_locations(locations)['location'])

    def test_icims_compound_jsonld_locality_preserves_every_coded_city(self):
        from app.api.ats.base import _jobposting_locations
        locations = _jobposting_locations({'jobLocation': [place('VA-Hopewell\nUS-VA-Richmond', 'UNAVAILABLE')]})
        self.assertEqual('Hopewell, VA, US; Richmond, VA, US', normalize_locations(locations)['location'])

    @patch('app.api.ats.playwright.requests.get')
    def test_phenom_paginates_past_1000_jobs_and_keeps_query_filters(self, get):
        from urllib.parse import parse_qs, urlparse
        def response(url, **kwargs):
            query = parse_qs(urlparse(url).query)
            self.assertEqual(['Engineering'], query['category'])
            offset = int(query.get('from', ['0'])[0])
            jobs = [{'title': f'Engineer {i}', 'reqId': str(i), 'location': 'New York, NY'}
                    for i in range(offset, min(offset + 10, 1001))]
            return Response(text='phApp.ddo = ' + json.dumps({'eagerLoadRefineSearch': {
                'totalHits': 1001, 'data': {'jobs': jobs}}}) + ';')
        get.side_effect = response
        jobs, error = fetch_phenom({'list_url': 'https://example.com/search-results?category=Engineering'}, {}, 'Employer')
        self.assertIsNone(error)
        self.assertEqual(1001, len(jobs))
        self.assertEqual(101, get.call_count)

    @patch('app.api.ats.playwright.requests.get')
    def test_phenom_deadline_is_reported_as_incomplete(self, get):
        jobs, error = fetch_phenom({'list_url': 'https://example.com/search-results',
            '_deadline_monotonic': 1}, {}, 'Employer')
        self.assertEqual([], jobs)
        self.assertIn('partial', error)
        get.assert_not_called()

    @patch('app.api.ats.playwright.requests.get')
    def test_phenom_duplicate_posting_rows_keep_both_cities(self, get):
        payload = {'eagerLoadRefineSearch': {'totalHits': 2, 'data': {'jobs': [
            {'title': 'Engineer', 'reqId': '42', 'location': 'Charlotte, NC'},
            {'title': 'Engineer', 'reqId': '42', 'location': 'New York, NY'}]}}}
        get.return_value = Response(text='phApp.ddo = ' + json.dumps(payload) + ';')
        jobs, error = fetch_phenom({'list_url': 'https://example.com/search-results'}, {}, 'Employer')
        _enrich(jobs, {}, 'Employer')
        self.assertEqual(1, len(jobs))
        self.assertEqual('Charlotte, NC, US; New York, NY, US', jobs[0]['location'])
        self.assertIn('partial', error)

    def test_icims_explicit_german_country_is_not_delaware(self):
        from app.api.ats.icims import _parse_icims_location
        self.assertEqual('Frankfurt, Germany', _parse_icims_location('DE-UNAVAILABLE-Frankfurt'))

    def test_placeholder_does_not_count_as_an_office_when_cities_are_known(self):
        result = normalize_locations(['Multiple Locations', 'Charlotte, NC', 'New York, NY'])
        self.assertEqual('Charlotte, NC, US; New York, NY, US', result['location'])
        self.assertEqual(2, len(result['locations']))
        self.assertEqual('Multiple Locations', normalize_locations(['Multiple Locations'])['location'])

    def test_greenhouse_internal_location_metadata_does_not_narrow_public_country(self):
        from app.api.ats.greenhouse import _greenhouse_locations
        self.assertEqual(['Netherlands'], _greenhouse_locations({
            'location': {'name': 'Netherlands'}, 'metadata': [
                {'name': 'Location', 'value': 'Amsterdam, Netherlands'},
                {'name': 'Additional Locations', 'value': ['Remote - Netherlands']}]}))

    @patch('app.api.ats.workday.requests.get')
    @patch('app.api.ats.workday.requests.post')
    def test_workday_cache_keeps_resolved_remote_country_and_unknown_country_cities(self, post, get):
        from app.api.utils import make_id
        for locations in [normalize_locations(['Remote, US', 'New York, NY']),
                          normalize_locations(['Getafe Area, Spain', 'Hamburg Area'])]:
            locations['locations'][0]['raw'] = 'Remote' if locations['location'].startswith('Remote') else 'Getafe Area'
            post.return_value = Response({'total': 1, 'jobPostings': [{'title': 'Engineer',
                'externalPath': '/job/Engineer_42', 'locationsText': '2 Locations',
                'postedOn': '2026-10-20', 'bulletFields': ['42']}]})
            url = 'https://example.wd1.myworkdayjobs.com/Jobs/job/Engineer_42'
            seen = {make_id('Employer', url, 'Engineer', 'workday', '42'): {
                **locations, 'has_description': True, 'posted': '2026-10-20'}}
            jobs, error = fetch_workday({'list_url': 'https://example.wd1.myworkdayjobs.com/Jobs',
                'fetch_experience_details': False, 'fetch_location_details': False}, seen, 'Employer')
            _enrich(jobs, seen, 'Employer')
            self.assertIsNone(error)
            self.assertEqual(locations['location'], jobs[0]['location'])
        get.assert_not_called()

    def test_workday_full_state_names_and_remote_state_are_preserved(self):
        from app.api.utils import normalize_location
        self.assertEqual('Lubbock, TX, US', normalize_location('USA - Texas - Lubbock'))
        self.assertEqual('San Antonio, TX, US', normalize_location('USA - Texas - San Antonio'))
        self.assertEqual('Remote, TX, US', normalize_location('USA - Texas - Remote'))
        self.assertEqual('Remote, TX, US', normalize_location('Remote, TX, US'))
        self.assertEqual('Lubbock, TX, US; San Antonio, TX, US',
                         normalize_location('USA - Texas - Lubbock; USA - Texas - San Antonio'))
        self.assertEqual('Lubbock, TX, US; San Antonio, TX, US', _workday_detail_location({
            'location': 'USA - Texas - Lubbock', 'additionalLocations': ['USA - Texas - San Antonio'],
            'country': {'descriptor': 'United States of America'}}))

    def test_country_alias_duplicates_do_not_create_a_second_singapore_office(self):
        self.assertEqual('Singapore', normalize_locations(['Singapore', 'Singapore, SG, SG'])['location'])

    def test_unavailable_city_placeholder_is_not_displayed_as_a_location(self):
        self.assertEqual('Paris, France', normalize_locations(['UNAVAILABLE, Paris, France'])['location'])

    @patch('app.api.ats.workday.requests.get')
    @patch('app.api.ats.workday.requests.post')
    def test_failed_workday_location_refresh_preserves_cached_cities(self, post, get):
        post.return_value = Response({'total': 1, 'jobPostings': [{'title': 'Engineer',
            'externalPath': '/job/Engineer_42', 'locationsText': '2 Locations',
            'postedOn': '2026-10-20', 'bulletFields': ['42']}]})
        get.return_value = Response({})
        locations = normalize_locations(['Charlotte, NC', 'New York, NY'])
        url = 'https://example.wd1.myworkdayjobs.com/Jobs/job/Engineer_42'
        seen = {make_id('Employer', url, 'Engineer', 'workday', '42'): {
            **locations, 'has_description': True, 'posted': '2026-10-20'}}
        jobs, error = fetch_workday({'list_url': 'https://example.wd1.myworkdayjobs.com/Jobs',
            'fetch_experience_details': False, 'refresh_location': True}, seen, 'Employer')
        _enrich(jobs, seen, 'Employer')
        self.assertEqual(locations['location'], jobs[0]['location'])
        get.assert_called_once()
