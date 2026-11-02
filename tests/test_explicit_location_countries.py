import json
import unittest
from unittest.mock import patch

from app.api.apis import _enrich
from app.api.ats.workday import _fetch_workday_detail, _workday_detail_location
from app.api.search import display_location
from app.api.utils import _COUNTRY_ALIASES, normalize_locations
from scripts.build_country_location_repairs import country_proposal


class ExplicitLocationCountryTests(unittest.TestCase):
    def test_microsoft_country_region_city_order(self):
        examples = [
            ('India, Karnataka, Bangalore', 'Bangalore, Karnataka, India', 'IN'),
            ('United States, Washington, Redmond', 'Redmond, WA, US', 'US'),
            ('Switzerland, Zürich, Zürich', 'Zürich, Switzerland', 'CH'),
            ('United Kingdom, Cambridgeshire, Cambridge', 'Cambridge, Cambridgeshire, UK', 'GB'),
            ('Germany, Berlin, Berlin', 'Berlin, Germany', 'DE'),
            ('Germany, Multiple Locations, Multiple Locations', 'Multiple Locations, Germany', 'DE'),
            ('CN, Shanghai, Remote', 'Shanghai, China', 'CN'),
            ('US, Washington, Remote', 'WA, US', 'US'),
            ('Canada, Ontario, Remote', 'ON, Canada', 'CA'),
        ]
        for raw, expected, iso in examples:
            with self.subTest(raw=raw):
                result = normalize_locations(raw)
                self.assertEqual(expected, result['location'])
                self.assertEqual(iso, result['country_iso'])
                self.assertEqual(expected, display_location(raw, 'Software Engineer'))
                self.assertEqual(raw, result['locations'][0]['raw'])

    def test_every_recognized_country_name_has_a_country_code(self):
        for country in set(_COUNTRY_ALIASES.values()) - {'Worldwide'}:
            with self.subTest(country=country):
                self.assertIsNotNone(normalize_locations(country)['country_iso'])

    def test_kraken_multiple_listed_countries_do_not_become_unknown(self):
        result = normalize_locations(['Romania', 'Brazil', 'Peru', 'Cyprus', 'Paraguay'])
        self.assertIsNone(result['country_iso'])
        self.assertEqual(['RO', 'BR', 'PE', 'CY', 'PY'], [loc['country_iso'] for loc in result['locations']])
        self.assertNotIn('Country not listed', display_location(result['location'], 'Engineer'))

    def test_country_named_city_keeps_explicit_trailing_country(self):
        result = normalize_locations('Mexico, Missouri, US')
        self.assertEqual('Mexico, MO, US', result['location'])
        self.assertEqual('US', result['country_iso'])

    def test_region_codes_stay_in_their_explicit_country_when_reparsed(self):
        for raw, iso in [('CA, US', 'US'), ('NL, Canada', 'CA')]:
            with self.subTest(raw=raw):
                result = normalize_locations(raw)
                self.assertEqual(raw, result['location'])
                self.assertEqual(iso, result['country_iso'])
        for raw, expected in [('MN, Pine River', 'Pine River, MN, US'),
                              ('ND, Bismarck, United States of America', 'Bismarck, ND, US'),
                              ('TN, Nashville', 'Nashville, TN, US')]:
            with self.subTest(raw=raw):
                result = normalize_locations(raw)
                self.assertEqual(expected, result['location'])
                self.assertEqual('US', result['country_iso'])
        self.assertEqual('CA', normalize_locations('CA, Montreal')['country_iso'])
        self.assertEqual('IN', normalize_locations('IN, Bangalore')['country_iso'])

    def test_explicit_provider_country_names_with_commas_and_alternate_spellings(self):
        for raw, expected, iso in [
            ('Korea, Republic of', 'South Korea', 'KR'),
            ('Mainland China', 'China', 'CN'),
            ('Congo, Democratic Republic of the', 'DR Congo', 'CD'),
            ("Côte d'Ivoire", "Côte d'Ivoire", 'CI'),
            ('Guinea', 'Guinea', 'GN'), ('Iraq', 'Iraq', 'IQ'), ('Tunisia', 'Tunisia', 'TN'),
        ]:
            with self.subTest(raw=raw):
                result = normalize_locations(raw)
                self.assertEqual(expected, result['location'])
                self.assertEqual(iso, result['country_iso'])
                self.assertNotIn('Country not listed', display_location(raw, 'Engineer'))

    def test_workday_country_without_comma_is_preserved(self):
        result = normalize_locations('Pune Maharashtra India')
        self.assertEqual('Pune Maharashtra, India', result['location'])
        self.assertEqual('IN', result['country_iso'])

    def test_remote_city_and_country_are_retained(self):
        result = normalize_locations('Remote - Bangalore, India')
        self.assertEqual('Remote, Bengaluru, India', result['location'])
        self.assertEqual('IN', result['country_iso'])
        self.assertEqual('remote', result['remote_mode'])
        self.assertEqual(result['location'], normalize_locations(result['location'])['location'])
        self.assertNotIn('Country not listed', display_location(result['location'], 'Engineer'))
        self.assertIsNone(normalize_locations('Remote - Unlisted City')['country_iso'])

    def test_repeated_state_codes_do_not_recurse(self):
        for raw in ['CA, CA, US', 'IN, CA, US', 'Remote - CA, CA, US']:
            with self.subTest(raw=raw):
                self.assertEqual('US', normalize_locations(raw)['country_iso'])

    def test_workday_facility_retains_address_and_explicit_country(self):
        raw = '1/124, SHIVAJI GARDENS, MOONLI'
        loc = _workday_detail_location({'location': raw, 'country': {'descriptor': 'India'}})
        self.assertEqual(raw + ', India', loc)
        result = normalize_locations(loc)
        self.assertEqual('IN', result['country_iso'])
        self.assertIsNone(result['locations'][0]['city'])
        self.assertNotIn('Country not listed', display_location(loc, 'Java React Full stack Developer'))

    @patch('app.api.ats.workday.requests.get')
    def test_workday_keeps_original_location_evidence_for_future_repairs(self, get):
        raw = '1/124, SHIVAJI GARDENS, MOONLI'
        get.return_value.json.return_value = {'jobPostingInfo': {
            'location': raw, 'country': {'descriptor': 'India'},
            'jobRequisitionLocation': {'descriptor': raw, 'country': {'alpha2Code': 'IN'}},
        }}
        detail = _fetch_workday_detail('https://example.test', 'tenant', 'board', '/job/one')
        self.assertEqual(raw, detail['raw_data']['location'])
        self.assertEqual('IN', detail['raw_data']['jobRequisitionLocation']['country']['alpha2Code'])

    def test_provider_enrichment_retains_all_country_first_offices(self):
        raw = ['India, Karnataka, Bangalore', 'India, Telangana, Hyderabad', 'India, Uttar Pradesh, Noida']
        jobs = [{'title': 'Principal Software Engineer', 'url': 'https://example.test/job/1',
                 'source': 'eightfold', 'locations_raw': raw, 'location_raw': raw[0]}]
        _enrich(jobs, {}, 'Microsoft')
        self.assertEqual('IN', jobs[0]['country_iso'])
        self.assertEqual(['Bangalore', 'Hyderabad', 'Noida'], [loc['city'] for loc in jobs[0]['locations']])
        self.assertEqual(raw, [loc['raw'] for loc in jobs[0]['locations']])

    def test_repair_proposal_uses_explicit_country_without_changing_identity(self):
        row = {'id': 'old-ms-id', 'company': 'Microsoft', 'url': 'https://example.test/job/1',
               'source': 'eightfold', 'country_iso': None, 'remote_mode': 'onsite',
               'location': 'India, Karnataka, Bangalore', 'location_raw': 'India, Karnataka, Bangalore',
               'locations': json.dumps([{'raw': 'India, Karnataka, Bangalore', 'country_iso': None}])}
        proposal = country_proposal(row, '2026-11-02T00:00:00Z')
        self.assertEqual(row['id'], proposal['id'])
        self.assertEqual([row['location']], proposal['expected_locations'])
        self.assertEqual('IN', normalize_locations(proposal['locations_raw'])['country_iso'])
        repaired = {**row, **normalize_locations(proposal['locations_raw'])}
        self.assertIsNone(country_proposal(repaired, '2026-11-02T00:00:00Z'))

    def test_repair_primary_workday_country_never_overwrites_secondary_office(self):
        row = {'id': 'old-wd-id', 'company': 'Employer', 'url': 'https://example.test/job/2',
               'source': 'workday', 'country_iso': 'GB', 'remote_mode': None,
               'location': '1, 124, MOONLI; London, UK', 'location_raw': '1, 124, MOONLI',
               'provider_country': json.dumps({'descriptor': 'India'}),
               'locations': json.dumps([{'raw': '1, 124, MOONLI', 'country_iso': None},
                                        {'raw': 'London, UK', 'country_iso': 'GB'}])}
        proposal = country_proposal(row, '2026-11-02T00:00:00Z')
        repaired = normalize_locations(proposal['locations_raw'])
        self.assertEqual(['IN', 'GB'], [loc['country_iso'] for loc in repaired['locations']])
        self.assertEqual('London, UK', proposal['locations_raw'][1])

    def test_repair_does_not_invent_country_for_unresolved_places(self):
        row = {'id': 'unknown', 'company': 'Employer', 'url': 'https://example.test/job/3',
               'source': 'test', 'country_iso': None, 'remote_mode': None,
               'location': 'Unlisted City', 'location_raw': 'Unlisted City', 'locations': '[]'}
        self.assertIsNone(country_proposal(row, '2026-11-02T00:00:00Z'))

    def test_repair_splits_repeated_primary_list_before_qualifying_its_country(self):
        row = {'id': 'compound', 'company': 'Citi', 'url': 'https://example.test/job/4',
               'source': 'workday', 'country_iso': None, 'remote_mode': None,
               'location': '1, 124, MOONLI; Pune Maharashtra India',
               'location_raw': '1, 124, MOONLI; Pune Maharashtra India',
               'provider_country': {'descriptor': 'India'},
               'locations': json.dumps([{'raw': '1, 124, MOONLI', 'country_iso': None},
                                        {'raw': 'Pune Maharashtra India', 'country_iso': None}])}
        proposal = country_proposal(row, '2026-11-02T00:00:00Z')
        result = normalize_locations(proposal['locations_raw'])
        self.assertEqual(2, len(result['locations']))
        self.assertEqual(['IN', 'IN'], [loc['country_iso'] for loc in result['locations']])

    def test_repair_keeps_qualified_remote_states_alongside_unspecified_remote(self):
        row = {'id': 'remote-states', 'company': 'Sanford Health', 'url': 'https://example.test/job/5',
               'source': 'workday', 'country_iso': 'US', 'remote_mode': 'remote',
               'location': 'Sioux Falls, SD, US; Remote',
               'location_raw': 'SD, Sioux Falls; Remote; SD, City - Remote, US; ND, City - Remote, US',
               'locations': json.dumps([{'raw': 'SD, Sioux Falls', 'country_iso': 'US'},
                                        {'raw': 'Remote', 'country_iso': None}])}
        proposal = country_proposal(row, '2026-11-02T00:00:00Z')
        result = normalize_locations(proposal['locations_raw'])
        self.assertIn('Remote, SD, US', result['location'])
        self.assertIn('Remote, ND, US', result['location'])
        self.assertEqual(4, len(result['locations']))


if __name__ == '__main__':
    unittest.main()
