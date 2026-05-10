import concurrent.futures
import json
import os
import secrets
import sqlite3
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from .common import DB_FILE, SEEN_FILE, SUMMARY_FILE
from .job_data import (
    add_or_update_company, company_categories_map, current_companies, init_db, load_seen,
    rebuild_seen_from_db, upsert_jobs, delete_expired_jobs,
)
from .apis import _API_MODES, fetch_jobs
from .utils import _parse_posted, location_matches, make_id

app = Flask(__name__, template_folder="../templates")
app.secret_key = os.environ.get("SECRET_KEY") or os.environ.get("JOBRADAR_SECRET_KEY") or secrets.token_hex(32)
init_db()

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or os.environ.get("JOBRADAR_ADMIN_PASSWORD")

def _admin_configured():
    return bool(ADMIN_PASSWORD)

def _is_admin():
    return bool(session.get("admin_ok"))

def _wants_json():
    return request.path.startswith("/api/") or "application/json" in (request.headers.get("Accept") or "")

def _admin_required():
    if not _admin_configured():
        return None  # no password set → local mode, allow through
    if _is_admin():
        return None
    if _wants_json():
        return jsonify({"error": "Admin login required"}), 401
    return redirect(url_for("login", next=request.full_path))

def _login_page(error=""):
    error_html = f"<p class='err'>{error}</p>" if error else ""
    return f"""<!doctype html>
<html><head><title>JobRadar Login</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fb;margin:0;display:grid;place-items:center;min-height:100vh;color:#172033}}
.box{{width:min(420px,calc(100vw - 32px));background:white;border:1px solid #dfe5ef;border-radius:10px;padding:24px;box-shadow:0 16px 40px rgba(23,32,51,.12)}}
h1{{font-size:20px;margin:0 0 16px}} label{{font-size:12px;font-weight:700;color:#526174}} input{{box-sizing:border-box;width:100%;padding:11px;border:1px solid #cfd8e6;border-radius:8px;margin:6px 0 14px}}
button{{width:100%;padding:11px;border:0;border-radius:8px;background:#1c2b4a;color:white;font-weight:700;cursor:pointer}} .err{{color:#b42318;background:#fff1f0;border:1px solid #ffccc7;border-radius:8px;padding:10px;font-size:13px}}
</style></head><body><form class="box" method="post"><h1>JobRadar Admin</h1>{error_html}<label>Password</label><input name="password" type="password" autofocus><button>Sign in</button></form></body></html>"""

@app.before_request
def protect_admin_routes():
    protected = (
        request.path == "/admin"
        or request.path.startswith("/api/scan")
        or request.path.startswith("/api/reset")
        or request.path in {"/api/clear_data", "/api/detect_ats", "/api/add_detected_company"}
    )
    if protected:
        return _admin_required()
    return None

# ── Scan ──────────────────────────────────────────────────────────────────────

scan_state = {
    "running":False, "done":False, "started_at":0,
    "progress":[], "results":[], "new_count":0,
}
_seen_lock = threading.Lock()

def scan_runner(selected_names):
    try:
        do_scan(selected_names)
    except Exception as e:
        scan_state.update(
            running=False,
            done=True,
            results=[],
            new_count=0,
        )
        for p in scan_state.get("progress", []):
            if p.get("status") in {"pending", "scanning"}:
                p.update(status="error", error=str(e), count=0, new=0)

