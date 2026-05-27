"""
Scan orchestration — two modes, one codebase.

  fresh (default)
      Collects only new jobs. Stops per-company as soon as a full page of
      already-known jobs is encountered. Never marks jobs as closed.
      Run this frequently (e.g. every 2–4 h).

  full
      Comprehensive audit. Scans every page regardless of seen status,
      updates changed fields (location, exp, skills…) and soft-deletes jobs
      that disappeared from the source (is_open=0). Run once a day.

Both modes share the same fetchers, enrichment pipeline, and DB writes.
"""
import concurrent.futures
import threading
import time
from datetime import datetime

try:
    from .api.apis import _API_MODES, fetch_jobs
    from .api.job_data import (
        delete_app_state, delete_expired_jobs, get_app_state, load_seen, upsert_jobs,
    )
    from .api.utils import make_id
    from .scan_store import (
        create_company_scan_status, create_scan_run,
        finish_company_scan_status, finish_scan_run, update_scan_counts,
    )
except ImportError:
    from api.apis import _API_MODES, fetch_jobs
    from api.job_data import (
        delete_app_state, delete_expired_jobs, get_app_state, load_seen, upsert_jobs,
    )
    from api.utils import make_id
    from scan_store import (
        create_company_scan_status, create_scan_run,
        finish_company_scan_status, finish_scan_run, update_scan_counts,
    )

# Guards concurrent writes to the shared `seen` dict across threads.
_seen_lock = threading.Lock()

# Thread-pool sizes: more workers for fast API sources, fewer for Playwright.
_API_WORKERS     = 8
_BROWSER_WORKERS = 2


# ── Public entry point ────────────────────────────────────────────────────────

def run_scan(companies, scan_type="fresh", progress=None):
    """Scan a list of companies and return a summary dict.

    Args:
        companies:  Company config dicts from companies.json.
        scan_type:  "fresh" | "full"  (see module docstring).
        progress:   Optional list to update in-place so Flask can stream
                    per-company status to the browser.

    Returns a dict with keys:
        scan_run_id, scan_type, status, progress, results,
        new_count, total_jobs, failed_companies, elapsed_s
    """
    companies   = [c for c in companies if c.get("scan_enabled", True) is not False]
    is_fresh    = scan_type != "full"
    seen        = _load_seen_with_reset()
    now_str     = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    t_start     = time.monotonic()
    summaries   = []        # per-company summary dicts
    total_jobs  = 0
    total_new   = 0

    scan_run_id = create_scan_run(scan_type=scan_type, total_companies=len(companies))

    if progress is None:
        progress = [
            {
                "company": c["name"],
                "mode":    (c.get("scrape") or {}).get("mode", "html"),
                "status":  "pending",
                "count":   0,
                "new":     0,
            }
            for c in companies
        ]

    # Pre-create DB status rows so the polling endpoint can return them immediately.
    status_ids = {
        i: create_company_scan_status(scan_run_id, c["name"])
        for i, c in enumerate(companies)
    }

    # ── Per-company helpers (closures so they share outer state) ──────────────

    def _fetch_one(i, company):
        """Call the appropriate ATS fetcher for one company."""
        progress[i]["status"] = "scanning"
        t0 = time.monotonic()
        try:
            raw, err = fetch_jobs(company, seen, scan_type=scan_type)
            return i, company, raw or [], err, round(time.monotonic() - t0, 1)
        except Exception as exc:
            return i, company, [], str(exc), round(time.monotonic() - t0, 1)

    def _commit_one(i, company, raw, err, duration):
        """Persist results for one company and update shared state."""
        nonlocal total_jobs, total_new
        name      = company["name"]
        cats      = company.get("categories", [])
        new_count = 0
        rows      = []

        with _seen_lock:
            for job in raw:
                jid    = make_id(name, job.get("url", ""), job.get("title", ""))
                is_new = jid not in seen

                if is_new:
                    new_count += 1
                    seen[jid] = _make_seen_entry(job, name, now_str)
                else:
                    _refresh_seen_entry(seen[jid], job)

                rows.append({
                    **job,
                    "id":         jid,
                    "company":    name,
                    "categories": cats,
                    "is_new":     is_new,
                    "first_seen": seen[jid]["first_seen"],
                    # Prefer freshly scraped date; fall back to what we cached.
                    "posted":     job.get("posted") or seen[jid].get("posted", ""),
                })

        if rows:
            upsert_jobs(rows)
            # Full scan only: soft-delete jobs that vanished from the source.
            if not is_fresh and raw:
                delete_expired_jobs(name, [r["id"] for r in rows])

        entry = _make_summary(name, progress[i]["mode"], raw, err, duration, new_count)
        progress[i].update(entry)
        summaries.append(dict(entry))
        total_jobs += len(raw)
        total_new += new_count

        finish_company_scan_status(
            status_ids[i],
            status="success" if not err else "failed",
            jobs_found=len(raw),
            new_jobs_found=new_count,
            error_message=err,
        )
        update_scan_counts(scan_run_id, len(summaries))

    # ── Run fetches concurrently ──────────────────────────────────────────────

    api_cos     = [(i, c) for i, c in enumerate(companies)
                   if (c.get("scrape") or {}).get("mode", "html") in _API_MODES]
    browser_cos = [(i, c) for i, c in enumerate(companies)
                   if (c.get("scrape") or {}).get("mode", "html") not in _API_MODES]

    try:
        with (
            concurrent.futures.ThreadPoolExecutor(max_workers=_API_WORKERS)     as api_ex,
            concurrent.futures.ThreadPoolExecutor(max_workers=_BROWSER_WORKERS) as pw_ex,
        ):
            futures = (
                [api_ex.submit(_fetch_one, i, c) for i, c in api_cos]
                + [pw_ex.submit(_fetch_one, i, c) for i, c in browser_cos]
            )
            for fut in concurrent.futures.as_completed(futures):
                _commit_one(*fut.result())

        failed  = [s for s in summaries if s["status"] == "error"]
        elapsed = round(time.monotonic() - t_start, 1)
        status  = "partial_success" if failed else "success"

        finish_scan_run(
            scan_run_id,
            status=status,
            total_jobs_found=total_jobs,
            new_jobs_found=total_new,
            updated_jobs_found=max(0, total_jobs - total_new),
            failed_companies=len(failed),
        )
        return {
            "scan_run_id":      scan_run_id,
            "scan_type":        scan_type,
            "status":           status,
            "progress":         progress,
            "results":          [],
            "new_count":        total_new,
            "total_jobs":       total_jobs,
            "failed_companies": len(failed),
            "elapsed_s":        elapsed,
        }

    except Exception as exc:
        finish_scan_run(
            scan_run_id,
            status="failed",
            total_jobs_found=total_jobs,
            new_jobs_found=total_new,
            updated_jobs_found=max(0, total_jobs - total_new),
            failed_companies=len([s for s in summaries if s["status"] == "error"]),
            error_message=str(exc),
        )
        raise


