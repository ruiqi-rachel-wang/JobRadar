import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from urllib.parse import urlsplit

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from .database import IS_POSTGRES, connect_db, placeholders
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
    experience_score_sql, fts_match_query, keyword_groups, keyword_relevance_sql, keyword_where,
    role_intent_where,
)

app = Flask(__name__, template_folder="../templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY") or os.environ.get("JOBRADAR_SECRET_KEY") or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
    or os.environ.get("FLASK_ENV") == "production"
    or bool(os.environ.get("RENDER"))
)
init_db()

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or os.environ.get("JOBRADAR_ADMIN_PASSWORD")

def _is_admin():
    return bool(session.get("admin_ok"))

def _is_user():
    return bool(session.get("user_id") or session.get("admin_ok"))

def _current_user_id():
    return session.get("user_id")

def _wants_json():
    return request.path.startswith("/api/") or "application/json" in (request.headers.get("Accept") or "")

def _safe_next(default_endpoint):
    target = request.args.get("next") or request.form.get("next") or ""
    parsed = urlsplit(target)
    if (
        target.startswith("/")
        and not target.startswith(("//", "/\\"))
        and not parsed.scheme
        and not parsed.netloc
    ):
        return target
    return url_for(default_endpoint)

def _admin_required():
    if not ADMIN_PASSWORD:
        if _wants_json():
            return jsonify({"error": "Admin password is not configured"}), 503
        return "Admin password is not configured", 503
    if _is_admin():  return None
    if _wants_json():
        return jsonify({"error": "Admin login required"}), 401
    return redirect(url_for("login", next=request.full_path))

def _user_required():
    if _is_user():   return None
    if _wants_json():
        return jsonify({"error": "Login required"}), 401
    return redirect(url_for("login", next=request.full_path))

@app.before_request
def protect_routes():
    if request.path == "/app":
        return _user_required()
    protected = (
        request.path == "/admin"
        or request.path.startswith("/api/scan")
        or request.path.startswith("/api/reset")
        or request.path.startswith("/api/reenrich")
        or request.path.startswith("/api/backfill")
        or request.path in {"/api/clear_data", "/api/detect_ats", "/api/add_detected_company"}
    )
    if protected:
        return _admin_required()
    return None

# ── Scan state + schedule ─────────────────────────────────────────────────────

scan_state = {
    "running": False, "done": False, "started_at": 0,
    "progress": [], "new_count": 0,
    "scan_run_id": None, "scan_type": "fresh",
}
_scan_cancel_event = threading.Event()

# ── Auto-schedule ──────────────────────────────────────────────────────────────

_last_schedule_trigger: str = ""   # "HH:MM" of last auto-triggered slot


def _get_schedule() -> dict:
    """Load the scan schedule from app_state (persisted JSON)."""
    from .job_data import get_app_state
    raw = get_app_state("scan_schedule")
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _try_run_scheduled():
    """Fire a scheduled scan if the current minute matches a configured slot."""
    global _last_schedule_trigger
    if scan_state["running"]:
        return
    now = datetime.now().strftime("%H:%M")
    if now == _last_schedule_trigger:
        return  # already fired this minute
    schedule = _get_schedule()
    for scan_type in ("fresh", "full"):
        cfg = schedule.get(scan_type, {})
        if cfg.get("enabled") and now in (cfg.get("times") or []):
            _last_schedule_trigger = now
            names = [c["name"] for c in current_companies()]
            threading.Thread(target=scan_runner, args=(names, scan_type), daemon=True).start()
            break


def _schedule_loop():
    """Background thread: checks every 30 s whether a scheduled slot should fire."""
    while True:
        time.sleep(30)
        try:
            _try_run_scheduled()
        except Exception:
            pass


if os.environ.get("JOBRADAR_ENABLE_APP_SCHEDULER", "true").lower() in {"1", "true", "yes"}:
    threading.Thread(target=_schedule_loop, daemon=True).start()


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

def scan_runner(selected_names, scan_type="fresh"):
    try:
        do_scan(selected_names, scan_type=scan_type)
    except Exception as e:
        scan_state.update(running=False, done=True, new_count=0)
        for p in scan_state.get("progress", []):
            if p.get("status") in {"pending", "scanning"}:
                p.update(status="error", error=str(e), count=0, new=0)

