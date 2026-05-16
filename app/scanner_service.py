"""Scan orchestration shared by Flask-triggered and CLI-triggered scans."""
import concurrent.futures
import threading
import time
from datetime import datetime

try:
    from .api.apis import _API_MODES, fetch_jobs
    from .api.job_data import (
        delete_app_state, delete_expired_jobs, get_app_state, load_seen, upsert_jobs,
    )
    from .api.utils import _parse_posted, make_id
    from .scan_store import (
        create_company_scan_status, create_scan_run, finish_company_scan_status,
        finish_scan_run, update_scan_counts,
    )
except ImportError:
    from api.apis import _API_MODES, fetch_jobs
    from api.job_data import (
        delete_app_state, delete_expired_jobs, get_app_state, load_seen, upsert_jobs,
    )
    from api.utils import _parse_posted, make_id
    from scan_store import (
        create_company_scan_status, create_scan_run, finish_company_scan_status,
        finish_scan_run, update_scan_counts,
    )

_seen_lock = threading.Lock()


def run_scan(companies, scan_type="manual", progress=None):
    companies = [company for company in companies if company.get("scan_enabled", True) is not False]
    force_all_new = get_app_state("force_all_new_once") == "1"
    seen = {} if force_all_new else load_seen()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []
    summaries = []
    t_scan = time.monotonic()
    scan_run_id = create_scan_run(scan_type=scan_type, total_companies=len(companies))

    if progress is None:
        progress = [
            {
                "company": c["name"],
                "mode": (c.get("scrape") or {}).get("mode", "html"),
                "status": "pending",
                "count": 0,
                "new": 0,
            }
            for c in companies
        ]

    status_ids = {}
    for i, company in enumerate(companies):
        status_ids[i] = create_company_scan_status(scan_run_id, company["name"])

    def _run_one(i, company):
        progress[i]["status"] = "scanning"
        t0 = time.monotonic()
        try:
            raw, err = fetch_jobs(company, seen)
            return i, company, raw or [], err, round(time.monotonic() - t0, 1)
        except Exception as exc:
            return i, company, [], str(exc), round(time.monotonic() - t0, 1)

    def _commit(i, company, raw, err, duration):
        name = company["name"]
        cats = company.get("categories", [])
        new_this = 0
        with _seen_lock:
            for job in raw:
                job["posted"] = _parse_posted(job.get("posted", ""))
                jid = make_id(name, job.get("url", ""), job.get("title", ""))
                is_new = jid not in seen
                if is_new:
                    seen[jid] = {
                        "first_seen": now_str,
                        "company": name,
                        "title": job["title"],
                        "url": job.get("url", ""),
                        "location": job.get("location", ""),
                        "source": job.get("source", ""),
                        "posted": job.get("posted", ""),
                        "exp_min": job.get("exp_min"),
                        "exp_max": job.get("exp_max"),
                        "exp_label": job.get("exp_label", ""),
                        "exp_source": job.get("exp_source", ""),
                    }
                    new_this += 1
                else:
                    if job.get("location"):
                        seen[jid]["location"] = job.get("location", "")
                    if job.get("posted"):
                        seen[jid]["posted"] = job.get("posted", "")
                    if job.get("source"):
                        seen[jid]["source"] = job.get("source", "")
                    if job.get("exp_label"):
                        seen[jid]["exp_min"] = job.get("exp_min")
                        seen[jid]["exp_max"] = job.get("exp_max")
                        seen[jid]["exp_label"] = job.get("exp_label", "")
                        seen[jid]["exp_source"] = job.get("exp_source", "")
                results.append({
                    **job,
                    "id": jid,
                    "company": name,
                    "categories": cats,
                    "is_new": is_new,
                    "first_seen": seen[jid]["first_seen"],
                    "posted": job.get("posted") or seen[jid].get("posted", ""),
                })

        company_rows = [r for r in results if r["company"] == name]
        if company_rows:
            upsert_jobs(company_rows)
            if raw:
                delete_expired_jobs(name, [r["id"] for r in company_rows])

        entry = {
            "company": name,
            "mode": progress[i]["mode"],
            "status": "done" if not err else "error",
            "count": len(raw),
            "new": new_this,
            "duration_s": duration,
        }
        if err:
            entry["error"] = err
        progress[i].update(entry)
        summaries.append(dict(entry))
        finish_company_scan_status(
            status_ids[i],
            "success" if not err else "failed",
            jobs_found=len(raw),
            new_jobs_found=new_this,
            error_message=err,
        )
        update_scan_counts(scan_run_id, len(summaries))

    api_companies = [
        (i, c) for i, c in enumerate(companies)
        if (c.get("scrape") or {}).get("mode", "html") in _API_MODES
    ]
    browser_companies = [
        (i, c) for i, c in enumerate(companies)
        if (c.get("scrape") or {}).get("mode", "html") not in _API_MODES
    ]

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as api_ex, \
             concurrent.futures.ThreadPoolExecutor(max_workers=2) as pw_ex:
            futures = (
                [api_ex.submit(_run_one, i, c) for i, c in api_companies]
                + [pw_ex.submit(_run_one, i, c) for i, c in browser_companies]
            )
            for future in concurrent.futures.as_completed(futures):
                _commit(*future.result())

        upsert_jobs(results)
        if force_all_new:
            delete_app_state("force_all_new_once")

        failed = [s for s in summaries if s["status"] == "error"]
        elapsed = round(time.monotonic() - t_scan, 1)
        results.sort(key=lambda job: (0 if job["is_new"] else 1, job["company"]))
        status = "success" if not failed else "partial_success"
        finish_scan_run(
            scan_run_id,
            status=status,
            total_jobs_found=len(results),
            new_jobs_found=sum(1 for job in results if job["is_new"]),
            updated_jobs_found=len(results) - sum(1 for job in results if job["is_new"]),
            failed_companies=len(failed),
        )
        return {
            "scan_run_id": scan_run_id,
            "status": status,
            "progress": progress,
            "results": results,
            "new_count": sum(1 for job in results if job["is_new"]),
            "total_jobs": len(results),
            "failed_companies": len(failed),
        }
    except Exception as exc:
        finish_scan_run(
            scan_run_id,
            status="failed",
            total_jobs_found=len(results),
            new_jobs_found=sum(1 for job in results if job.get("is_new")),
            updated_jobs_found=len(results) - sum(1 for job in results if job.get("is_new")),
            failed_companies=len([s for s in summaries if s["status"] == "error"]),
            error_message=str(exc),
        )
        raise

