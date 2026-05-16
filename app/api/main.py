import json
import os
import secrets
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from .database import connect_db, placeholders
try:
    from scanner_service import run_scan
    from scan_store import latest_scan as latest_scan_record, scan_companies as scan_company_records
except ModuleNotFoundError:
    from ..scanner_service import run_scan
    from ..scan_store import latest_scan as latest_scan_record, scan_companies as scan_company_records

from .job_data import (
    add_or_update_company, current_companies, init_db, jobs_from_seen_fallback,
    set_app_state,
)
from .apis import fetch_jobs
from .utils import experience_match_score
from .search import (
    display_location, display_title,
    experience_score_sql, keyword_groups, keyword_relevance_sql, keyword_where,
)

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
    # Local development should not get stuck on a stale Flask session after
    # server restarts. Production/non-loopback requests still require admin.
    if request.remote_addr in {"127.0.0.1", "::1"}:
        return None
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
    "scan_run_id": None,
}

def _recover_stale_scans():
    """On startup, mark any scan_runs stuck in 'running' as abandoned."""
    try:
        with connect_db() as conn:
            conn.execute(
                "UPDATE scan_runs SET status='abandoned', finished_at=? WHERE status='running'",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
            )
    except Exception:
        pass

_recover_stale_scans()

def scan_runner(selected_names):
    try:
        do_scan(selected_names)
    except Exception as e:
        scan_state.update(running=False, done=True, results=[], new_count=0)
        for p in scan_state.get("progress", []):
            if p.get("status") in {"pending", "scanning"}:
                p.update(status="error", error=str(e), count=0, new=0)