def do_scan(selected_names, scan_type="fresh"):
    _scan_cancel_event.clear()
    scan_state.update(
        running=True, done=False, started_at=time.time(),
        progress=[], new_count=0,
        scan_run_id=None, scan_type=scan_type,
    )
    companies = current_companies()
    target = [c for c in companies if c["name"] in selected_names]
    scan_state["progress"] = [
        {"company": c["name"],
         "mode":    (c.get("scrape") or {}).get("mode", "html"),
         "status":  "pending", "count": 0, "new": 0}
        for c in target
    ]
    result = run_scan(
        target,
        scan_type=scan_type,
        progress=scan_state["progress"],
        on_scan_run_id=lambda scan_run_id: scan_state.update(scan_run_id=scan_run_id),
        should_cancel=_scan_cancel_event.is_set,
    )
    scan_state.update(
        running=False,
        done=True,
        new_count=result["new_count"],
        scan_run_id=result.get("scan_run_id") or scan_state.get("scan_run_id"),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

def _user_ctx():
    """Template context for the current user."""
    uid = session.get("user_id")
    if uid:
        with connect_db(rows=True) as conn:
            row = conn.execute("SELECT username, role FROM users WHERE id=?", (uid,)).fetchone()
        if row:
            return {"username": row["username"], "is_admin": row["role"] == "admin"}
    if _is_admin():
        return {"username": "Admin", "is_admin": True}
    return {"username": None, "is_admin": False}

@app.route("/")
def public_view():
    return render_template("user.html", companies=current_companies(),
                           is_public=True, is_admin=False, username=None)

@app.route("/app")
def app_view():
    ctx = _user_ctx()
    return render_template("user.html", companies=current_companies(),
                           is_public=False, is_admin=ctx["is_admin"],
                           username=ctx["username"])

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        # Admin shortcut: username "admin" + env password
        if ADMIN_PASSWORD and username.lower() == "admin" and secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["admin_ok"] = True
            return redirect(_safe_next("admin"))
        # Regular user lookup
        with connect_db(rows=True) as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username=?",
                (username,)
            ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"]  = row["id"]
            session["username"] = row["username"]
            if row["role"] == "admin":
                session["admin_ok"] = True
                return redirect(_safe_next("admin"))
            return redirect(_safe_next("app_view"))
        return render_template("auth.html", mode="login",
                               error="Incorrect username or password.",
                               next=request.args.get("next", "")), 401
    return render_template("auth.html", mode="login", error="",
                           next=request.args.get("next", ""))

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        # Validation
        if not username or not password:
            return render_template("auth.html", mode="signup",
                                   error="Username and password are required."), 400
        if not re.match(r'^[a-zA-Z0-9_.-]{3,30}$', username):
            return render_template("auth.html", mode="signup",
                                   error="Username: 3-30 chars, letters/numbers/._- only."), 400
        if len(password) < 6:
            return render_template("auth.html", mode="signup",
                                   error="Password must be at least 6 characters."), 400
        if password != confirm:
            return render_template("auth.html", mode="signup",
                                   error="Passwords do not match."), 400
        pw_hash = generate_password_hash(password)
        try:
            with connect_db() as conn:
                conn.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                    (username, email, pw_hash)
                )
        except Exception:
            return render_template("auth.html", mode="signup",
                                   error="Username already taken."), 400
        # Auto-login after signup
        with connect_db(rows=True) as conn:
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        session.clear()
        session["user_id"]  = row["id"]
        session["username"] = username
        return redirect(url_for("app_view"))
    return render_template("auth.html", mode="signup", error="")

@app.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("public_view"))

@app.route("/admin")
def admin():
    return render_template("admin.html", companies=current_companies())

@app.route("/legacy")
def legacy():
    return render_template("index.html", companies=current_companies())

# ── Apply list API ─────────────────────────────────────────────────────────────

