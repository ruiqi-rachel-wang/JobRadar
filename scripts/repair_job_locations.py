#!/usr/bin/env python3
"""Apply reviewed location proposals without changing job identities or dates.

Dry-run by default. Each proposal must name an existing job id/company/URL and
include authoritative locations_raw plus a verification basis. Only location
columns and the supplied source location metadata may be changed. Does not
insert, expire, or delete jobs, run scans, or send alerts.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCATION_KEYS = {
    'categories', 'location', 'country', 'additionalLocations', 'locationString',
    'additionalLocationsList', 'additional_locations', 'full_location', 'multipleLocations',
    'secondaryLocations', 'otherWorkLocations', 'metadata', 'multi_location', 'multi_location_array',
    'isMultiLocation', 'jobRequisitionLocation',
}


@lru_cache(maxsize=4096)
def _canonical_json(raw_json, remote_mode):
    from app.api.utils import normalize_locations
    return json.dumps(normalize_locations(json.loads(raw_json), remote_mode=remote_mode))


def planned_change(row, proposal):
    if (row['company'] != proposal['company'] or row['url'] != proposal['url']
            or not row['is_open']):
        return None, 'identity_or_status_changed'
    if not proposal.get('basis') or not proposal.get('checked_at'):
        raise ValueError('proposal must include source evidence and its verification time')
    if not isinstance(proposal.get('locations_raw'), list) or not proposal['locations_raw']:
        return None, 'no_source_locations'
    canonical = json.loads(_canonical_json(json.dumps(proposal['locations_raw'], sort_keys=True), row['remote_mode']))
    if not canonical['locations'] or canonical['location'] == 'Multiple Locations':
        return None, 'unresolved_source'
    current = json.loads(row['locations'] or '[]')
    clean = lambda items: [{k: v for k, v in item.items() if k != 'raw'} for item in items]
    if (row['location'] == canonical['location'] and clean(current) == clean(canonical['locations'])
            and row['country_iso'] == canonical['country_iso']):
        return None, 'already_correct'
    allowed_locations = proposal.get('expected_locations')
    if allowed_locations is not None and row['location'] not in allowed_locations:
        return None, 'location_changed_since_snapshot'
    raw = json.loads(row['raw_data'] or '{}')
    raw.update({key: value for key, value in (proposal.get('raw_data') or {}).items()
                if key in LOCATION_KEYS})
    return {**canonical, 'location_raw': '; '.join(proposal['locations_raw']), 'raw_data': raw}, 'update'


def repair(proposals, apply=False, backup_dir=None):
    from app.api.database import connect_db
    by_id = {}
    for proposal in proposals:
        if proposal['id'] in by_id:
            raise ValueError('duplicate proposal id: ' + proposal['id'])
        by_id[proposal['id']] = proposal
    counts, companies, changes = Counter(), Counter(), []
    with connect_db(rows=True) as conn:
        ids = list(by_id)
        for start in range(0, len(ids), 400):
            batch = ids[start:start + 400]
            rows = conn.execute(f'SELECT * FROM jobs WHERE id IN ({",".join("?" for _ in batch)})', batch).fetchall()
            counts['not_found'] += len(batch) - len(rows)
            for row in rows:
                row = dict(row)
                change, status = planned_change(row, by_id[row['id']])
                counts[status] += 1
                if change:
                    changes.append((row, change))
                    companies[row['company']] += 1
    backup = None
    if apply and changes:
        if not backup_dir:
            raise ValueError('--backup-dir is required when applying changes')
        directory = Path(backup_dir)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = directory / ('locations-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.jsonl.gz')
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb') as zipped:
            for row, _ in changes:
                zipped.write((json.dumps(row, default=str) + '\n').encode())
        with connect_db(rows=True) as conn:
            for row, change in changes:
                # Compare the current row again inside the write transaction;
                # a scheduled scan may have updated it during the dry run.
                current = conn.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()
                if not current or dict(current) != row:
                    counts['concurrent_change_skipped'] += 1
                    continue
                conn.execute('UPDATE jobs SET location=?, location_raw=?, locations=?, country_iso=?, remote_mode=?, raw_data=? WHERE id=?',
                    (change['location'], change['location_raw'], json.dumps(change['locations']),
                     change['country_iso'], change['remote_mode'], json.dumps(change['raw_data']), row['id']))
                after = dict(conn.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
                mutable = {'location', 'location_raw', 'locations', 'country_iso', 'remote_mode', 'raw_data'}
                if any(after[key] != value for key, value in row.items() if key not in mutable):
                    raise RuntimeError('repair changed a non-location field: ' + row['id'])
                counts['applied'] += 1
    return {'apply': apply, 'proposals': len(by_id), 'counts': dict(counts),
            'companies': dict(companies), 'backup': str(backup) if backup else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('proposals')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir')
    parser.add_argument('--output')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    opener = gzip.open if str(args.proposals).endswith('.gz') else open
    with opener(args.proposals, 'rt') as stream:
        proposals = [json.loads(line) for line in stream if line.strip()]
    result = repair(proposals, args.apply, args.backup_dir)
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
