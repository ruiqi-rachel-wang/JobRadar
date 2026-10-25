"""Employer-owned public listings that do not use a shared ATS schema."""
import json
import re
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import HEADERS, requests, scan_deadline_exceeded
from ..utils import make_id, norm, _COUNTRY_CODES


def _already_seen(job, seen, company):
    return any(key in seen for key in (
        make_id(company, job['url'], job['title'], job['source'], job.get('ats_id')),
        make_id(company, job['url'], job['title']),
    ))


def _finish(jobs, error=None):
    return list(jobs.values()), error


def fetch_amazon(scrape, seen, company):
    jobs, signatures = {}, set()
    country = scrape.get('country_code', '')
    country = {'US': 'USA', 'UK': 'GBR'}.get(country, country)
    for page in range(int(scrape.get('hard_max_pages') or 1000)):
        if scan_deadline_exceeded(scrape):
            return _finish(jobs, 'company scan deadline reached (partial Amazon results)')
        params = {'offset': page * 100, 'result_limit': 100, 'sort': 'recent'}
        if country:
            params['normalized_country_code[]'] = country
        try:
            response = requests.get(scrape['api_url'], params=params, headers=HEADERS, timeout=25)
            response.raise_for_status()
            payload = response.json()
            if payload.get('error'):
                return _finish(jobs, str(payload['error']))
            entries = payload.get('jobs')
            if not isinstance(entries, list):
                return _finish(jobs, 'Amazon response has no jobs array')
        except Exception as exc:
            return _finish(jobs, str(exc))
        if not entries:
            return _finish(jobs)
        signature = tuple(str(j.get('id_icims') or j.get('id')) for j in entries)
        if signature in signatures:
            return _finish(jobs, 'Amazon repeated a page (partial results)')
        signatures.add(signature)
        page_jobs = []
        for item in entries:
            title, path = norm(item.get('title') or ''), item.get('job_path') or ''
            if not title or not re.match(r'^/en/jobs/\d+/', path):
                continue
            locations = []
            for value in item.get('locations') or []:
                try:
                    value = json.loads(value) if isinstance(value, str) else value
                except ValueError:
                    continue
                if isinstance(value, dict):
                    label = ', '.join(str(value.get(k)) for k in
                                      ('city', 'region', 'normalizedCountryName') if value.get(k))
                    if label and label not in locations:
                        locations.append(label)
            if not locations:
                locations = [item.get('normalized_location') or item.get('location') or '']
            description = '\n\n'.join(str(item[k]) for k in
                                      ('description', 'basic_qualifications', 'preferred_qualifications') if item.get(k))
            job = {'title': title, 'url': urljoin('https://www.amazon.jobs', path),
                   'ats_id': str(item.get('id_icims') or item.get('id') or ''),
                   'source': 'amazon', 'locations_raw': locations,
                   'location_raw': '; '.join(locations), 'description': description,
                   'posted_at': item.get('posted_date'), 'employment_type_raw': item.get('job_schedule_type'),
                   'department': item.get('job_category')}
            jobs[job['url']] = job
            page_jobs.append(job)
        if scrape.get('stop_on_seen') and page_jobs and all(_already_seen(j, seen, company) for j in page_jobs):
            return _finish(jobs)
        hits = int(payload.get('hits') or 0)
        # Amazon's public search reports at most 10,000 hits. A capped
        # listing must not expire older jobs outside that result window.
        if hits and (page + 1) * 100 >= hits:
            return _finish(jobs, 'Amazon search hit its 10000-result window (partial results)' if hits >= 10000 else None)
        time.sleep(0.1)
    return _finish(jobs, 'Amazon page limit reached (partial results)')


def _apple_search_state(text):
    for node in BeautifulSoup(text, 'lxml').find_all('script'):
        script = node.string or node.get_text() or ''
        marker = 'window.__staticRouterHydrationData = JSON.parse('
        start = script.find(marker)
        if start >= 0:
            encoded, _ = json.JSONDecoder().raw_decode(script[start + len(marker):])
            return json.loads(encoded)['loaderData']['search']
    raise ValueError('Apple public search state missing')