@app.route("/api/apply", methods=["GET"])
def get_apply():
    uid = _current_user_id()
    if not uid:
        return jsonify([])
    with connect_db(rows=True) as conn:
        rows = conn.execute(
            "SELECT job_id, stage, job_title, job_company, job_location, "
            "job_url, job_exp_label, job_posted, added_at FROM user_apply_list "
            "WHERE user_id=? ORDER BY added_at DESC", (uid,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/apply", methods=["POST"])
def add_apply():
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "not logged in"}), 401
    d = request.get_json(silent=True) or {}
    job_id = (d.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO user_apply_list "
            "(user_id, job_id, stage, job_title, job_company, job_location, job_url, job_exp_label, job_posted) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id, job_id) DO UPDATE SET stage=excluded.stage",
            (uid, job_id, d.get("stage", "to-apply"),
             d.get("job_title",""), d.get("job_company",""), d.get("job_location",""),
             d.get("job_url",""), d.get("job_exp_label",""), d.get("job_posted",""))
        )
    return jsonify({"ok": True})

@app.route("/api/apply/<job_id>", methods=["PATCH"])
def update_apply(job_id):
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "not logged in"}), 401
    stage = (request.get_json(silent=True) or {}).get("stage", "")
    valid = {"to-apply","applied","interview","offer","closed"}
    if stage not in valid:
        return jsonify({"error": "invalid stage"}), 400
    with connect_db() as conn:
        conn.execute("UPDATE user_apply_list SET stage=? WHERE user_id=? AND job_id=?",
                     (stage, uid, job_id))
    return jsonify({"ok": True})

@app.route("/api/apply/<job_id>", methods=["DELETE"])
def remove_apply(job_id):
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "not logged in"}), 401
    with connect_db() as conn:
        conn.execute("DELETE FROM user_apply_list WHERE user_id=? AND job_id=?", (uid, job_id))
    return jsonify({"ok": True})

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
    data      = request.get_json(silent=True) or {}
    selected  = data.get("companies", [])
    scan_type = data.get("scan_type", "fresh")
    if scan_type not in {"fresh", "full"}:
        scan_type = "fresh"
    if not selected:
        return jsonify({"error": "No companies selected"}), 400
    threading.Thread(target=scan_runner, args=(selected, scan_type), daemon=True).start()
    return jsonify({"ok": True, "scan_type": scan_type})

@app.route("/api/reset_scan", methods=["POST"])
def reset_scan():
    # Keep progress/results so the panel still shows what was found before stop.
    # Only clear the running flag so polling stops.
    _scan_cancel_event.set()
    scan_state.update(running=False, done=True, started_at=0)
    return jsonify({"ok":True})

@app.route("/api/progress")
def get_progress():
    return jsonify({
        "running":     scan_state["running"],
        "done":        scan_state["done"],
        "scan_type":   scan_state.get("scan_type", "fresh"),
        "progress":    scan_state["progress"],
        "new_count":   scan_state["new_count"],
        "scan_run_id": scan_state.get("scan_run_id"),
    })

@app.route("/api/reset_seen", methods=["POST"])
def reset_seen():
    set_app_state("force_all_new_once", "1")
    return jsonify({"ok":True})

@app.route("/api/reenrich", methods=["POST"])
def reenrich_jobs():
    """Re-extract experience/skills/employment-type from already-stored descriptions."""
    if scan_state.get("running"):
        return jsonify({"error": "Cannot re-enrich while a scan is running"}), 409
    import threading
    def _run():
        try:
            from .apis import _strip_html
            from .utils import extract_experience, extract_skills, extract_employment_type as et
            with connect_db(rows=True) as conn:
                rows = conn.execute(
                    "SELECT id, title, description FROM jobs WHERE length(description) > 0"
                ).fetchall()
            updates = []
            for r in rows:
                clean = _strip_html(r["description"] or "")
                if not clean:
                    continue
                exp    = extract_experience(r["title"] or "", clean, allow_title_fallback=False)
                skills = extract_skills(r["title"] or "", clean)
                emp    = et(r["title"] or "", clean)
                updates.append((
                    clean,
                    exp.get("min"), exp.get("max"), exp.get("label", ""), exp.get("source", ""),
                    json.dumps(skills or []),
                    emp,
                    r["id"],
                ))
            with connect_db() as conn:
                conn.executemany(
                    "UPDATE jobs SET description=?,exp_min=?,exp_max=?,exp_label=?,exp_source=?,skills=?,employment_type=? WHERE id=?",
                    updates,
                )
            scan_state["_reenrich"] = f"Done — {len(updates)} jobs updated"
        except Exception as exc:
            scan_state["_reenrich"] = f"Error: {exc}"
    scan_state["_reenrich"] = "running"
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": "Re-enrichment started in background"})

