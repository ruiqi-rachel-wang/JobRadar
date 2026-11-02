#!/usr/bin/env python3
"""Preview country repairs from preserved employer location labels.

Only propose rows where the revised normalizer resolves a previously missing
country without removing any known country. Apply the resulting proposals with
repair_job_locations.py, which backs up and guards each write separately.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.utils import _COUNTRY_ISO_BY_DISPLAY, _country_code, normalize_locations


def country_proposal(row, checked_at):
    from scripts.backfill_canonical_locations import _decode_locations, _raw_values
    old_locations = [item for item in _decode_locations(row.get('locations')) if isinstance(item, dict)]
    old_unknown = sum(not item.get('country_iso') for item in old_locations)
    if old_locations and not old_unknown:
        return None
    raw = _raw_values(row)
    if not raw:
        raw = [row['location']] if row.get('location') else []
    if not raw:
        return None
    # Some historical primary labels contain the entire semicolon-separated
    # list as well as its individual offices. Keep each source office once,
    # so the provider's primary country qualifies only the first office.
    raw = list(dict.fromkeys(part.strip() for value in raw for part in value.split(';') if part.strip()))

    provider_country = row.get('provider_country')
    if isinstance(provider_country, str):
        try:
            provider_country = json.loads(provider_country)
        except ValueError:
            pass
    if isinstance(provider_country, dict):
        provider_country = provider_country.get('descriptor') or provider_country.get('alpha2Code')
    # Workday's country belongs to the primary office; never assign it to
    # secondary offices in other countries. Its raw label can be an address.
    if row.get('source') == 'workday' and provider_country:
        country = _country_code(provider_country)
        if country and _COUNTRY_ISO_BY_DISPLAY.get(country.lower()):
            primary = normalize_locations(raw[0])
            if len(primary['locations']) == 1 and not primary['country_iso']:
                raw[0] = raw[0] + ', ' + country

    new = normalize_locations(raw, remote_mode=row.get('remote_mode'))
    old_known = {loc['country_iso'] for loc in old_locations if loc.get('country_iso')}
    if row.get('country_iso'):
        old_known.add(row['country_iso'])
    new_known = {loc['country_iso'] for loc in new['locations'] if loc.get('country_iso')}
    new_unknown = sum(not loc.get('country_iso') for loc in new['locations'])
    newly_qualified_offices = sum(bool(loc.get('country_iso')) for loc in new['locations']) > sum(
        bool(loc.get('country_iso')) for loc in old_locations
    )
    if (not new_known or not old_known <= new_known
            or (new_unknown >= (old_unknown or 1) and not newly_qualified_offices)):
        return None
    return {
        'id': row['id'], 'company': row['company'], 'url': row['url'],
        'locations_raw': raw, 'expected_locations': [row['location']],
        'basis': 'Reparsed preserved employer location labels and explicit primary Workday country',
        'checked_at': checked_at,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='JSONL or JSONL.gz proposals (no database writes)')
    parser.add_argument('--report', help='Aggregate JSON report')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    from app.api.database import IS_POSTGRES, connect_db
    if IS_POSTGRES:
        country = "raw_data -> 'country'"
    else:
        country = "json_extract(CASE WHEN json_valid(raw_data) THEN raw_data ELSE '{}' END, '$.country')"
    fields = (
        'id, company, url, location, location_raw, locations, country_iso, remote_mode, source, '
        f"CASE WHEN source = 'workday' THEN {country} ELSE NULL END AS provider_country"
    )
    checked_at = datetime.now(timezone.utc).isoformat()
    companies, checked, proposed = Counter(), 0, 0
    output = Path(args.output)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        target = gzip.GzipFile(fileobj=stream, mode='wb') if output.suffix == '.gz' else stream
        try:
            with connect_db(rows=True) as conn:
                cursor = conn.execute(f'SELECT {fields} FROM jobs WHERE is_open = TRUE')
                while True:
                    batch = cursor.fetchmany(500)
                    if not batch:
                        break
                    for record in batch:
                        checked += 1
                        proposal = country_proposal(dict(record), checked_at)
                        if proposal:
                            target.write((json.dumps(proposal, ensure_ascii=False) + '\n').encode())
                            companies[proposal['company']] += 1
                            proposed += 1
        finally:
            if target is not stream:
                target.close()
    report = {'open_jobs_checked': checked, 'proposed_repairs': proposed,
              'companies': dict(companies), 'output': str(output), 'database_writes': 0}
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