# ── Private helpers ───────────────────────────────────────────────────────────

def _load_seen_with_reset():
    """Load the seen-job cache, honouring a one-shot force-reset flag."""
    if get_app_state("force_all_new_once") == "1":
        delete_app_state("force_all_new_once")
        return {}
    return load_seen()


def _make_seen_entry(job, company_name, now_str):
    """Build the initial cache entry for a newly discovered job."""
    return {
        "first_seen":  now_str,
        "company":     company_name,
        "title":       job.get("title", ""),
        "url":         job.get("url", ""),
        "location":    job.get("location", ""),
        "source":      job.get("source", ""),
        "posted":      job.get("posted", ""),
        "exp_min":     job.get("exp_min"),
        "exp_max":     job.get("exp_max"),
        "exp_label":   job.get("exp_label", ""),
        "exp_source":  job.get("exp_source", ""),
        "skills":      job.get("skills") or [],
    }


def _refresh_seen_entry(entry, job):
    """Overwrite stale cached fields with newer data from a re-scan."""
    if job.get("location"):  entry["location"]  = job["location"]
    if job.get("posted"):    entry["posted"]    = job["posted"]
    if job.get("source"):    entry["source"]    = job["source"]
    if job.get("exp_label"):
        entry.update({
            "exp_min":    job.get("exp_min"),
            "exp_max":    job.get("exp_max"),
            "exp_label":  job.get("exp_label", ""),
            "exp_source": job.get("exp_source", ""),
        })
    if job.get("skills"):
        entry["skills"] = job["skills"]


def _make_summary(name, mode, raw, err, duration, new_count):
    """Build the per-company status dict used by the progress list and scan log."""
    entry = {
        "company":    name,
        "mode":       mode,
        "status":     "error" if err else "done",
        "count":      len(raw),
        "new":        new_count,
        "duration_s": duration,
    }
    if err:
        entry["error"] = err
    return entry