@app.route("/api/reenrich/status", methods=["GET"])
def reenrich_status():
    return jsonify({"status": scan_state.get("_reenrich", "idle")})

@app.route("/api/backfill_descriptions", methods=["POST"])
def backfill_descriptions():
    """Fetch descriptions for open jobs that don't have them, then re-extract experience."""
    if scan_state.get("running"):
        return jsonify({"error": "Cannot backfill while a scan is running"}), 409
    if scan_state.get("_backfill_desc") == "running":
        return jsonify({"error": "Backfill already in progress"}), 409

    limit = int(request.json.get("limit", 500) if request.json else 500)

    def _run():
        import concurrent.futures
        import requests
        from bs4 import BeautifulSoup
        from .apis import _strip_html
        from .utils import extract_experience, extract_skills, extract_employment_type as et
        from .common import HEADERS

        _SELECTORS = [
            '.jobDescription', '#jobDescription', '[class*="job-description"]',
            '[class*="jobDescription"]', '[class*="job-content"]', '[class*="jobContent"]',
            '.iCIMS_JobContent', 'main article', 'main', 'article',
        ]

        def _fetch_desc(row):
            try:
                from .common import REQUESTS_VERIFY_SSL
                r = requests.get(row["url"], headers=HEADERS, timeout=20, verify=REQUESTS_VERIFY_SSL)
                if r.status_code != 200:
                    return row["id"], ""
                soup = BeautifulSoup(r.text, "lxml")
                for sel in _SELECTORS:
                    el = soup.select_one(sel)
                    if el:
                        text = " ".join(el.get_text(" ", strip=True).split())
                        if len(text) > 100:
                            return row["id"], text
                return row["id"], ""
            except Exception:
                return row["id"], ""

        try:
            with connect_db(rows=True) as conn:
                rows = conn.execute(
                    "SELECT id, url, title, source FROM jobs "
                    "WHERE is_open=1 AND has_description=0 AND COALESCE(url,'') != '' "
                    "ORDER BY first_seen DESC LIMIT ?",
                    (limit,),
                ).fetchall()

            scan_state["_backfill_desc"] = f"running — fetching {len(rows)} descriptions"
            updates = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                for job_id, desc in ex.map(_fetch_desc, rows):
                    if desc:
                        updates.append((job_id, desc))

            if updates:
                enrich_updates = []
                for job_id, desc in updates:
                    clean = _strip_html(desc)
                    title = next((r["title"] for r in rows if r["id"] == job_id), "")
                    exp    = extract_experience(title, clean)
                    skills = extract_skills(title, clean)
                    emp    = et(title, clean)
                    enrich_updates.append((
                        clean,
                        exp.get("min"), exp.get("max"), exp.get("label", ""), exp.get("source", ""),
                        json.dumps(skills or []),
                        emp,
                        job_id,
                    ))
                with connect_db() as conn:
                    conn.executemany(
                        "UPDATE jobs SET description=?,exp_min=?,exp_max=?,exp_label=?,exp_source=?,skills=?,employment_type=?,has_description=1 WHERE id=?",
                        enrich_updates,
                    )

            scan_state["_backfill_desc"] = f"done — {len(updates)}/{len(rows)} descriptions fetched"
        except Exception as exc:
            scan_state["_backfill_desc"] = f"error: {exc}"

    scan_state["_backfill_desc"] = "running"
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"ok": True, "message": f"Description backfill started (limit={limit})"})

@app.route("/api/backfill_descriptions/status", methods=["GET"])
def backfill_descriptions_status():
    return jsonify({"status": scan_state.get("_backfill_desc", "idle")})

@app.route("/api/clear_data", methods=["POST"])
def clear_data():
    if scan_state.get("running"):
        return jsonify({"error": "Cannot clear data while a scan is running"}), 409
    with connect_db() as conn:
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM app_state")
        conn.execute("DELETE FROM scan_runs")
        conn.execute("DELETE FROM scan_company_status")
    scan_state.update(new_count=0, done=False, scan_run_id=None)
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

@app.route("/api/schedule", methods=["GET"])
def get_schedule():
    return jsonify(_get_schedule())

