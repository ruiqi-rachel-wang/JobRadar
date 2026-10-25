import json
import unittest
from unittest.mock import patch

from app.api.apis import _apply_scan_defaults, _enrich
from app.api.ats.public_careers import fetch_amazon, fetch_apple, fetch_google, fetch_deshaw, fetch_ibm, fetch_goldman


class Response:
    def __init__(self, data=None, text=''):
        self.data, self.text = data, text

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


def apple_page(entries, total):
    data = {'loaderData': {'search': {'searchResults': entries, 'totalRecords': total}}}
    return Response(text='<script>window.__staticRouterHydrationData = JSON.parse(' +
                    json.dumps(json.dumps(data)) + ');</script>')


def apple_role(identifier, places):
    return {'id': identifier, 'positionId': '123', 'postingTitle': 'Engineer',
            'transformedPostingTitle': 'engineer', 'postDateInGMT': '2026-10-23T12:00:00Z',
            'locations': [{'name': city, 'stateProvince': state, 'countryName': 'United States'}
                          for city, state in places]}


class PublicCareersTests(unittest.TestCase):
    @patch('app.api.ats.public_careers.time.sleep')
    @patch('app.api.ats.public_careers.requests.get')
    def test_amazon_uses_posting_paths_country_filter_and_all_places(self, get, sleep):
        get.return_value = Response({'hits': 1, 'jobs': [{
            'id_icims': '42', 'title': 'Engineer', 'job_path': '/en/jobs/42/engineer',
            'posted_date': 'October 23, 2026', 'description': 'Build systems.',
            'locations': [json.dumps({'city': 'Charlotte', 'region': 'NC', 'normalizedCountryName': 'United States'}),
                          json.dumps({'city': 'New York', 'region': 'NY', 'normalizedCountryName': 'United States'})]}]})
        jobs, error = fetch_amazon({'api_url': 'https://www.amazon.jobs/en/search.json', 'country_code': 'US'}, {}, 'Amazon')
        self.assertIsNone(error)
        self.assertEqual('USA', get.call_args.kwargs['params']['normalized_country_code[]'])
        self.assertEqual(100, get.call_args.kwargs['params']['result_limit'])
        self.assertEqual('https://www.amazon.jobs/en/jobs/42/engineer', jobs[0]['url'])
        _enrich(jobs, {}, 'Amazon')
        self.assertEqual('Charlotte, NC, US; New York, NY, US', jobs[0]['location'])
        self.assertEqual('2026-10-23', jobs[0]['posted'])

    @patch('app.api.ats.public_careers.time.sleep')
    @patch('app.api.ats.public_careers.requests.get')
    def test_amazon_repeated_page_is_partial_and_retains_results(self, get, sleep):
        get.return_value = Response({'hits': 500, 'jobs': [{'id_icims': '42', 'title': 'Engineer',
            'job_path': '/en/jobs/42/engineer'}]})
        jobs, error = fetch_amazon({'api_url': 'https://www.amazon.jobs/en/search.json'}, {}, 'Amazon')
        self.assertEqual(1, len(jobs))
        self.assertIn('partial', error)
        self.assertEqual(2, get.call_count)

    @patch('app.api.ats.public_careers.time.sleep')
    @patch('app.api.ats.public_careers.requests.get')
    def test_apple_paginates_and_preserves_posting_ids_and_all_locations(self, get, sleep):
        get.side_effect = [apple_page([apple_role('123-1', [('Charlotte', 'NC'), ('New York', 'NY')])], 2),
                           apple_page([apple_role('123-2', [('Chicago', 'IL')])], 2)]
        jobs, error = fetch_apple({'list_url': 'https://jobs.apple.com/en-us/search?location=united-states-USA'}, {}, 'Apple')
        self.assertIsNone(error)
        self.assertEqual(['123-1', '123-2'], [job['ats_id'] for job in jobs])
        self.assertEqual(2, get.call_args.kwargs['params']['page'])
        _enrich(jobs, {}, 'Apple')
        self.assertEqual('Charlotte, NC, US; New York, NY, US', jobs[0]['location'])

    @patch('app.api.ats.public_careers.requests.get')
    def test_apple_shell_is_an_error_not_an_empty_hiring_feed(self, get):
        get.return_value = Response(text='<html>Sign in</html>')
        jobs, error = fetch_apple({'list_url': 'https://jobs.apple.com/en-us/search'}, {}, 'Apple')
        self.assertEqual([], jobs)
        self.assertIn('state missing', error)

    @patch('app.api.ats.public_careers.requests.get')
    def test_apple_confirms_empty_payload_before_reporting_no_jobs(self, get):
        get.side_effect = [apple_page([], 0), apple_page([apple_role('123-1', [('New York', 'NY')])], 1)]
        jobs, error = fetch_apple({'list_url': 'https://jobs.apple.com/en-us/search'}, {}, 'Apple')
        self.assertIsNone(error)
        self.assertEqual(1, len(jobs))
        self.assertEqual(2, get.call_count)

    @patch('app.api.ats.public_careers.time.sleep')
    @patch('app.api.ats.public_careers.requests.get')
    def test_google_reads_titles_all_places_and_follows_next_page(self, get, sleep):
        card = lambda path: ('<li><h3>Engineer</h3><span class="r0wTof">Charlotte, NC, US</span>'
            '<span class="r0wTof">New York, NY, US</span><a href="' + path + '">Learn more</a></li>')
        get.side_effect = [Response(text=card('jobs/results/123-engineer') + '<a href="?page=2">Next</a>'),
                           Response(text=card('/about/careers/applications/jobs/results/456-engineer'))]
        jobs, error = fetch_google({'list_url': 'https://www.google.com/about/careers/applications/jobs/results/'}, {}, 'Google')
        self.assertIsNone(error)
        self.assertEqual(2, len(jobs))
        self.assertTrue(all(j['url'].startswith('https://www.google.com/about/careers/applications/jobs/results/') for j in jobs))
        self.assertEqual(2, len(jobs[0]['locations_raw']))

    @patch('app.api.ats.public_careers.requests.get')
    def test_deshaw_excludes_internal_and_inactive_jobs(self, get):
        public = {'data': {'id': 42, 'activeOnJobsListing': True, 'displayName': 'Engineer',
                          'jobUrl': 'Engineer-42', 'jobMetadata': {'jobLocations': [{'name': 'New York, NY'}]}}}
        hidden = {'data': {**public['data'], 'id': 43, 'activeOnJobsListing': False}}
        data = {'props': {'pageProps': {'regularJobs': [public, hidden], 'internships': [], 'internalJobs': [hidden]}}}
        get.return_value = Response(text='<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>')
        jobs, error = fetch_deshaw({'list_url': 'https://www.deshaw.com/careers/choose-your-path'}, {}, 'D. E. Shaw')
        self.assertIsNone(error)
        self.assertEqual(['42'], [job['ats_id'] for job in jobs])

    def test_fresh_early_stop_only_for_newest_first_employer_feeds(self):
        for mode in ('amazon', 'apple'):
            self.assertTrue(_apply_scan_defaults({'mode': mode}, 'fresh')['stop_on_seen'])
            self.assertFalse(_apply_scan_defaults({'mode': mode, 'stop_on_seen': True}, 'full')['stop_on_seen'])
        self.assertNotIn('stop_on_seen', _apply_scan_defaults({'mode': 'google'}, 'fresh'))


