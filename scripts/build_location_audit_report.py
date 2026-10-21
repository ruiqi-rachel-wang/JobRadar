#!/usr/bin/env python3
"""Build the company inventory and reviewable location-only repair proposals."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def json_files(directory):
    for path in Path(directory).glob('*.json'):
        try:
            yield json.loads(path.read_text())
        except (ValueError, OSError):
            continue  # An audit checkpoint may still be being written.


def stored_workday_info(row):
    raw = row.get('raw_data') or {}
    # BOA's dedicated endpoint supplies richer locationString and
    # additionalLocationsList fields. Its legacy Workday descriptors are
    # incomplete and must never replace the already-repaired BOA locations.
    if (row.get('source') != 'workday' or not raw.get('additionalLocations')
            or 'locationString' in raw or 'additionalLocationsList' in raw):
        return None
    primary = raw.get('location') or row.get('location_raw')
    if not primary or primary == 'Multiple Locations':
        return None
    return {'location': str(primary).split(';')[0],
            'additionalLocations': raw['additionalLocations'], 'country': raw.get('country')}


def build(directory, output):
    from app.api.ats.workday import _workday_detail_location
    from app.api.ats.greenhouse import _greenhouse_locations
    from scripts.repair_job_locations import planned_change
    @lru_cache(maxsize=4096)
    def workday_location(info_json):
        return _workday_detail_location(json.loads(info_json))
    directory, output = Path(directory), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    companies = json.loads((ROOT / 'app/companies.json').read_text())
    inventory = {c['name']: {'company': c['name'], 'mode': (c.get('scrape') or {}).get('mode', 'html'),
                 'scan_enabled': c.get('scan_enabled', True), 'open_jobs_reviewed': 0,
                 'stored_multi_location': 0, 'stored_missing_location': 0, 'stored_placeholders': 0,
                 'source_status': 'not_freshly_probed', 'fresh_source_jobs': 0,
                 'source_errors': '', 'detail_checks': 0, 'detail_blocked_or_failed': 0,
                 'location_repairs_proposed': 0} for c in companies}
    sources = list(json_files(directory / 'sources'))
    index = {}
    for source in sources:
        entry = inventory.get(source['name'])
        if entry:
            entry.update(source_status=source['status'], fresh_source_jobs=source['count'],
                         source_errors=' | '.join(source['errors']))
        for job in source['jobs']:
            for key in [('url', job['url']), ('ats', str(job.get('ats_id') or ''))]:
                if key[1]:
                    index[(source['name'], *key)] = (job, source)
    for detail in json_files(directory / 'details'):
        entry = inventory.get(detail['company'])
        if entry:
            entry['detail_checks'] += 1
            entry['detail_blocked_or_failed'] += detail['status'] in {'blocked', 'http_error', 'error'}
    confirmed = {d['id']: d for d in json_files(directory / 'confirmed-details') if d['status'] == 'confirmed'}
    workday = {d['id']: d for d in json_files(directory / 'workday-details') if d['status'] == 'resolved'}
    live_rechecks = {d['id']: d for d in json_files(directory / 'live-recheck-details') if d['status'] == 'resolved'}
    phase1_path = directory / 'phase1-expected-locations.json'
    phase1_expected = json.loads(phase1_path.read_text()) if phase1_path.exists() else {}
    counts, by_source, by_basis, changes, samples = Counter(), Counter(), Counter(), [], []
    snapshot_count = 0
    with gzip.open(directory / 'open-locations.jsonl.gz', 'rt') as stream:
        for line in stream:
            row = json.loads(line)
            snapshot_count += 1
            entry = inventory.get(row['company'])
            if entry:
                entry['open_jobs_reviewed'] += 1
                entry['stored_multi_location'] += len(row['locations']) > 1
                entry['stored_missing_location'] += not bool(row['location'])
                entry['stored_placeholders'] += 'locations' in (row['location'] or '').lower()
            proposal = None
            info = stored_workday_info(row)
            if info:
                loc = workday_location(json.dumps(info, sort_keys=True))
                if loc:
                    proposal = {'locations_raw': [p.strip() for p in loc.split(';') if p.strip()],
                        'basis': 'stored authoritative Workday additionalLocations; no inherited primary country',
                        'checked_at': '2026-10-21T05:00:00Z'}
            match = (index.get((row['company'], 'ats', str(row.get('ats_id') or '')))
                     or index.get((row['company'], 'url', row['url'])))
            if match:
                job, source = match
                if job.get('source') == 'greenhouse':
                    job = {**job, 'locations_raw': _greenhouse_locations({
                        'location': {'name': job.get('location_raw') or ''},
                        'metadata': (job.get('raw_data') or {}).get('metadata') or []})}
                if job.get('locations_raw'):
                    proposal = {key: job[key] for key in ('locations_raw', 'raw_data') if key in job}
                    proposal.update(basis='fresh ' + source['mode'] + ' public job feed',
                                    checked_at=source['checked_at'], source_complete=source['complete'])
            if row['id'] in confirmed and row['source'] in {'icims', 'html', 'avature', 'jobvite', 'talentbrew', 'playwright'}:
                detail = confirmed[row['id']]
                proposal = {key: detail[key] for key in ('locations_raw', 'basis', 'checked_at')}
            if row['id'] in workday:
                detail = workday[row['id']]
                proposal = {key: detail[key] for key in ('locations_raw', 'raw_data', 'checked_at') if key in detail}
                if detail.get('raw_data'):
                    resolved = workday_location(json.dumps(detail['raw_data'], sort_keys=True))
                    proposal['locations_raw'] = [p.strip() for p in resolved.split(';') if p.strip()]
                proposal['basis'] = 'fresh official Workday jobPostingInfo'
            if row['id'] in live_rechecks:
                detail = live_rechecks[row['id']]
                proposal = {key: detail[key] for key in ('locations_raw', 'raw_data', 'checked_at') if key in detail}
                if detail.get('raw_data'):
                    resolved = workday_location(json.dumps(detail['raw_data'], sort_keys=True))
                    proposal['locations_raw'] = [p.strip() for p in resolved.split(';') if p.strip()]
                proposal['basis'] = 'live recheck of Workday jobPostingInfo after scheduled scans'
            if not proposal:
                continue
            if (proposal.get('locations_raw') == [row['location_raw']]
                    and len(row['locations']) == 1 and row['location']):
                counts['unchanged_primary_source'] += 1
                continue
            proposal.update({key: row[key] for key in ('id', 'company', 'title', 'url', 'source')})
            proposal['expected_locations'] = list(dict.fromkeys([
                row['location'], *([phase1_expected[row['id']]] if row['id'] in phase1_expected else []),
                *([live_rechecks[row['id']]['expected_current_location']] if row['id'] in live_rechecks else []),
            ]))
            fake_row = {**row, 'is_open': 1, 'remote_mode': None, 'country_iso': None,
                        'raw_data': json.dumps(row['raw_data']), 'locations': json.dumps(row['locations'])}
            # Preserve the existing workplace mode when the snapshot has one.
            modes = {x.get('remote_mode') for x in row['locations'] if x.get('remote_mode')}
            if len(modes) == 1:
                fake_row['remote_mode'] = next(iter(modes))
            countries = {x.get('country_iso') for x in row['locations'] if x.get('country_iso')}
            if len(countries) == 1:
                fake_row['country_iso'] = next(iter(countries))
            change, status = planned_change(fake_row, proposal)
            if (change is None and status == 'already_correct'
                    and row['id'] in phase1_expected
                    and phase1_expected[row['id']] != row['location']):
                # A later normalizer improvement can remove a redundant
                # label introduced by the first pass, returning to the
                # original display. That cleanup still needs a proposal.
                change, status = planned_change({**fake_row, 'location': phase1_expected[row['id']]}, proposal)
            counts[status] += 1
            if not change:
                continue
            if (change['location'] == row['location']
                    and phase1_expected.get(row['id'], row['location']) == change['location']):
                counts['unchanged_display'] += 1
                continue
            if (proposal['basis'].startswith('stored authoritative Workday')
                    and len(change['locations']) < len(row['locations'])):
                counts['stored_evidence_would_reduce_locations_skipped'] += 1
                continue
            changes.append(proposal)
            by_source[row['source']] += 1
            by_basis[proposal['basis']] += 1
            if entry:
                entry['location_repairs_proposed'] += 1
            old = {x.get('display', '').casefold() for x in row['locations']}
            added = [x['display'] for x in change['locations'] if x['display'].casefold() not in old]
            samples.append({'company': row['company'], 'id': row['id'], 'title': row['title'], 'source': row['source'],
                            'before': row['location'], 'after': change['location'], 'added': '; '.join(added),
                            'url': row['url'], 'basis': proposal['basis']})
    with gzip.open(directory / 'location-proposals.jsonl.gz', 'wt') as stream:
        for proposal in changes:
            stream.write(json.dumps(proposal) + '\n')
    with (output / 'all_companies.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(next(iter(inventory.values()))), lineterminator='\n')
        writer.writeheader()
        writer.writerows(sorted(inventory.values(), key=lambda x: x['company'].casefold()))
    with (output / 'location_repairs.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(samples[0]) if samples else ['company'], lineterminator='\n')
        writer.writeheader()
        writer.writerows(samples)
    summary = {'configured_companies': len(inventory), 'open_records_reviewed': snapshot_count,
               'fresh_feed_companies': len(sources), 'fresh_feed_postings': sum(s['count'] for s in sources),
               'fresh_feed_statuses': dict(Counter(s['status'] for s in sources)),
               'proposals': len(changes), 'companies_with_proposals': sum(e['location_repairs_proposed'] > 0 for e in inventory.values()),
               'by_source': dict(by_source), 'by_basis': dict(by_basis),
               'detail_checks': sum(e['detail_checks'] for e in inventory.values()),
               'workday_detail_statuses': dict(Counter(d['status'] for d in json_files(directory / 'workday-details'))),
               'detail_statuses': dict(Counter(d['status'] for d in json_files(directory / 'details'))),
               'comparison_counts': dict(counts)}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default=str(ROOT / 'app/data/company_health/2026-10-21-locations'))
    parser.add_argument('--output', default=str(ROOT / 'docs/location_audit_2026-10-21'))
    args = parser.parse_args()
    build(args.data, args.output)


if __name__ == '__main__':
    main()