@app.route("/api/schedule", methods=["POST"])
def save_schedule():
    guard = _admin_required()
    if guard: return guard
    data = request.get_json(silent=True) or {}
    schedule = {}
    for scan_type in ("fresh", "full"):
        cfg   = data.get(scan_type, {})
        times = sorted({t for t in (cfg.get("times") or [])
                        if re.match(r'^\d{2}:\d{2}$', str(t))})
        schedule[scan_type] = {"enabled": bool(cfg.get("enabled")), "times": times}
    from .job_data import set_app_state
    set_app_state("scan_schedule", json.dumps(schedule))
    return jsonify({"ok": True})

@app.route("/api/scans/recent")
def api_scans_recent():
    with connect_db(rows=True) as conn:
        rows = conn.execute("""
            SELECT id, scan_type, status, started_at, finished_at,
                   total_companies, companies_scanned, total_jobs_found,
                   new_jobs_found, failed_companies
            FROM scan_runs ORDER BY id DESC LIMIT 10
        """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/feedback", methods=["POST"])
def submit_feedback():
    d   = request.get_json(silent=True) or {}
    msg = (d.get("message") or "").strip()
    if not msg:
        return jsonify({"error": "message required"}), 400
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO feedback (type, message, url) VALUES (?, ?, ?)",
            (d.get("type", "other"), msg, (d.get("url") or "").strip()),
        )
    return jsonify({"ok": True})

@app.route("/api/feedback", methods=["GET"])
def get_feedback():
    guard = _admin_required()
    if guard: return guard
    status = request.args.get("status")
    with connect_db(rows=True) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE status=? ORDER BY submitted_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY submitted_at DESC"
            ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/feedback/<int:fid>", methods=["DELETE"])
def delete_feedback(fid):
    guard = _admin_required()
    if guard: return guard
    with connect_db() as conn:
        conn.execute("DELETE FROM feedback WHERE id=?", (fid,))
    return jsonify({"ok": True})

@app.route("/api/feedback/<int:fid>", methods=["PATCH"])
def update_feedback(fid):
    guard = _admin_required()
    if guard: return guard
    status = (request.get_json(silent=True) or {}).get("status", "")
    if status not in {"new", "triaged", "resolved"}:
        return jsonify({"error": "invalid status"}), 400
    with connect_db() as conn:
        conn.execute("UPDATE feedback SET status=? WHERE id=?", (status, fid))
    return jsonify({"ok": True})

