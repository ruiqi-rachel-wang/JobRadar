import unittest
from unittest.mock import patch
from app.api.ats.workable import fetch_workable
from app.api.utils import normalize_locations


class Response:
    def __init__(self, jobs):
        self.jobs = jobs
    def raise_for_status(self):
        pass
    def json(self):
        return {'jobs': self.jobs}


class WorkableLocationTests(unittest.TestCase):
    @patch('app.api.ats.workable.requests.get')
    def test_duplicate_shortcode_rows_keep_new_york_and_san_francisco(self, get):
        job = {'title': 'Senior Customer Solutions Engineer', 'shortcode': 'ABC123',
               'url': 'https://apply.workable.com/j/ABC123'}
        ny = {'city': 'New York', 'region': 'New York', 'country': 'United States'}
        sf = {'city': 'San Francisco', 'region': 'California', 'country': 'United States'}
        get.return_value = Response([{**job, 'locations': [ny]}, {**job, 'locations': [sf]},
                                     {**job, 'locations': [ny]}])
        jobs, error = fetch_workable({'workable_slug': 'employer'}, {}, 'Employer')
        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual('ABC123', jobs[0]['ats_id'])
        self.assertEqual('New York, NY, US; San Francisco, CA, US',
                         normalize_locations(jobs[0]['locations_raw'])['location'])
        self.assertEqual([ny, sf], jobs[0]['raw_data']['locations'])

    @patch('app.api.ats.workable.requests.get')
    def test_primary_office_is_kept_when_locations_array_contains_only_other_offices(self, get):
        get.return_value = Response([{'title': 'Engineer', 'shortcode': 'ABC123',
            'location': {'city': 'Charlotte', 'region': 'North Carolina', 'country': 'United States'},
            'locations': [{'city': 'New York', 'region': 'New York', 'country': 'United States'}]}])
        jobs, error = fetch_workable({'workable_slug': 'employer'}, {}, 'Employer')
        self.assertIsNone(error)
        self.assertEqual('Charlotte, NC, US; New York, NY, US', normalize_locations(jobs[0]['locations_raw'])['location'])

    @patch('app.api.ats.workable.requests.get')
    def test_different_postings_with_the_same_title_stay_separate(self, get):
        get.return_value = Response([{'title': 'Engineer', 'shortcode': 'ONE', 'city': 'London', 'country': 'United Kingdom'},
                                     {'title': 'Engineer', 'shortcode': 'TWO', 'city': 'New York', 'state': 'New York', 'country': 'United States'}])
        jobs, error = fetch_workable({'workable_slug': 'employer'}, {}, 'Employer')
        self.assertIsNone(error)
        self.assertEqual(['ONE', 'TWO'], [j['ats_id'] for j in jobs])
        self.assertEqual(['London, United Kingdom'], jobs[0]['locations_raw'])