def do_scan(selected_names):
    seen    = load_seen()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []
    t_scan  = time.monotonic()

    scan_state.update(running=True, done=False, started_at=time.time(), progress=[], results=[], new_count=0)
    companies = current_companies()
    target = [c for c in companies if c["name"] in selected_names]

    # Pre-populate progress list so the frontend sees all companies immediately
    progress = [
        {"company": c["name"],
         "mode":    (c.get("scrape") or {}).get("mode", "html"),
         "status":  "pending", "count": 0, "new": 0}
        for c in target
    ]
    scan_state["progress"] = progress
    all_summaries = []

    def _commit(i, company, raw, err, duration):
        name     = company["name"]
        cats     = company.get("categories", [])
        new_this = 0
        with _seen_lock:
            for j in raw:
                jid    = make_id(name, j["url"], j["title"])
                is_new = jid not in seen
                if is_new:
                    seen[jid] = {
                        "first_seen": now_str,
                        "company": name,
                        "title": j["title"],
                        "url": j.get("url", ""),
                        "location": j.get("location", ""),
                        "source": j.get("source", ""),
                        "posted": j.get("posted", ""),
                    }
                    new_this += 1
                else:
                    # Update mutable fields — never touch first_seen
                    if j.get("location"):
                        seen[jid]["location"] = j.get("location", "")
                    if j.get("posted"):
                        seen[jid]["posted"] = j.get("posted", "")
                    if j.get("source"):
                        seen[jid]["source"] = j.get("source", "")
                results.append({
                    **j,
                    "id":         jid,
                    "company":    name,
                    "categories": cats,
                    "is_new":     is_new,
                    # first_seen is always the original discovery date, never the current scan
                    "first_seen": seen[jid]["first_seen"],
                    # posted: prefer what the scan just found; fall back to what we already know
                    "posted":     j.get("posted") or seen[jid].get("posted", ""),
                })
        # Flush this company's jobs to DB immediately so a mid-scan stop
        # doesn't lose work that already finished.
        company_rows = [r for r in results if r["company"] == name]
        if company_rows:
            upsert_jobs(company_rows)
            # Delete jobs that no longer appear in the scan (filled/expired).
            # Guard: only if scan returned results — empty means error, not expiry.
            if raw:
                delete_expired_jobs(name, [r["id"] for r in company_rows])
        entry = {
            "company":    name,
            "mode":       progress[i]["mode"],
            "status":     "done" if not err else "error",
            "count":      len(raw),
            "new":        new_this,
            "duration_s": duration,
        }
        if err:
            entry["error"] = err
        progress[i].update(entry)
        all_summaries.append(dict(entry))

    def _run_one(i, company):
        progress[i]["status"] = "scanning"
        t0 = time.monotonic()
        try:
            raw, err = fetch_jobs(company, seen)
            return i, company, raw or [], err, round(time.monotonic() - t0, 1)
        except Exception as e:
            return i, company, [], str(e), round(time.monotonic() - t0, 1)

    api_cos = [(i, c) for i, c in enumerate(target)
               if (c.get("scrape") or {}).get("mode", "html") in _API_MODES]
    pw_cos  = [(i, c) for i, c in enumerate(target)
               if (c.get("scrape") or {}).get("mode", "html") not in _API_MODES]

    # Both lanes start simultaneously:
    #   API lane  — up to 8 concurrent HTTP workers
    #   Browser lane — up to 2 concurrent Playwright/HTML workers (browsers are heavy)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as api_ex, \
         concurrent.futures.ThreadPoolExecutor(max_workers=2) as pw_ex:
        all_futs = (
            [api_ex.submit(_run_one, i, c) for i, c in api_cos] +
            [pw_ex.submit(_run_one, i, c)  for i, c in pw_cos]
        )
        for fut in concurrent.futures.as_completed(all_futs):
            _commit(*fut.result())

    upsert_jobs(results)
    rebuild_seen_from_db()   # always sync seen_jobs.json from DB after scan

    # Write scan summary to disk — merge with existing data so a partial scan
    # only updates the companies that were actually scanned.
    elapsed = round(time.monotonic() - t_scan, 1)
    try:
        existing = json.loads(SUMMARY_FILE.read_text())
        per_company_map = {e["company"]: e for e in existing.get("per_company", [])}
    except Exception:
        per_company_map = {}
    for s in all_summaries:
        per_company_map[s["company"]] = s
    merged = sorted(per_company_map.values(), key=lambda x: x["company"])
    failed  = [s for s in merged if s["status"] == "error"]
    zero    = [s for s in merged if s["status"] == "done" and s["count"] == 0]
    SUMMARY_FILE.write_text(json.dumps({
        "scan_time":        now_str,
        "duration_seconds": elapsed,
        "total_companies":  len(merged),
        "success":          len(merged) - len(failed),
        "failed_count":     len(failed),
        "zero_jobs_count":  len(zero),
        "failed":  [{"company": s["company"], "mode": s.get("mode"), "error": s.get("error")} for s in failed],
        "zero_jobs": [{"company": s["company"], "mode": s.get("mode")} for s in zero],
        "per_company": merged,
    }, indent=2, ensure_ascii=False))

    results.sort(key=lambda j: (0 if j["is_new"] else 1, j["company"]))
    scan_state.update(
        running=False, done=True, results=results,
        new_count=sum(1 for j in results if j["is_new"]),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("user.html", companies=current_companies())

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if _admin_configured() and secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["admin_ok"] = True
            return redirect(request.args.get("next") or url_for("admin"))
        return _login_page("Incorrect password." if _admin_configured() else "Set ADMIN_PASSWORD in the environment first."), 401
    return _login_page()

@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/admin")
def admin():
    return render_template("admin.html", companies=current_companies())

@app.route("/legacy")
def legacy():
    return render_template("index.html", companies=current_companies())

@app.route("/api/scan", methods=["POST"])
def start_scan():
    if scan_state["running"] and time.time() - float(scan_state.get("started_at") or 0) > 6 * 60 * 60:
        scan_state.update(running=False, done=True)
    if scan_state["running"]:
        return jsonify({"ok":True, "already_running":True})
    data     = request.get_json(silent=True) or {}
    selected = data.get("companies",[])
    if not selected:
        return jsonify({"error":"No companies selected"}), 400
    threading.Thread(target=scan_runner, args=(selected,), daemon=True).start()
    return jsonify({"ok":True})

@app.route("/api/reset_scan", methods=["POST"])
def reset_scan():
    rebuild_seen_from_db()
    # Keep progress/results so the panel still shows what was found before stop.
    # Only clear the running flag so polling stops.
    scan_state.update(running=False, done=True, started_at=0)
    return jsonify({"ok":True})

@app.route("/api/progress")
def get_progress():
    return jsonify({
        "running":   scan_state["running"],
        "done":      scan_state["done"],
        "progress":  scan_state["progress"],
        "results":   scan_state["results"],
        "new_count": scan_state["new_count"],
    })

@app.route("/api/reset_seen", methods=["POST"])
def reset_seen():
    if SEEN_FILE.exists(): SEEN_FILE.unlink()
    scan_state["results"] = []
    return jsonify({"ok":True})

@app.route("/api/clear_data", methods=["POST"])
def clear_data():
    if scan_state.get("running"):
        return jsonify({"error": "Cannot clear data while a scan is running"}), 409
    for f in (SEEN_FILE, SUMMARY_FILE):
        if f.exists(): f.unlink()
    if DB_FILE.exists():
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("DELETE FROM jobs")
            conn.commit()
    scan_state.update(results=[], new_count=0, done=False)
    return jsonify({"ok": True})

@app.route("/api/detect_ats", methods=["POST"])
def detect_ats_route():
    data = request.get_json(silent=True) or {}
    careers_url = (data.get("careers_url") or "").strip()
    if not careers_url:
        return jsonify({"error": "careers_url required"}), 400
    from .ats.detector import detect_ats
    results = detect_ats(careers_url)
    return jsonify({"results": results})

@app.route("/api/add_detected_company", methods=["POST"])
def add_detected_company_route():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    careers_url = (data.get("careers_url") or "").strip()
    config = data.get("config")
    if not name:
        return jsonify({"error": "name required"}), 400
    if not careers_url and not config:
        return jsonify({"error": "careers_url or config required"}), 400

    from .ats.detector import detect_ats
    if not isinstance(config, dict):
        results = detect_ats(careers_url)
        best = next((r for r in results if r.get("verified") and (r.get("job_count") or 0) != 0), None)
        best = best or (results[0] if results else None)
        if not best:
            return jsonify({"error": "No ATS detected"}), 404
        config = best.get("config") or {}
    else:
        best = {"mode": config.get("mode"), "config": config, "job_count": -1, "verified": False, "error": None}

    # Quick probe: cap at 1 page so we don't scan the full company.
    # Workday, Greenhouse, etc. can have thousands of jobs — we just need a sample.
    probe_config = {
        **config,
        "hard_max_pages": 1,  # workday
        "max_pages": 1,       # all other API fetchers
        "sort": False,        # don't stop early on seen-jobs during probe
        "fetch_posted_details": False,  # skip per-job date requests
    }
    test_company = {"name": name, "categories": ["Detected"], "scrape": probe_config}
    raw, err = fetch_jobs(test_company, {})
    job_count = best.get("job_count") if (best.get("job_count") or 0) > 0 else len(raw or [])
    if err and not raw:
        return jsonify({
            "error": "Detected config did not return jobs",
            "detail": err,
            "detected": best,
        }), 422

    updated = add_or_update_company(name, config, ["Detected"])
    return jsonify({
        "ok": True,
        "updated": updated,
        "company": name,
        "config": config,
        "job_count": job_count,
        "sample_jobs": (raw or [])[:5],
        "warning": err,
    })

@app.route("/api/companies")
def api_companies():
    return jsonify(current_companies())

@app.route("/api/summary")
def api_summary():
    if SUMMARY_FILE.exists():
        return jsonify(json.loads(SUMMARY_FILE.read_text()))
    return jsonify({"error": "No scan summary yet"}), 404

@app.route("/api/jobs")
def api_jobs():
    keyword  = (request.args.get("keyword")  or "").strip().lower()
    location = (request.args.get("location") or "").strip().lower()
    since    = (request.args.get("since")    or "").strip()
    category = (request.args.get("category") or "").strip()
    companies = [c.strip() for c in request.args.getlist("company") if c.strip()]
    if not companies:
        raw_companies = (request.args.get("companies") or "").strip()
        companies = [c.strip() for c in raw_companies.split(",") if c.strip()]
    limit    = min(int(request.args.get("limit", 100) or 100), 5000)
    page     = max(int(request.args.get("page", 1) or 1), 1)
    offset   = (page - 1) * limit

    clauses, params = [], []
    if keyword:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(company) LIKE ?)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if location:
        if location in ("united states", "usa", "u.s.", "u.s.a.", "america"):
            clauses.append("(LOWER(location) = ? OR LOWER(location) LIKE ?)")
            params.extend(["us", "%, us%"])
        elif location in ("uk", "u.k.", "united kingdom", "england", "scotland", "wales"):
            clauses.append("(LOWER(location) = ? OR LOWER(location) LIKE ? OR LOWER(location) LIKE ?)")
            params.extend(["uk", "%, uk%", "%united kingdom%"])
        else:
            clauses.append("LOWER(location) LIKE ?")
            params.append(f"%{location}%")
    if since:
        clauses.append("COALESCE(NULLIF(posted,''), first_seen) >= ?")
        params.append(since)
    if category:
        clauses.append("categories LIKE ?")
        params.append(f'%"{category}"%')
    if companies:
        placeholders = ",".join("?" for _ in companies)
        clauses.append(f"company IN ({placeholders})")
        params.extend(companies)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    # Sort: use posted date when available, otherwise first_seen — both normalised to YYYY-MM-DD
    sql   = (f"SELECT id, title, url, company, location, source, categories, first_seen, posted "
             f"FROM jobs {where} ORDER BY COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) DESC LIMIT ? OFFSET ?")
    count_sql = f"SELECT COUNT(*) FROM jobs {where}"
    query_params = list(params) + [limit, offset]

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, query_params).fetchall()
            total = conn.execute(count_sql, params).fetchone()[0]
            total_rows = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        jobs = []
        for r in rows:
            cats   = json.loads(r["categories"] or "[]")
            fs     = (r["first_seen"] or "")[:10]
            posted = (r["posted"] or "")[:10]
            jobs.append({
                "id":         r["id"],
                "title":      r["title"],
                "url":        r["url"],
                "company":    r["company"],
                "location":   r["location"] or "",
                "source":     r["source"] or "",
                "categories": cats,
                "first_seen": r["first_seen"],
                "posted":     posted,
                "is_new":     fs == today,
            })
        if not jobs and total == 0:
            jobs, total = jobs_from_seen_fallback(keyword, location, since, category, companies, limit, offset, today)
        return jsonify({"jobs": jobs, "total": total, "page": page, "limit": limit})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def jobs_from_seen_fallback(keyword, location, since, category, companies, limit, offset, today):
    cats_by_company = company_categories_map()
    company_set = set(companies or [])
    jobs = []
    for jid, item in load_seen().items():
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        company = item.get("company", "")
        first_seen = item.get("first_seen", "")
        posted = item.get("posted", "")
        cats = cats_by_company.get(company, [])
        if company_set and company not in company_set:
            continue
        loc = item.get("location", "")
        if keyword and keyword not in f"{title} {company}".lower():
            continue
        if location and not location_matches(location, loc):
            continue
        if since and (posted or first_seen) < since:
            continue
        if category and category not in cats:
            continue
        fs = first_seen[:10]
        jobs.append({
            "id":         jid,
            "title":      title,
            "url":        item.get("url", ""),
            "company":    company,
            "location":   loc,
            "source":     item.get("source", "seen"),
            "categories": cats,
            "first_seen": first_seen,
            "posted":     _parse_posted(posted),
            "is_new":     fs == today,
        })
    jobs.sort(key=lambda j: j.get("first_seen", ""), reverse=True)
    return jobs[offset:offset + limit], len(jobs)