def do_scan(selected_names):
    scan_state.update(running=True, done=False, started_at=time.time(), progress=[], results=[], new_count=0, scan_run_id=None)
    companies = current_companies()
    target = [c for c in companies if c["name"] in selected_names]
    scan_state["progress"] = [
        {"company": c["name"],
         "mode":    (c.get("scrape") or {}).get("mode", "html"),
         "status":  "pending", "count": 0, "new": 0}
        for c in target
    ]
    result = run_scan(target, scan_type="manual", progress=scan_state["progress"])
    scan_state.update(
        running=False,
        done=True,
        results=result["results"],
        new_count=result["new_count"],
        scan_run_id=result.get("scan_run_id"),
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
    if not scan_state["running"]:
        # Also guard against another process that started a scan (Postgres multi-worker)
        try:
            with connect_db(rows=True) as conn:
                row = conn.execute(
                    "SELECT id FROM scan_runs WHERE status='running' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            if row:
                return jsonify({"ok": True, "already_running": True})
        except Exception:
            pass
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
    # Keep progress/results so the panel still shows what was found before stop.
    # Only clear the running flag so polling stops.
    scan_state.update(running=False, done=True, started_at=0)
    return jsonify({"ok":True})

@app.route("/api/progress")
def get_progress():
    return jsonify({
        "running":     scan_state["running"],
        "done":        scan_state["done"],
        "progress":    scan_state["progress"],
        "results":     scan_state["results"],
        "new_count":   scan_state["new_count"],
        "scan_run_id": scan_state.get("scan_run_id"),
    })

@app.route("/api/reset_seen", methods=["POST"])
def reset_seen():
    set_app_state("force_all_new_once", "1")
    scan_state["results"] = []
    return jsonify({"ok":True})

@app.route("/api/clear_data", methods=["POST"])
def clear_data():
    if scan_state.get("running"):
        return jsonify({"error": "Cannot clear data while a scan is running"}), 409
    with connect_db() as conn:
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM app_state")
        conn.execute("DELETE FROM scan_runs")
        conn.execute("DELETE FROM scan_company_status")
    scan_state.update(results=[], new_count=0, done=False, scan_run_id=None)
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
    run = latest_scan_record()
    if not run:
        return jsonify({"error": "No scan summary yet"}), 404
    run = dict(run)
    companies = current_companies()
    mode_by_name = {c["name"]: (c.get("scrape") or {}).get("mode", "html") for c in companies}
    scan_run_id = run["id"]
    company_rows = scan_company_records(scan_run_id)
    per_company = []
    failed = []
    zero = []
    for row in company_rows:
        row = dict(row)
        name = row["company_name"]
        status = "done" if row["status"] == "success" else ("error" if row["status"] == "failed" else row["status"])
        entry = {
            "company":   name,
            "mode":      mode_by_name.get(name, ""),
            "status":    status,
            "count":     row.get("jobs_found", 0) or 0,
            "new":       row.get("new_jobs_found", 0) or 0,
        }
        if row.get("error_message"):
            entry["error"] = row["error_message"]
        per_company.append(entry)
        if status == "error":
            failed.append({"company": name, "mode": entry["mode"], "error": entry.get("error")})
        if status == "done" and entry["count"] == 0:
            zero.append({"company": name, "mode": entry["mode"]})
    return jsonify({
        "scan_time":        run.get("started_at", ""),
        "duration_seconds": None,
        "total_companies":  run.get("total_companies", len(per_company)),
        "success":          run.get("total_companies", len(per_company)) - len(failed),
        "failed_count":     len(failed),
        "zero_jobs_count":  len(zero),
        "failed":           failed,
        "zero_jobs":        zero,
        "per_company":      per_company,
    })

@app.route("/api/scans/latest")
def api_latest_scan():
    row = latest_scan_record()
    return jsonify(dict(row) if row else None)

@app.route("/api/scans/<int:scan_run_id>/companies")
def api_scan_companies(scan_run_id):
    rows = scan_company_records(scan_run_id)
    return jsonify([dict(row) for row in rows])

@app.route("/api/jobs")
def api_jobs():
    keyword  = (request.args.get("keyword")  or "").strip().lower()
    location = (request.args.get("location") or "").strip().lower()
    since    = (request.args.get("since")    or "").strip()
    exp_years_raw = (request.args.get("experience") or request.args.get("years") or "").strip()
    exp_years = None
    if exp_years_raw:
        try:
            exp_years = max(0.0, float(exp_years_raw))
        except ValueError:
            exp_years = None
    category = (request.args.get("category") or "").strip()
    companies = [c.strip() for c in request.args.getlist("company") if c.strip()]
    if not companies:
        raw_companies = (request.args.get("companies") or "").strip()
        companies = [c.strip() for c in raw_companies.split(",") if c.strip()]
    active_company_names = {
        company.get("name")
        for company in current_companies()
        if company.get("scan_enabled", True) is not False
    }
    if companies:
        companies = [name for name in companies if name in active_company_names]
    limit    = min(int(request.args.get("limit", 100) or 100), 5000)
    page     = max(int(request.args.get("page", 1) or 1), 1)
    offset   = (page - 1) * limit

    clauses, params = ["is_open = TRUE"], []
    kw_groups = keyword_groups(keyword)
    if keyword:
        keyword_clauses, keyword_params = keyword_where(kw_groups)
        clauses.extend(keyword_clauses)
        params.extend(keyword_params)
    if location:
        if location in ("united states", "usa", "u.s.", "u.s.a.", "america"):
            clauses.append("(LOWER(location) = ? OR LOWER(location) LIKE ?)")
            params.extend(["us", "%, us%"])
        elif location in ("uk", "u.k.", "united kingdom", "england", "scotland", "wales"):
            clauses.append("(LOWER(location) = ? OR LOWER(location) LIKE ? OR LOWER(location) LIKE ?)")
            params.extend(["uk", "%, uk%", "%united kingdom%"])
        else:
            clauses.append("(' ' || REPLACE(LOWER(location), ', ', ' ') || ' ') LIKE ?")
            params.append(f"% {location} %")
    if since:
        clauses.append("COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) >= ?")
        params.append(since)
    if exp_years is not None:
        # Keep unknowns, exact/close matches, and junior-ish roles. Drop known
        # requirements clearly above the user's experience.
        clauses.append("(exp_min IS NULL OR exp_min <= ?)")
        params.append(int(exp_years) + 1)
    if category:
        clauses.append("categories LIKE ?")
        params.append(f'%"{category}"%')
    if companies:
        company_marks = placeholders(len(companies))
        clauses.append(f"company IN ({company_marks})")
        params.extend(companies)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    company_interviews = {
        company.get("name"): company.get("interview")
        for company in current_companies()
        if company.get("interview")
    }
    relevance_sql = keyword_relevance_sql(keyword, kw_groups) if kw_groups else "0"
    select_rank = f", {relevance_sql} AS relevance" if kw_groups else ""
    exp_score_sql = experience_score_sql(exp_years) if exp_years is not None else "0"
    select_exp = f", {exp_score_sql} AS exp_score" if exp_years is not None else ""
    exp_order = ", exp_score DESC" if exp_years is not None else ""
    order_sql = (
        f"COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) DESC{exp_order}, relevance DESC"
        if kw_groups else
        f"COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) DESC{exp_order}"
    )
    # Sort by posted date, falling back to first_seen only when posted is missing.
    sql   = (f"SELECT id, title, url, company, location, source, categories, first_seen, posted, "
             f"exp_min, exp_max, exp_label, exp_source{select_rank}{select_exp} "
             f"FROM jobs {where} ORDER BY {order_sql} LIMIT ? OFFSET ?")
    count_sql = f"SELECT COUNT(*) FROM jobs {where}"
    query_params = list(params) + [limit, offset]

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with connect_db(rows=True) as conn:
            rows = conn.execute(sql, query_params).fetchall()
            total_row = conn.execute(count_sql, params).fetchone()
            total = next(iter(total_row.values())) if isinstance(total_row, dict) else total_row[0]
        jobs = []
        for r in rows:
            cats   = json.loads(r["categories"] or "[]")
            fs     = (r["first_seen"] or "")[:10]
            posted = (r["posted"] or "")[:10]
            title  = display_title(r["title"], r["url"])
            loc    = display_location(r["location"] or "", r["title"], r["url"])
            jobs.append({
                "id":         r["id"],
                "title":      title,
                "url":        r["url"],
                "company":    r["company"],
                "location":   loc,
                "source":     r["source"] or "",
                "categories": cats,
                "first_seen": r["first_seen"],
                "posted":     posted,
                "exp_min":    r["exp_min"],
                "exp_max":    r["exp_max"],
                "exp_label":  r["exp_label"] or "",
                "exp_source": r["exp_source"] or "",
                "exp_score":  experience_match_score(exp_years, r["exp_min"], r["exp_max"]) if exp_years is not None else 0,
                "interview":  company_interviews.get(r["company"]),
                "is_new":     fs == today,
            })
        if not jobs and total == 0:
            jobs, total = jobs_from_seen_fallback(keyword, location, since, category, companies, limit, offset, today)
        return jsonify({"jobs": jobs, "total": total, "page": page, "limit": limit})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
