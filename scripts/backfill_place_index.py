#!/usr/bin/env python3
"""Rebuild derived geographic membership without changing any job fields."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
import time
import sqlite3
import gzip
import itertools

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--report',type=Path)
    parser.add_argument('--staging-db',type=Path,help='Build memberships in a separate SQLite file without taking the live writer lock')
    parser.add_argument('--since',help='Refresh jobs scanned since this database timestamp')
    parser.add_argument('--snapshot',type=Path,help='Read an exported JSONL(.gz) location snapshot for a bulk build')
    args=parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT/'.env')
    os.environ['JOBRADAR_ENABLE_APP_SCHEDULER']='false'
    from app.api.database import IS_POSTGRES, connect_db
    from app.api.geography import init_place_index,job_place_ids,sync_job_places
    last='';counts=Counter();started=time.monotonic()
    stage = None
    if args.staging_db:
        if args.staging_db.exists():raise SystemExit('Staging database already exists')
        stage=sqlite3.connect(args.staging_db)
        # This file is disposable derived data. Avoid journal fsyncs and build
        # the lookup index once after the load; live DB pragmas are unchanged.
        stage.execute('PRAGMA journal_mode=OFF')
        stage.execute('PRAGMA synchronous=OFF')
        stage.execute('PRAGMA cache_size=-262144')
        stage.execute('PRAGMA temp_store=MEMORY')
        stage.execute('CREATE TABLE job_places (job_id TEXT NOT NULL,place_id TEXT NOT NULL,PRIMARY KEY(job_id,place_id))')
    if args.apply and not stage:
        with connect_db() as conn:init_place_index(conn)
    # SQLite otherwise chooses its is_open/date index and sorts the entire
    # open-job set for every page. Walk the primary-key range once instead.
    source = 'jobs' if IS_POSTGRES else 'jobs INDEXED BY sqlite_autoindex_jobs_1'
    snapshot = (gzip.open(args.snapshot,'rt') if args.snapshot.suffix == '.gz' else args.snapshot.open()) if args.snapshot else None
    while True:
        if snapshot:
            rows=[json.loads(line) for line in itertools.islice(snapshot,1000)]
        else:
            with connect_db(rows=True) as conn:
                since_clause = ' AND last_seen_at>=?' if args.since else ''
                params = (last,args.since) if args.since else (last,)
                rows=[dict(r) for r in conn.execute(
                    'SELECT id,location,location_raw,locations,country_iso,remote_mode '
                    f'FROM {source} WHERE is_open=TRUE AND id>?{since_clause} ORDER BY id LIMIT 1000',params).fetchall()]
        if not rows:break
        memberships=[]
        for row in rows:
            ids=job_place_ids(row)
            counts['jobs_checked']+=1
            counts['jobs_with_city']+=any(p.startswith('city:') for p in ids)
            counts['jobs_with_region']+=any(p.startswith('region:') for p in ids)
            counts['jobs_with_country']+=any(p.startswith('country:') for p in ids)
            counts['jobs_with_remote_scope']+='workplace:remote' in ids
            counts['geographic_memberships']+=len(ids)
            counts['jobs_without_geographic_place']+=not any(p.startswith(('city:','region:','country:')) for p in ids)
            memberships.extend((row['id'],p) for p in sorted(ids))
        if stage:
            stage.executemany('INSERT INTO job_places(job_id,place_id) VALUES (?,?)',memberships)
        elif args.apply:
            with connect_db() as conn:sync_job_places(conn,rows)
        last=rows[-1]['id']
        if counts['jobs_checked']%25000==0:print(json.dumps(dict(counts)),flush=True)
    if snapshot:snapshot.close()
    if stage:
        init_place_index(stage)
        stage.commit()
        stage.close()
    report={'apply':args.apply,'staging_db':str(args.staging_db) if args.staging_db else None,'counts':dict(counts),'elapsed_seconds':round(time.monotonic()-started,2),
            'job_fields_modified':False,'unknown_locations_match_selected_geography':False}
    if args.report:args.report.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))

if __name__=='__main__':main()
