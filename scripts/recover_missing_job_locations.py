#!/usr/bin/env python3
"""Recheck missing job locations on official job documents (no DB writes).

Checkpoint every URL so a long review can resume. Workday's job-specific CXS
JSON is the official document for its JS pages. Other pages use explicit
JobPosting/labelled-location fields; employer headquarters are never inferred.
"""
import argparse
import collections
import concurrent.futures
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from app.api.ats.base import parse_detail_description
from app.api.ats.workday import _workday_location_parts
from app.api.common import HEADERS, http_session


def workday_url(job_url, scrape):
    public = scrape.get('list_url','')
    host = urlparse(public).netloc
    match = re.match(r'([^.]+)\.(wd\d+\.myworkdayjobs\.com)$',host)
    if match:
        tenant = match[1]
        board = [part for part in urlparse(public).path.split('/') if part and not re.fullmatch(r'[a-z]{2}-[A-Z]{2}',part)][0]
        origin = 'https://'+host
    else:
        match = re.match(r'/recruiting/([^/]+)/([^/]+)',urlparse(public).path)
        if not match:
            return None
        tenant,board=match[1],match[2]
        origin='https://'+tenant+'.'+host.replace('myworkdaysite.com','myworkdayjobs.com')
    path = urlparse(job_url).path
    if '/job/' not in path:
        return None
    return origin+'/wday/cxs/'+tenant+'/'+board+path[path.index('/job/'):]


def workday_locations(data):
    info = data.get('jobPostingInfo') or {}
    country = info.get('country') or {}
    country = (country.get('descriptor') or country.get('alpha2Code') or '') if isinstance(country,dict) else str(country)
    values = []
    for key in ('location','jobRequisitionLocation'):
        for value in _workday_location_parts(info.get(key)):
            if country and not value.lower().endswith(country.lower()):
                value += ', '+country
            if value not in values:
                values.append(value)
    for value in _workday_location_parts(info.get('additionalLocations')):
        if value not in values:
            values.append(value)
    return values


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--companies',type=Path,default=ROOT/'app/companies.json')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=24)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--public-html',action='store_true',help='Read the official HTML job document instead of its Workday JSON representation')
    parser.add_argument('--per-host',type=int,default=3)
    parser.add_argument('--delay',type=float,default=0)
    args=parser.parse_args()
    configs={c['name']:c for c in json.loads(args.companies.read_text())}
    opener=gzip.open if args.snapshot.suffix=='.gz' else open
    with opener(args.snapshot,'rt') as stream:rows=[json.loads(line) for line in stream if line.strip()]
    grouped=collections.defaultdict(list)
    for row in rows:
        grouped[row['url']].append(row)
    completed=set()
    if args.resume and args.output.exists():
        for line in args.output.read_text().splitlines():
            if line.strip():completed.add(json.loads(line)['url'])
    by_host=collections.defaultdict(collections.deque)
    for url,items in grouped.items():
        if url not in completed:
            by_host[urlparse(url).netloc].append((url,items))
    pending=[]
    while by_host:
        for host in list(by_host):
            pending.append(by_host[host].popleft())
            if not by_host[host]:del by_host[host]
    locks=collections.defaultdict(lambda:threading.Semaphore(max(1,args.per_host)))
    failures=collections.Counter()
    state_lock=threading.Lock()
    def check(item):
        url,items=item;row=items[0];host=urlparse(url).netloc
        cfg=configs.get(row['company'],{})
        record={'url':url,'ids':[r['id'] for r in items],'company':row['company'],
                'checked_at':datetime.now(timezone.utc).isoformat(),'locations_raw':[]}
        if cfg.get('scan_enabled') is False:
            record.update(status='source_disabled',reason='Company already disabled; no new location inferred')
            return record
        target=workday_url(url,cfg.get('scrape',{})) if row.get('source')=='workday' and not args.public_html else None
        target=target or url
        with locks[host]:
            with state_lock:
                blocked=failures[host]>=3
            if blocked:
                record.update(status='host_blocked',reason='Three current requests to this host returned 403/429; remaining URLs unverified')
                return record
            try:
                if args.delay:time.sleep(min(args.delay,5))
                response=http_session().get(target,headers={**HEADERS,'Accept':'application/json' if '/wday/cxs/' in target else 'text/html,application/xhtml+xml'},timeout=(8,25),verify=cfg.get('scrape',{}).get('verify_ssl',True))
                record['http_status']=response.status_code
                record['source_url']=target
                record['final_url']=response.url
                if response.status_code in (403,429):
                    with state_lock:failures[host]+=1
                    record['status']='blocked';return record
                if response.status_code in (404,410):
                    record['status']='not_found';return record
                response.raise_for_status()
                if '/wday/cxs/' in target:
                    values=workday_locations(response.json())
                else:
                    detail=parse_detail_description(response.text)
                    values=detail.get('locations_raw') or []
                record.update(status='location_fields_found' if values else 'no_explicit_location_fields',locations_raw=values)
            except Exception as exc:
                record.update(status='request_failed',reason=type(exc).__name__)
        return record
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.output.exists() and not args.resume:raise SystemExit('Output already exists; use --resume')
    counts=collections.Counter()
    fd=os.open(args.output,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
    with os.fdopen(fd,'a') as out, concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(check,item) for item in pending]
        for index,future in enumerate(concurrent.futures.as_completed(futures),1):
            result = future.result()
            out.write(json.dumps(result,ensure_ascii=False)+'\n');out.flush();counts[result['status']]+=len(result['ids'])
            if index%200==0 or index==len(pending):
                print(json.dumps({'urls_checked':index,'urls_pending_initially':len(pending),'jobs_by_status':dict(counts)}),flush=True)
    print(json.dumps({'total_jobs_requested':len(rows),'unique_urls':len(grouped),'already_checkpointed':len(completed),'new_statuses':dict(counts)}))

if __name__=='__main__':main()