def fetch_apple(scrape, seen, company):
    jobs, signatures = {}, set()
    expected_total = 0
    page_size = 0
    incomplete_error = None
    for page in range(1, int(scrape.get('hard_max_pages') or 1000) + 1):
        if expected_total and page_size and (page - 1) * page_size >= expected_total:
            return _finish(jobs, incomplete_error or
                           (f'partial Apple listing: fetched {len(jobs)} of {expected_total} jobs'
                            if len(jobs) < expected_total else None))
        if scan_deadline_exceeded(scrape):
            return _finish(jobs, 'company scan deadline reached (partial Apple results)')
        try:
            # Apple canonicalizes page=1 but returns an empty hydration
            # payload for that URL. Its first page must omit the parameter.
            response = requests.get(scrape['list_url'], params={'page': page} if page > 1 else {}, headers=HEADERS, timeout=25)
            response.raise_for_status()
            data = _apple_search_state(response.text)
            entries = data['searchResults']
            if not entries:
                # The public site intermittently sends an empty hydration
                # payload while still serving active listings. Confirm it
                # once before treating a successful HTTP response as data.
                for _retry in range(2):
                    response = requests.get(scrape['list_url'], params={'page': page} if page > 1 else None,
                                            headers=HEADERS, timeout=25)
                    response.raise_for_status()
                    data = _apple_search_state(response.text)
                    entries = data['searchResults']
                    if entries:
                        break
                if not entries:
                    incomplete_error = 'Apple returned an unconfirmed empty search page (partial results)'
                    if expected_total:
                        continue
                    return _finish(jobs, incomplete_error)
            expected_total = max(expected_total, int(data.get('totalRecords') or 0))
            page_size = page_size or len(entries)
        except Exception as exc:
            return _finish(jobs, str(exc))
        if not entries:
            return _finish(jobs, None if not data.get('totalRecords') else 'Apple ended before the advertised total (partial results)')
        signature = tuple(str(j.get('id')) for j in entries)
        if signature in signatures:
            return _finish(jobs, 'Apple repeated a page (partial results)')
        signatures.add(signature)
        page_jobs = []
        for item in entries:
            title, position = norm(item.get('postingTitle') or ''), str(item.get('id') or '').removeprefix('PIPE-')
            slug = item.get('transformedPostingTitle') or ''
            if not title or not position or not slug:
                continue
            locations = []
            for value in item.get('locations') or []:
                city = value.get('city') or value.get('name') or ''
                label = ', '.join(str(p) for p in (city, value.get('stateProvince'), value.get('countryName')) if p)
                if label and label not in locations:
                    locations.append(label)
            job = {'title': title, 'url': f'https://jobs.apple.com/en-us/details/{position}/{slug}',
                   'ats_id': position, 'requisition_id': item.get('positionId'), 'source': 'apple',
                   'locations_raw': locations, 'location_raw': '; '.join(locations),
                   'description': item.get('jobSummary'), 'posted_at': item.get('postDateInGMT'),
                   'department': (item.get('team') or {}).get('teamName')}
            jobs[job['url']] = job
            page_jobs.append(job)
        if scrape.get('stop_on_seen') and page_jobs and all(_already_seen(j, seen, company) for j in page_jobs):
            return _finish(jobs)
        if expected_total and len(jobs) >= expected_total:
            return _finish(jobs)
        time.sleep(0.1)
    return _finish(jobs, 'Apple page limit reached (partial results)')