class PublicSourceRecoveryTests(unittest.TestCase):
    @patch('app.api.ats.public_careers.time.sleep')
    @patch('app.api.ats.public_careers.requests.post')
    def test_ibm_rereads_tied_pages_until_all_documents_are_covered(self, post, sleep):
        def document(i):
            return {'_id': str(i), '_source': {'title': 'Engineer',
                'url': 'https://careers.ibm.com/careers/JobDetail?jobId=' + str(i),
                'field_keyword_19': 'Bangalore, IN'}}
        # A nominal two-page response repeats a document and misses one.
        # A bounded second pass must collect it before claiming completeness.
        first = [document(i) for i in range(1, 101)]
        post.side_effect = [Response({'hits': {'total': {'value': 101}, 'hits': first}}),
                            Response({'hits': {'total': {'value': 101}, 'hits': [document(1)]}}),
                            Response({'hits': {'total': {'value': 101}, 'hits': [document(101)]}})]
        jobs, error = fetch_ibm({}, {}, 'IBM')
        self.assertIsNone(error)
        self.assertEqual(101, len(jobs))
        self.assertEqual(['Bangalore, India'], jobs[0]['locations_raw'])

    @patch('app.api.ats.public_careers.requests.post')
    def test_goldman_uses_specific_role_urls_and_every_city(self, post):
        post.return_value = Response({'data': {'roleSearch': {'totalCount': 1, 'items': [{
            'roleId': '42_GS_MID_CAREER', 'externalSource': {'sourceId': '42'},
            'jobTitle': 'Engineer', 'status': 'POSTED', 'locations': [
                {'city': 'New York', 'state': 'NY', 'country': 'United States'},
                {'city': 'London', 'country': 'United Kingdom'}]}]}}})
        jobs, error = fetch_goldman({}, {}, 'Goldman Sachs')
        self.assertIsNone(error)
        self.assertEqual('https://higher.gs.com/roles/42', jobs[0]['url'])
        _enrich(jobs, {}, 'Goldman Sachs')
        self.assertEqual('New York, NY, US; London, UK', jobs[0]['location'])

    def test_expired_crawler_locks_do_not_hide_companies(self):
        from contextlib import contextmanager
        from app.api import main
        class DB:
            def execute(self, query):
                return self
            def fetchall(self):
                return [{'company': 'Expired', 'value': '2000-01-01 00:00:00'},
                        {'company': 'Current', 'value': '2999-01-01 00:00:00'},
                        {'company': 'Invalid', 'value': 'not-a-date'}]
        @contextmanager
        def connect(rows=False):
            yield DB()
        with patch.object(main, 'connect_db', connect):
            self.assertEqual({'Current': '2999-01-01 00:00:00'}, main._crawler_locks())
        self.assertTrue(main._company_is_public({'name': 'Current', 'scan_enabled': True},
                                               {'Current': '2999-01-01 00:00:00'}))

