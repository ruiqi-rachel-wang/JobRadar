#!/usr/bin/env python3
"""Checkpointed, read-only source audit with a hard timeout per company.

Every configured company gets a result, including intentionally disabled ones.
Listing adapters run without job-detail enrichment or database writes. Results
contain source-advertised URLs, not a claim that every URL passed an HTTP check.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fingerprint(company):
    return hashlib.sha256(json.dumps(company, sort_keys=True).encode()).hexdigest()


def adapter_revision():
    digest = hashlib.sha256()
    for path in [Path(__file__), ROOT / 'app/api/apis.py', *sorted((ROOT / 'app/api/ats').glob('*.py'))]:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def result_path(directory, company):
    key = hashlib.sha256(company["name"].encode()).hexdigest()[:20]
    return Path(directory) / "sources" / f"{key}.json"


def probe(company, budget):
    from app.api.apis import FETCHERS, _apply_scan_defaults, _apply_text_filter, _remove_non_jobs
    from app.api.common import robots_allowed
    import requests

    trace = []
    original = requests.sessions.Session.request

    def request(session, method, url, **kwargs):
        if ".myworkdayjobs.com/" in str(url) and "/wday/cxs/" in str(url):
            time.sleep(0.35)
        response = original(session, method, url, **kwargs)
        trace.append({"method": method, "url": str(url), "status": response.status_code,
                      "final_url": response.url})
        return response

    requests.sessions.Session.request = request
    started = time.monotonic()
    scrape = company.get("scrape") or {}
    sources = scrape.get("sources") or [] if scrape.get("mode") == "multi" else [scrape]
    results, jobs, errors = [], {}, []
    for source in sources:
        cfg = _apply_scan_defaults(source, "full")
        cfg.update({
            "fetch_posted_details": False, "fetch_experience_details": False,
            "fetch_location_details": False, "refresh_location": False,
            "require_complete_locations": False,
            "fetch_descriptions": False, "fetch_title_details": False,
            "fetch_details": False, "greenhouse_content": False,
            "max_desc_details": 0, "max_location_details": 0, "max_description_details": 0, "max_title_details": 0,
            "page_workers": 1, "_deadline_monotonic": started + budget - 5,
        })
        mode = cfg.get("mode", "html")
        try:
            url = cfg.get("list_url") or cfg.get("url")
            if url and not robots_allowed(url):
                found, error = [], "blocked by configured robots policy"
            elif mode not in FETCHERS:
                found, error = [], f"unsupported adapter: {mode}"
            else:
                found, error = FETCHERS[mode](cfg, {}, company["name"])
            found = found or []
            _remove_non_jobs(found)
            _apply_text_filter(found, cfg)
        except Exception as exc:
            found, error = [], f"{type(exc).__name__}: {exc}"
        if error:
            errors.append(str(error)[:1200])
        for job in found:
            url = job.get("url")
            if url:
                jobs[url] = {k: job.get(k) for k in (
                    "title", "url", "apply_url", "ats_id", "requisition_id", "source", "posted", "posted_at"
                )}
        results.append({"mode": mode, "count": len(found), "error": str(error)[:1200] if error else None})
    return {
        "name": company["name"], "mode": scrape.get("mode", "html"),
        "status": "error" if errors else "listed" if jobs else "empty",
        "count": len(jobs), "complete": not errors,
        "errors": errors, "sources": results, "jobs": list(jobs.values()),
        "requests": trace[:5] + trace[-15:] if len(trace) > 20 else trace,
        "request_count": len(trace), "elapsed_s": round(time.monotonic() - started, 2),
    }


def isolated_probe(company, budget):
    started = time.monotonic()
    if company.get("scan_enabled", True) is False:
        return {"name": company["name"], "status": "disabled", "count": 0,
                "complete": False, "jobs": [], "errors": [], "elapsed_s": 0}
    env = dict(os.environ, JOBRADAR_HTTP_RETRIES="1", JOBRADAR_HTTP_RETRY_BACKOFF_SECONDS="0.5",
               JOBRADAR_ENABLE_APP_SCHEDULER="false")
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--probe-one", "--timeout", str(budget)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True, cwd=ROOT, env=env,
    )
    try:
        stdout, stderr = process.communicate(json.dumps(company), timeout=budget)
        if process.returncode:
            raise RuntimeError((stderr or stdout)[-1200:])
        # Some optional adapter dependencies log to stdout on import.
        result = json.loads(stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
        result = {"name": company["name"], "status": "timeout", "complete": False,
                  "count": 0, "jobs": [], "errors": [f"hard timeout after {budget}s"]}
    except Exception as exc:
        result = {"name": company["name"], "status": "error", "complete": False,
                  "count": 0, "jobs": [], "errors": [str(exc)[:1200]]}
    result["elapsed_s"] = round(time.monotonic() - started, 2)
    return result


def audit(companies, directory, workers, budget, cache_hours=24):
    directory = Path(directory)
    (directory / "sources").mkdir(parents=True, exist_ok=True)
    pending, counts, cached = [], Counter(), 0
    revision = adapter_revision()
    for company in companies:
        path = result_path(directory, company)
        if path.exists():
            old = json.loads(path.read_text())
            try:
                age = time.time() - datetime.fromisoformat(old['checked_at'].replace('Z', '+00:00')).timestamp()
            except (KeyError, ValueError):
                age = float('inf')
            if (old.get("fingerprint") == fingerprint(company)
                    and old.get('adapter_revision') == revision
                    and 0 <= age < cache_hours * 3600):
                cached += 1
                counts[old["status"]] += 1
                continue
        pending.append(company)
    started = time.monotonic()
    print(json.dumps({"total": len(companies), "cached": cached, "pending": len(pending)}), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(isolated_probe, company, budget): company for company in pending}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            company = futures[future]
            result = future.result()
            result["fingerprint"] = fingerprint(company)
            result['adapter_revision'] = revision
            result["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            result_path(directory, company).write_text(json.dumps(result, ensure_ascii=False, default=str))
            counts[result["status"]] += 1
            if completed % 25 == 0 or completed == len(pending):
                print(json.dumps({"done": cached + completed, "total": len(companies),
                                  "statuses": dict(counts), "elapsed_s": round(time.monotonic() - started, 1)}), flush=True)
    print(json.dumps({"complete": True, "companies": len(companies), "statuses": dict(counts)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies", default=str(ROOT / "app/companies.json"))
    parser.add_argument("--output", default=str(ROOT / 'app/data/company_health' / datetime.now(timezone.utc).date().isoformat()))
    parser.add_argument("--cache-hours", type=float, default=24, help="Reuse matching probes for this long; 0 forces a fresh check")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--only", action="append")
    parser.add_argument("--probe-one", action="store_true")
    args = parser.parse_args()
    if args.probe_one:
        print(json.dumps(probe(json.load(sys.stdin), args.timeout), ensure_ascii=False, default=str))
        return
    companies = json.loads(Path(args.companies).read_text())
    if args.only:
        companies = [c for c in companies if c["name"] in args.only]
    audit(companies, args.output, args.workers, args.timeout, cache_hours=max(0, args.cache_hours))


if __name__ == "__main__":
    main()