def fetch_google(scrape, seen, company):
    jobs, signatures = {}, set()
    for page in range(1, int(scrape.get('hard_max_pages') or 1000) + 1):
        if scan_deadline_exceeded(scrape):
            return _finish(jobs, 'company scan deadline reached (partial Google results)')
        try:
            response = requests.get(scrape['list_url'], params={'page': page}, headers=HEADERS, timeout=25)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'lxml')
        except Exception as exc:
            return _finish(jobs, str(exc))
        current = {}
        for link in soup.select('a[href*="jobs/results/"]'):
            path = link.get('href') or ''
            match = re.search(r'(?:^|/)jobs/results/(\d+)-', path)
            card = link.find_parent('li')
            heading = card.find('h3') if card else None
            if not match or not heading:
                continue
            locations = list(dict.fromkeys(norm(n.get_text(' ', strip=True)) for n in card.select('.r0wTof') if n.get_text(strip=True)))
            job = {'title': norm(heading.get_text(' ', strip=True)),
                   'url': urljoin('https://www.google.com/about/careers/applications/', path).split('?', 1)[0],
                   'ats_id': match.group(1), 'source': 'google', 'locations_raw': locations,
                   'location_raw': '; '.join(locations),
                   'description': '\n'.join(n.get_text(' ', strip=True) for n in card.select('.Xsxa1e'))}
            current[job['url']] = job
        signature = tuple(sorted(current))
        if not current:
            return _finish(jobs, 'Google public search returned no recognizable job cards')
        if signature in signatures:
            return _finish(jobs, 'Google repeated a page (partial results)')
        signatures.add(signature)
        jobs.update(current)
        next_page = any(re.search(r'[?&]page=' + str(page + 1) + r'(?:&|$)', a.get('href') or '') for a in soup.find_all('a', href=True))
        if not next_page:
            return _finish(jobs)
        time.sleep(0.1)
    return _finish(jobs, 'Google page limit reached (partial results)')


def fetch_deshaw(scrape, seen, company):
    try:
        response = requests.get(scrape['list_url'], headers=HEADERS, timeout=25)
        response.raise_for_status()
        node = BeautifulSoup(response.text, 'lxml').find('script', id='__NEXT_DATA__')
        data = json.loads(node.string)['props']['pageProps']
        if data.get('jobsFetchingError') or not all(isinstance(data.get(k), list) for k in ('regularJobs', 'internships')):
            return [], 'D. E. Shaw public job data unavailable'
        jobs = {}
        for row in [*data['regularJobs'], *data['internships']]:
            item = row.get('data') or {}
            if item.get('activeOnJobsListing') is not True:
                continue
            title, slug = norm(item.get('displayName') or ''), item.get('jobUrl') or ''
            if not title or not slug:
                continue
            description = item.get('jobDescription') or {}
            metadata = item.get('jobMetadata') or {}
            locations = [v['name'] for v in metadata.get('jobLocations') or [] if v.get('name')]
            job = {'title': title, 'url': 'https://www.deshaw.com/careers/' + slug.lower(),
                   'ats_id': str(item['id']), 'source': 'deshaw', 'locations_raw': locations,
                   'location_raw': '; '.join(locations), 'employment_type_raw': metadata.get('workStatus'),
                   'department': (item.get('department') or {}).get('name'),
                   'description': '\n\n'.join(str(description[k]) for k in ('websiteDescription', 'responsibilitiesHtml', 'peopleWeAreLookingForStr') if description.get(k))}
            jobs[job['url']] = job
        return _finish(jobs)
    except Exception as exc:
        return [], str(exc)


def fetch_ibm(scrape, seen, company):
    jobs, document_ids = {}, set()
    total = 0
    # The public search orders tied hits inconsistently across replicas.
    # Bounded rereads fill the gaps; only document-id coverage of the
    # advertised total permits a complete result and expiry reconciliation.
    for _round in range(3):
        signatures = set()
        for page in range(int(scrape.get('hard_max_pages') or 1000)):
            if scan_deadline_exceeded(scrape):
                return _finish(jobs, 'company scan deadline reached (partial IBM results)')
            query = {'appId': 'careers', 'scopes': ['careers2'], 'query': {'bool': {'must': []}},
                     'size': 100, 'from': page * 100, 'sort': [{'_score': 'desc'}, {'pageviews': 'desc'}],
                     'lang': 'zz', '_source': ['title', 'url', 'description', 'field_keyword_17',
                                              'field_keyword_08', 'field_keyword_18', 'field_keyword_19']}
            try:
                response = requests.post('https://www-api.ibm.com/search/api/v2', json=query, headers=HEADERS, timeout=25)
                response.raise_for_status()
                data = response.json()
                if data.get('timed_out') or (data.get('_shards') or {}).get('failed'):
                    return _finish(jobs, 'IBM search shards failed (partial results)')
                hits = data['hits']
                entries, total = hits['hits'], hits['total']['value']
            except Exception as exc:
                return _finish(jobs, str(exc))
            if not entries:
                break
            signature = tuple(str(x.get('_id')) for x in entries)
            if signature in signatures:
                break
            signatures.add(signature)
            for entry in entries:
                item = entry.get('_source') or {}
                url, title = item.get('url') or '', norm(item.get('title') or '')
                match = re.search(r'[?&]jobId=(\d+)(?:&|$)', url)
                if not match or not title or not url.startswith('https://careers.ibm.com/'):
                    continue
                document_ids.add(entry['_id'])
                location = item.get('field_keyword_19') or ''
                if isinstance(location, str) and re.search(r', [A-Z]{2}$', location):
                    city, country = location.rsplit(', ', 1)
                    location = city + ', ' + _COUNTRY_CODES.get(country, country)
                jobs[url] = {'title': title, 'url': url, 'ats_id': match.group(1), 'source': 'ibm',
                             'locations_raw': location if isinstance(location, list) else [location],
                             'description': item.get('description'), 'remote_mode': item.get('field_keyword_17'),
                             'department': item.get('field_keyword_08')}
            if total and len(document_ids) >= total:
                return _finish(jobs)
            if (page + 1) * 100 >= total:
                break
            time.sleep(0.1)
        if not total:
            return _finish(jobs)
    return _finish(jobs, f'partial IBM listing: observed {len(document_ids)} of {total} source records')


