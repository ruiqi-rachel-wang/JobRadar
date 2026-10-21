import gzip
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from scripts.repair_job_locations import repair
from app.api.utils import normalize_locations


class RepairJobLocationsTests(unittest.TestCase):
    def test_dry_run_backup_identity_preservation_and_repeatability(self):
        db = sqlite3.connect(':memory:')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY,company TEXT,url TEXT,is_open INTEGER,'
                   'location TEXT,location_raw TEXT,locations TEXT,country_iso TEXT,remote_mode TEXT,'
                   'raw_data TEXT,first_seen TEXT,posted TEXT,title TEXT)')
        original = normalize_locations(['Charlotte, NC'])
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', ('one', 'Employer',
            'https://example.com/job/42', 1, original['location'], 'Charlotte, NC',
            json.dumps(original['locations']), 'US', None, '{}', '2026-01-01', '2025-12-30', 'Engineer'))
        proposal = {'id': 'one', 'company': 'Employer', 'url': 'https://example.com/job/42',
            'locations_raw': ['Charlotte, NC', 'New York, NY'], 'basis': 'public provider API',
            'checked_at': '2026-10-21T00:00:00Z'}
        @contextmanager
        def connect(rows=False):
            yield db
        with tempfile.TemporaryDirectory() as directory, patch('app.api.database.connect_db', connect):
            result = repair([proposal])
            self.assertEqual(1, result['counts']['update'])
            self.assertEqual([], list(Path(directory).iterdir()))
            self.assertEqual(original['location'], db.execute('SELECT location FROM jobs').fetchone()[0])
            result = repair([proposal], apply=True, backup_dir=directory)
            self.assertEqual(1, result['counts']['applied'])
            with gzip.open(result['backup'], 'rt') as stream:
                before = json.loads(stream.readline())
            after = dict(db.execute('SELECT * FROM jobs').fetchone())
            self.assertEqual('Charlotte, NC, US; New York, NY, US', after['location'])
            for field in ['id', 'url', 'company', 'is_open', 'title', 'posted', 'first_seen']:
                self.assertEqual(before[field], after[field])
            again = repair([proposal], apply=True, backup_dir=directory)
            self.assertEqual(1, again['counts']['already_correct'])
            self.assertIsNone(again['backup'])
            changed = {**proposal, 'url': 'https://example.com/job/someone-else'}
            self.assertEqual(1, repair([changed])['counts']['identity_or_status_changed'])

    def test_stored_workday_repair_does_not_overwrite_boa_dedicated_locations(self):
        from scripts.build_location_audit_report import stored_workday_info
        row = {'source': 'workday', 'location_raw': 'Charlotte, NC', 'raw_data': {
            'location': 'Charlotte, NC', 'additionalLocations': ['ONE BRYANT PARK'],
            'locationString': 'USA, NC, Charlotte', 'additionalLocationsList': ['USA, NY, New York']}}
        self.assertIsNone(stored_workday_info(row))
        normal = {**row, 'raw_data': {'location': 'Getafe Area', 'additionalLocations': ['Hamburg Area'],
                   'country': {'descriptor': 'Spain'}}}
        self.assertEqual('Getafe Area', stored_workday_info(normal)['location'])

    def test_repair_skips_locations_changed_by_a_newer_scan(self):
        from scripts.repair_job_locations import planned_change
        current = normalize_locations(['London, UK'])
        row = {'company': 'Employer', 'url': 'https://example.com/job/42', 'is_open': 1,
               'location': current['location'], 'locations': json.dumps(current['locations']),
               'country_iso': 'GB', 'remote_mode': None, 'raw_data': '{}'}
        proposal = {'company': row['company'], 'url': row['url'],
                    'locations_raw': ['Charlotte, NC', 'New York, NY'],
                    'basis': 'public API', 'checked_at': '2026-10-21T05:00:00Z',
                    'expected_locations': ['Charlotte, NC, US']}
        change, status = planned_change(row, proposal)
        self.assertIsNone(change)
        self.assertEqual('location_changed_since_snapshot', status)
