#!/usr/bin/env python3
"""Read-only, checkpointed audit of explicit multi-location source fields.

Inventory every configured company and every open row in a location snapshot.
Freshly probe the shared adapters that previously discarded secondary locations.
Partial, blocked and timed-out sources remain explicitly unverified. This tool
never inserts jobs, expires postings, sends alerts, or writes to the jobs DB.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODES = {'greenhouse', 'lever', 'oraclecloud', 'jobsapi', 'phenom'}


def result_path(directory, name):
    return Path(directory) / 'sources' / (hashlib.sha256(name.encode()).hexdigest()[:20] + '.json')


def _without_links(value):
    if isinstance(value, dict):
        return {k: _without_links(v) for k, v in value.items() if k != 'links'}
    if isinstance(value, list):
        return [_without_links(v) for v in value]
    return value


def probe(company, budget):
    from app.api.apis import FETCHERS, _apply_text_filter, _remove_non_jobs
    from app.api.common import robots_allowed
    cfg = dict(company['scrape'])
    cfg.update(fetch_experience_details=False, fetch_descriptions=False,
               allow_browser_fallback=False, stop_on_seen=False,
               _deadline_monotonic=time.monotonic() + budget - 5)
    if cfg.get('list_url') and cfg.get('respect_robots') is not False and not robots_allowed(cfg['list_url']):
        jobs, error = [], 'blocked by configured robots policy'
    else:
        jobs, error = FETCHERS[cfg['mode']](cfg, {}, company['name'])
        _remove_non_jobs(jobs)
        _apply_text_filter(jobs, cfg)
    fields = ('url', 'title', 'ats_id', 'source', 'location', 'location_raw', 'locations_raw', 'raw_data')
    return {'name': company['name'], 'mode': cfg['mode'], 'complete': not bool(error),
            'errors': [str(error)] if error else [], 'count': len(jobs),
            'status': 'partial' if error and jobs else 'error' if error else 'listed' if jobs else 'empty',
            'jobs': [{k: _without_links(j[k]) for k in fields if k in j} for j in jobs]}


def isolated_probe(company, budget):
    env = dict(os.environ, JOBRADAR_ENABLE_APP_SCHEDULER='false', JOBRADAR_HTTP_RETRIES='0')
    p = subprocess.Popen([sys.executable, __file__, '--probe-one', '--timeout', str(budget)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True, cwd=ROOT, env=env)
    started = time.monotonic()
    try:
        out, err = p.communicate(json.dumps(company), timeout=budget)
        if p.returncode:
            raise RuntimeError((err or out)[-1200:])
        result = json.loads(out.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.communicate()
        result = {'name': company['name'], 'mode': company['scrape']['mode'],
                  'status': 'timeout', 'complete': False, 'jobs': [], 'count': 0,
                  'errors': [f'hard timeout after {budget}s']}
    except Exception as exc:
        result = {'name': company['name'], 'mode': company['scrape']['mode'],
                  'status': 'error', 'complete': False, 'jobs': [], 'count': 0, 'errors': [str(exc)]}
    result.update(checked_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  elapsed_s=round(time.monotonic() - started, 2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--companies', default=str(ROOT / 'app/companies.json'))
    parser.add_argument('--output', default=str(ROOT / 'app/data/company_health/2026-10-21-locations'))
    parser.add_argument('--timeout', type=int, default=240)
    parser.add_argument('--workers', type=int, default=12)
    parser.add_argument('--probe-one', action='store_true')
    parser.add_argument('--only', action='append')
    parser.add_argument('--resume', action='store_true', help='Reuse this run\'s existing checkpoints')
    args = parser.parse_args()
    if args.probe_one:
        print(json.dumps(probe(json.load(sys.stdin), args.timeout), default=str))
        return
    directory = Path(args.output)
    (directory / 'sources').mkdir(parents=True, exist_ok=True)
    all_companies = json.loads(Path(args.companies).read_text())
    selected = [c for c in all_companies if c.get('scan_enabled', True)
                and (c.get('scrape') or {}).get('mode') in MODES
                and (not args.only or c['name'] in args.only)]
    pending = [c for c in selected if not (args.resume and result_path(directory, c['name']).exists())]
    print(json.dumps({'configured': len(all_companies), 'selected': len(selected), 'pending': len(pending)}), flush=True)
    counts = Counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(isolated_probe, c, args.timeout): c for c in pending}
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            c = futures[future]
            result = future.result()
            result_path(directory, c['name']).write_text(json.dumps(result, default=str))
            counts[result['status']] += 1
            if index % 20 == 0 or index == len(pending):
                print(json.dumps({'done': index, 'total': len(pending), 'statuses': counts}), flush=True)


if __name__ == '__main__':
    main()
