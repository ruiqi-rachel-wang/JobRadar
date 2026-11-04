#!/usr/bin/env python3
"""Close explicitly reviewed non-job URLs, preserving their rows and saved IDs.

Dry run by default. Each proposal identifies the company, URL, id and evidence.
Only is_open changes; a complete private backup precedes guarded writes.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('proposals', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    os.environ['JOBRADAR_ENABLE_APP_SCHEDULER'] = 'false'
    from app.api.database import IS_POSTGRES, connect_db
    proposals = json.loads(args.proposals.read_text())
    if len({p['id'] for p in proposals}) != len(proposals):
        raise ValueError('Duplicate proposal id')
    changes = []
    counts = Counter()
    with connect_db(rows=True) as conn:
        for p in proposals:
            if not p.get('basis') or not p.get('checked_at'):
                raise ValueError('Source evidence required')
            row = conn.execute('SELECT * FROM jobs WHERE id=?', (p['id'],)).fetchone()
            if not row or row['company'] != p['company'] or row['url'] != p['url']:
                counts['identity_changed'] += 1
            elif not row['is_open']:
                counts['already_closed'] += 1
            else:
                changes.append(dict(row))
    backup = None
    if args.apply and changes:
        if not args.backup_dir:
            raise ValueError('--backup-dir is required')
        args.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = args.backup_dir / ('non-job-pages-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.jsonl.gz')
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream, gzip.GzipFile(fileobj=stream, mode='wb') as zipped:
            for row in changes:
                zipped.write((json.dumps(row, default=str) + '\n').encode())
        with connect_db(rows=True) as conn:
            if not IS_POSTGRES and not getattr(getattr(conn, "raw", conn), "in_transaction", False):
                conn.execute("BEGIN IMMEDIATE")
            for row in changes:
                current = conn.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()
                if not current or dict(current) != row:
                    counts['concurrent_change_skipped'] += 1
                    continue
                conn.execute('UPDATE jobs SET is_open=FALSE WHERE id=?', (row['id'],))
                after = dict(conn.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
                if any(after[k] != v for k, v in row.items() if k != 'is_open'):
                    raise RuntimeError('Unexpected non-status change')
                counts['closed'] += 1
    report = {'apply': args.apply, 'proposals': len(proposals), 'eligible': len(changes),
              'counts': dict(counts), 'backup': str(backup) if backup else None}
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