class ExplicitSourceAccessTests(unittest.TestCase):
    @patch('app.api.apis.robots_allowed', return_value=False)
    def test_only_explicit_company_exception_skips_the_robots_gate(self, robots):
        from app.api.apis import fetch_jobs
        role = {'title': 'Engineer', 'url': 'https://example.com/job/42'}
        with patch.dict('app.api.apis.FETCHERS', {'test_source': lambda *args: ([dict(role)], None)}), \
             patch('app.api.apis._enrich'):
            company = {'name': 'Employer', 'scrape': {'mode': 'test_source', 'list_url': 'https://example.com/jobs'}}
            jobs, error = fetch_jobs(company, {})
            self.assertEqual([], jobs)
            self.assertIn('robots', error)
            company['scrape']['respect_robots'] = False
            jobs, error = fetch_jobs(company, {})
            self.assertIsNone(error)
            self.assertEqual([role], jobs)

    @patch('app.api.ats.public_careers.time.sleep')
    @patch('app.api.ats.public_careers.requests.get')
    def test_apple_empty_middle_page_does_not_hide_later_jobs(self, get, sleep):
        get.side_effect = [apple_page([apple_role('1', [('New York', 'NY')])], 3),
                           apple_page([], 0), apple_page([], 0), apple_page([], 0),
                           apple_page([apple_role('3', [('Chicago', 'IL')])], 3)]
        jobs, error = fetch_apple({'list_url': 'https://jobs.apple.com/en-us/search'}, {}, 'Apple')
        self.assertEqual(['1', '3'], [job['ats_id'] for job in jobs])
        self.assertIn('partial', error)

class WorkdayIdentityRecoveryTests(unittest.TestCase):
    def test_display_columns_cannot_collapse_distinct_workday_jobs(self):
        from app.api.ats.workday import _workday_requisition_id
        first = {'bulletFields': ['Posted Today', 'Full time'],
                 'externalPath': '/job/Triangle/Carpenter_JR16862'}
        second = {'bulletFields': ['Posted Today', 'Full time'],
                  'externalPath': '/job/Columbus/Manager_JR16872'}
        self.assertEqual('JR16862', _workday_requisition_id(first))
        self.assertEqual('JR16872', _workday_requisition_id(second))
        self.assertEqual('R123', _workday_requisition_id({
            'bulletFields': ['R123', 'London'], 'externalPath': '/job/London/Engineer_R123-1'}))