_GOLDMAN_QUERY = '''query GetRoles($searchQueryInput: RoleSearchQueryInput!) {
  roleSearch(searchQueryInput: $searchQueryInput) {
    totalCount
    items {
      roleId jobTitle status division jobFunction
      locations { primary state country city }
      externalSource { sourceId }
    }
  }
}'''


def fetch_goldman(scrape, seen, company):
    jobs, signatures = {}, set()
    for page in range(int(scrape.get('hard_max_pages') or 1000)):
        if scan_deadline_exceeded(scrape):
            return _finish(jobs, 'company scan deadline reached (partial Goldman Sachs results)')
        query = {'operationName': 'GetRoles', 'query': _GOLDMAN_QUERY,
                 'variables': {'searchQueryInput': {'page': {'pageSize': 100, 'pageNumber': page},
                     'sort': {'sortStrategy': 'RELEVANCE', 'sortOrder': 'DESC'}, 'filters': [],
                     'experiences': ['EARLY_CAREER', 'PROFESSIONAL'], 'searchTerm': ''}}}
        try:
            response = requests.post('https://api-higher.gs.com/gateway/api/v1/graphql',
                                     json=query, headers={**HEADERS, 'Origin': 'https://higher.gs.com',
                                                         'Referer': 'https://higher.gs.com/'}, timeout=25)
            response.raise_for_status()
            payload = response.json()
            if payload.get('errors'):
                return _finish(jobs, 'Goldman Sachs GraphQL returned errors (partial results)')
            data = payload['data']['roleSearch']
            entries, total = data['items'], int(data['totalCount'])
        except Exception as exc:
            return _finish(jobs, str(exc))
        if not entries:
            return _finish(jobs, 'Goldman Sachs ended before its advertised total (partial results)' if len(jobs) < total else None)
        signature = tuple(str(j.get('roleId')) for j in entries)
        if signature in signatures:
            return _finish(jobs, 'Goldman Sachs repeated a page (partial results)')
        signatures.add(signature)
        for item in entries:
            identifier = str((item.get('externalSource') or {}).get('sourceId') or '')
            if not identifier.isdigit() or item.get('status') != 'POSTED' or not item.get('jobTitle'):
                continue
            places = [', '.join(str(value.get(k)) for k in ('city', 'state', 'country') if value.get(k))
                      for value in item.get('locations') or []]
            url = 'https://higher.gs.com/roles/' + identifier
            jobs[url] = {'title': norm(item['jobTitle']), 'url': url, 'ats_id': identifier,
                         'source': 'goldman', 'locations_raw': places,
                         'department': item.get('division'), 'team': item.get('jobFunction')}
        if (page + 1) * 100 >= total:
            return _finish(jobs)
        time.sleep(0.1)
    return _finish(jobs, 'Goldman Sachs page limit reached (partial results)')