@app.route("/api/jobs/<job_id>")
def api_job_detail(job_id):
    interview_map = {c["name"]: c.get("interview") for c in current_companies() if c.get("interview")}
    with connect_db(rows=True) as conn:
        row = conn.execute(
            "SELECT id, title, url, company, location, source, posted, first_seen, "
            "exp_label, exp_min, exp_max, skills, categories, employment_type, description "
            "FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()
    if not row:
        return jsonify({"error": "Job not found"}), 404
    skills = json.loads(row["skills"] or "[]") if row["skills"] else []
    cats   = json.loads(row["categories"] or "[]") if row["categories"] else []
    return jsonify({
        "id":              row["id"],
        "title":           row["title"] or "",
        "company":         row["company"] or "",
        "location":        row["location"] or "",
        "source":          row["source"] or "",
        "posted":          (row["posted"] or "")[:10],
        "first_seen":      (row["first_seen"] or "")[:10],
        "url":             row["url"] or "",
        "exp_label":       row["exp_label"] or "",
        "employment_type": row["employment_type"] or "",
        "skills":          skills,
        "categories":      cats,
        "interview":       interview_map.get(row["company"] or ""),
        "description":     row["description"] or "",
    })

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
    company_configs = current_companies()
    active_company_names = {
        company.get("name")
        for company in company_configs
        if company.get("scan_enabled", True) is not False
    }
    if companies:
        companies = [name for name in companies if name in active_company_names]
    try:
        limit = min(int(request.args.get("limit", 100) or 100), 5000)
    except (ValueError, TypeError):
        limit = 100
    try:
        page = max(int(request.args.get("page", 1) or 1), 1)
    except (ValueError, TypeError):
        page = 1
    offset   = (page - 1) * limit

    kw_groups = keyword_groups(keyword)
    fts_query = fts_match_query(keyword, location, kw_groups) if (not IS_POSTGRES and keyword and location) else ""
    use_fts = bool(fts_query)

    if use_fts:
        from_sql = "jobs_fts JOIN jobs ON jobs.id = jobs_fts.job_id"
        clauses, params = ["jobs_fts MATCH ?", "jobs.is_open = TRUE"], [fts_query]
        field = lambda name: f"jobs.{name}"
        role_where, role_params = role_intent_where(kw_groups, "jobs.title")
        if role_where:
            clauses.append(role_where)
            params.extend(role_params)
    else:
        from_sql = "jobs"
        clauses, params = ["is_open = TRUE"], []
        field = lambda name: name
        if keyword:
            keyword_clauses, keyword_params = keyword_where(kw_groups)
            clauses.extend(keyword_clauses)
            params.extend(keyword_params)

    if location and not use_fts:
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
        clauses.append(f"COALESCE(NULLIF({field('posted')},''), substr({field('first_seen')},1,10)) >= ?")
        params.append(since)
    if exp_years is not None:
        # Keep unknowns, exact/close matches, and junior-ish roles. Drop known
        # requirements clearly above the user's experience.
        clauses.append(f"({field('exp_min')} IS NULL OR {field('exp_min')} <= ?)")
        params.append(int(exp_years) + 1)
    if category:
        clauses.append(f"{field('categories')} LIKE ?")
        params.append(f'%"{category}"%')
    if companies:
        company_marks = placeholders(len(companies))
        clauses.append(f"{field('company')} IN ({company_marks})")
        params.extend(companies)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    company_interviews = {
        company.get("name"): company.get("interview")
        for company in company_configs
        if company.get("interview")
    }
    relevance_sql = keyword_relevance_sql(keyword, kw_groups) if kw_groups and not use_fts else "0"
    select_rank = f", {relevance_sql} AS relevance" if kw_groups else ""
    exp_score_sql = experience_score_sql(exp_years) if exp_years is not None else "0"
    if use_fts:
        exp_score_sql = exp_score_sql.replace("exp_min", "jobs.exp_min").replace("exp_max", "jobs.exp_max")
    select_exp = f", {exp_score_sql} AS exp_score" if exp_years is not None else ""
    exp_order = ", exp_score DESC" if exp_years is not None else ""
    date_expr = f"COALESCE(NULLIF({field('posted')},''), substr({field('first_seen')},1,10))"
    order_sql = (
        f"{date_expr} DESC{exp_order}, relevance DESC"
        if kw_groups and not use_fts else
        f"{date_expr} DESC{exp_order}"
    )
    # Sort by posted date, falling back to first_seen only when posted is missing.
    select_cols = (
        "jobs.id, jobs.title, jobs.url, jobs.company, jobs.location, jobs.source, "
        "jobs.categories, jobs.first_seen, jobs.posted, jobs.exp_min, jobs.exp_max, "
        "jobs.exp_label, jobs.exp_source, jobs.skills, jobs.employment_type"
        if use_fts else
        "id, title, url, company, location, source, categories, first_seen, posted, "
        "exp_min, exp_max, exp_label, exp_source, skills, employment_type"
    )
    sql   = (f"SELECT {select_cols}{select_rank}{select_exp} "
             f"FROM {from_sql} {where} ORDER BY {order_sql} LIMIT ? OFFSET ?")
    count_sql = f"SELECT COUNT(*) FROM {from_sql} {where}"
    query_params = list(params) + [limit, offset]
    exact_count = not (keyword or location or since or exp_years is not None or category or companies)

    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with connect_db(rows=True) as conn:
            rows = conn.execute(sql, query_params).fetchall()
            has_more = False
            if exact_count:
                total_row = conn.execute(count_sql, params).fetchone()
                total = next(iter(total_row.values())) if isinstance(total_row, dict) else total_row[0]
            else:
                has_more = len(rows) == limit
                total = offset + len(rows) + (1 if has_more else 0)
            total_lower_bound = offset + len(rows)
            total_exact = exact_count or not has_more
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
                "skills":           json.loads(r["skills"] or "[]"),
                "employment_type":  r["employment_type"] or "",
                "interview":        company_interviews.get(r["company"]),
                "is_new":     fs == today,
            })
        if not jobs and total == 0:
            jobs, total = jobs_from_seen_fallback(keyword, location, since, category, companies, limit, offset, today)
        return jsonify({
            "jobs": jobs,
            "total": total,
            "total_lower_bound": total_lower_bound,
            "total_exact": total_exact,
            "has_more": has_more,
            "page": page,
            "limit": limit,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
