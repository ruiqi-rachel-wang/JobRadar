import unittest
from unittest.mock import patch

from app.api.ats.bankofamerica import _boa_locations, fetch_bankofamerica
from app.api.utils import normalize_locations


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def posting(**overrides):
    result = {
        'postingTitle': 'GFC External Risk Event Review Manager',
        'jcrURL': '/en-us/job-detail/26038549/gfc-external-risk-event-review-manager-multiple-locations',
        'jobRequisitionId': '26038549',
        'city': 'New York', 'state': 'New York', 'country': 'United States',
        'locationString': 'US - NC - Charlotte - 100 N Tryon St (NC1007)',
        'additionalLocations': (
            'US - NJ - Jersey City - 525 Washington Blvd (NJ2525),'
            'US - NY - New York - ONE BRYANT PARK (NY1100),'
            'US - NY - New York - ONE BRYANT PARK (NY1100),'
        ),
        'additionalLocationsList': (
            '525 Washington Blvd (NJ2525), Jersey City, New Jersey, United States; '
            'ONE BRYANT PARK, New York, New York, United States; '
        ),
    }
    result.update(overrides)
    return result


class BankOfAmericaLocationsTests(unittest.TestCase):
    def test_keeps_primary_nc_and_additional_ny_and_nj_without_street_addresses(self):
        locations = _boa_locations(posting(city='Charlotte', state='North Carolina'))
        self.assertEqual(['Charlotte, NC, US', 'Jersey City, NJ, US', 'New York, NY, US'], locations)
        self.assertEqual(3, len(normalize_locations(locations)['locations']))

    def test_primary_address_takes_priority_over_search_result_city(self):
        locations = _boa_locations(posting(city='Incorrect City', state='California'))
        self.assertNotIn('Incorrect City', '; '.join(locations))
        self.assertEqual('Charlotte, NC, US', locations[0])

    def test_readable_additional_offices_handle_commas_in_street_addresses(self):
        locations = _boa_locations(posting(
            locationString='', city='Charlotte', state='North Carolina',
            additionalLocations='',
            additionalLocationsList='One Bryant Park, Floor 5, New York, New York, United States; ',
        ))
        normalized = normalize_locations(locations)['location']
        self.assertEqual('Charlotte, NC, US; New York, NY, US', normalized)

    @patch('app.api.ats.bankofamerica.time.sleep', return_value=None)
    @patch('app.api.ats.bankofamerica.requests.get')
    def test_pages_use_end_pointer_and_merge_locations_for_duplicate_postings(self, get, _sleep):
        first = posting(additionalLocations='', additionalLocationsList='')
        second = posting(locationString='US - NY - New York - One Bryant Park',
                         additionalLocations='', additionalLocationsList='')
        get.side_effect = [Response({'jobsList': [first], 'totalMatches': 2}),
                           Response({'jobsList': [second], 'totalMatches': 2})]
        jobs, error = fetch_bankofamerica({'rows': 1}, {}, 'Bank of America')
        self.assertIsNone(error)
        self.assertEqual({'start': 1, 'rows': 2, 'search': 'jobsByCountry', 'country': 'United States'},
                         get.call_args_list[1].kwargs['params'])
        self.assertEqual('26038549', jobs[0]['ats_id'])
        self.assertEqual('26038549', jobs[0]['requisition_id'])
        self.assertEqual(1, len(jobs))
        self.assertEqual(['Charlotte, NC, US', 'New York, NY, US'], jobs[0]['locations_raw'])

    @patch('app.api.ats.bankofamerica.requests.get')
    def test_duplicates_in_one_page_merge_every_office(self, get):
        first = posting(additionalLocations='', additionalLocationsList='')
        second = posting(locationString='US - NY - New York - One Bryant Park',
                         additionalLocations='', additionalLocationsList='')
        get.return_value = Response({'jobsList': [first, second], 'totalMatches': 2})
        jobs, error = fetch_bankofamerica({}, {}, 'Bank of America')
        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual(['Charlotte, NC, US', 'New York, NY, US'], jobs[0]['locations_raw'])

    @patch('app.api.ats.bankofamerica.requests.get')
    def test_page_cap_is_partial_and_cannot_close_unseen_postings(self, get):
        get.return_value = Response({'jobsList': [posting()], 'totalMatches': 1741})
        jobs, error = fetch_bankofamerica({'rows': 1, 'hard_max_pages': 1}, {}, 'Bank of America')
        self.assertEqual(1, len(jobs))
        self.assertEqual('partial Bank of America listing: fetched 1 of 1741 source records', error)

    @patch('app.api.ats.bankofamerica.requests.get')
    def test_invalid_response_and_missing_detail_urls_are_errors(self, get):
        get.return_value = Response({'message': 'Temporarily unavailable'})
        self.assertTrue(fetch_bankofamerica({}, {}, 'Bank of America')[1])
        get.return_value = Response({'jobsList': [posting(jcrURL='')], 'totalMatches': 1})
        jobs, error = fetch_bankofamerica({}, {}, 'Bank of America')
        self.assertEqual([], jobs)
        self.assertIn('invalid Bank of America posting', error)

    @patch('app.api.ats.bankofamerica.time.sleep', return_value=None)
    @patch('app.api.ats.bankofamerica.requests.get')
    def test_repeated_pages_are_not_reported_as_a_complete_listing(self, get, _sleep):
        get.return_value = Response({'jobsList': [posting()], 'totalMatches': 2})
        jobs, error = fetch_bankofamerica({'rows': 1}, {}, 'Bank of America')
        self.assertEqual(1, len(jobs))
        self.assertIn('repeated Bank of America listing page', error)
