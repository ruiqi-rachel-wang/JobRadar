import json
import os
import re
import secrets
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from html import escape as html_escape
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import stripe
except ModuleNotFoundError:
    stripe = None

from .database import connect_auth_db, connect_db, init_auth_db, placeholders
try:
    from scanner_service import run_scan
    from scan_store import (
        latest_scan as latest_scan_record,
        repair_abandoned_scan_totals,
        running_scan as running_scan_record,
        scan_companies as scan_company_records,
    )
except ModuleNotFoundError:
    from ..scanner_service import run_scan
    from ..scan_store import (
        latest_scan as latest_scan_record,
        repair_abandoned_scan_totals,
        running_scan as running_scan_record,
        scan_companies as scan_company_records,
    )

from .job_data import (
    add_or_update_company, current_companies, get_app_state, init_db, jobs_from_seen_fallback,
    set_app_state,
)
from .common import DATA_DIR
from .apis import fetch_jobs
from .utils import experience_match_score, extract_keywords
from .search import (
    JOB_DATE_ORDER_SQL, display_location, display_title, keyword_groups, keyword_where,
)
from .taxonomy import migrate_saved_filter

SECRET_KEY = os.environ.get("SECRET_KEY") or os.environ.get("JOBRADAR_SECRET_KEY")
IS_PRODUCTION = (
    os.environ.get("FLASK_ENV") == "production"
    or os.environ.get("JOBDROP_ENV") == "production"
    or bool(os.environ.get("RENDER"))
)
if IS_PRODUCTION and not SECRET_KEY:
    raise RuntimeError("SECRET_KEY or JOBRADAR_SECRET_KEY must be set in production.")

app = Flask(__name__, template_folder="../templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.secret_key = SECRET_KEY or secrets.token_hex(32)
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = (
    os.environ.get("SESSION_COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
    or IS_PRODUCTION
)
init_db()
init_auth_db()

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or os.environ.get("JOBRADAR_ADMIN_PASSWORD")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID") or os.environ.get("JOBDROP_STRIPE_PRICE_ID")
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY") or os.environ.get("JOBDROP_STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET") or os.environ.get("JOBDROP_STRIPE_WEBHOOK_SECRET")
FREE_APPLY_LIMIT = int(os.environ.get("JOBDROP_FREE_APPLY_LIMIT", "30"))
SMTP_HOST = os.environ.get("JOBDROP_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("JOBDROP_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("JOBDROP_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("JOBDROP_SMTP_PASSWORD", "")
SMTP_USE_TLS = os.environ.get("JOBDROP_SMTP_USE_TLS", "true").lower() not in {"0", "false", "no"}
EMAIL_FROM = os.environ.get("JOBDROP_EMAIL_FROM", "")
EMAIL_FROM_NAME = os.environ.get("JOBDROP_EMAIL_FROM_NAME", "JobDrop")
PUBLIC_BASE_URL = os.environ.get("JOBDROP_PUBLIC_URL", "https://jobdrop.site").rstrip("/")
EMAIL_ALERTS_CONFIGURED = bool(
    SMTP_HOST and EMAIL_FROM
)
_EMAIL_ALERT_LOCK = threading.Lock()
if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


@app.after_request
def add_security_headers(response):
    """Apply browser protections without blocking JobDrop's existing inline UI."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'",
    )
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
    return response


@app.before_request
def reject_cross_site_writes():
    """Reject browser-initiated cross-site mutations while allowing webhooks."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return None
    if request.path == "/stripe/webhook":
        return None

    fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
    origin = request.headers.get("Origin", "")
    expected_origin = request.host_url.rstrip("/")
    if fetch_site == "cross-site" or (
        origin and origin.rstrip("/") != expected_origin
    ):
        return jsonify(error="Cross-site request rejected."), 403
    return None

def _is_admin():
    return bool(session.get("admin_ok"))

def _is_user():
    return bool(session.get("user_id") or session.get("admin_ok"))

def _current_user_id():
    return session.get("user_id")

def _ensure_password_admin_user():
    """Return the persistent account behind the password-only admin login."""
    with connect_auth_db(rows=True) as conn:
        row = conn.execute(
            "SELECT id, role FROM users WHERE username=?",
            ("admin",),
        ).fetchone()
        if row:
            if row["role"] != "admin":
                raise RuntimeError("The reserved admin username belongs to a non-admin account.")
            return row["id"]
        # The environment password remains the only admin credential. This
        # unusable random hash merely satisfies the persistent user schema.
        conn.execute(
            "INSERT INTO users (username, password_hash, role, plan_status) "
            "VALUES (?, ?, 'admin', 'free')",
            ("admin", generate_password_hash(secrets.token_urlsafe(32))),
        )
        row = conn.execute(
            "SELECT id FROM users WHERE username=?",
            ("admin",),
        ).fetchone()
        return row["id"]

def _has_paid_access():
    if _is_admin():
        return True
    uid = _current_user_id()
    if not uid:
        return False
    with connect_auth_db(rows=True) as conn:
        row = conn.execute(
            "SELECT role, plan_status FROM users WHERE id=?",
            (uid,),
        ).fetchone()
    return bool(row and (row["role"] == "admin" or row["plan_status"] == "pro"))

def _current_user_profile():
    uid = _current_user_id()
    if not uid:
        return None
    with connect_auth_db(rows=True) as conn:
        return conn.execute(
            "SELECT id, username, email, role, plan_status, plan_started_at, "
            "plan_current_period_end, email_alerts_enabled "
            "FROM users WHERE id=?",
            (uid,),
        ).fetchone()

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

def _paid_required():
    if not _current_user_id() and not _is_admin():
        return jsonify({"error": "Login required"}), 401
    if not _has_paid_access():
        return jsonify({"error": "Pro required", "code": "pro_required"}), 402
    return None

def _crawler_locks():
    try:
        with connect_db(rows=True) as conn:
            rows = conn.execute(
                "SELECT REPLACE(key, 'crawler_403_until:', '') AS company, value "
                "FROM app_state WHERE key LIKE 'crawler_403_until:%'"
            ).fetchall()
        return {row["company"]: row["value"] for row in rows}
    except Exception:
        return {}

def _company_is_public(company, locks=None):
    locks = locks or {}
    name = company.get("name")
    return (
        bool(name)
        and company.get("scan_enabled", True) is not False
        and not company.get("policy")
        and name not in locks
    )

def _admin_companies():
    locks = _crawler_locks()
    annotated = []
    for company in current_companies():
        item = dict(company)
        name = item.get("name")
        if name in locks:
            item["crawler_locked_until"] = locks[name]
        if not _company_is_public(item, locks):
            item["admin_only"] = True
        annotated.append(item)
    return annotated

def _public_companies():
    locks = _crawler_locks()
    return [dict(company) for company in current_companies() if _company_is_public(company, locks)]

@app.before_request
def attach_password_admin_identity():
    """Upgrade existing password-only admin sessions to a persistent account."""
    if not _is_admin() or _current_user_id():
        return None
    try:
        session["user_id"] = _ensure_password_admin_user()
        session["username"] = "admin"
    except Exception:
        app.logger.exception("Could not attach the persistent admin account")
    return None

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
        or request.path.startswith("/api/admin/")
        or request.path.startswith("/api/scans/")
        or request.path in {
            "/api/clear_data",
            "/api/detect_ats",
            "/api/add_detected_company",
            "/api/progress",
            "/api/summary",
            "/api/scans/latest",
            "/api/scans/recent",
            "/api/schedule",
        }
    )
    if protected:
        return _admin_required()
    return None

def _now_db():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _parse_db_dt(value):
    if not value:
        return None
    text = str(value).replace("T", " ").replace("Z", "").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None

def _db_dt_iso(value):
    """Return a browser-safe timestamp with the server's UTC offset attached."""
    dt = _parse_db_dt(value)
    if not dt:
        return ""
    if dt.tzinfo is None:
        # Existing auth timestamps are stored in the server's local timezone.
        # astimezone() supplies the correct historical UTC offset (including DST).
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")

def _days_since(value):
    dt = _parse_db_dt(value)
    if not dt:
        return None
    return max(0, (datetime.now() - dt.replace(tzinfo=None)).days)

def _ts_to_db(value):
    if not value:
        return ""
    return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")

def _stripe_ready(require_webhook=False):
    if not stripe:
        return False, "Stripe Python package is not installed."
    if not STRIPE_SECRET_KEY:
        return False, "STRIPE_SECRET_KEY is not configured."
    if not STRIPE_PRICE_ID:
        return False, "STRIPE_PRICE_ID is not configured."
    if require_webhook and not STRIPE_WEBHOOK_SECRET:
        return False, "STRIPE_WEBHOOK_SECRET is not configured."
    return True, ""

def _plan_from_subscription_status(status):
    if status in {"active", "trialing"}:
        return "pro" if status == "active" else "trial"
    if status in {"past_due", "unpaid", "incomplete", "incomplete_expired"}:
        return status
    return "free"

def _update_user_subscription(uid, *, customer_id="", subscription_id="", status="", period_end="", started_at=""):
    plan_status = _plan_from_subscription_status(status)
    with connect_auth_db() as conn:
        if plan_status == "free":
            conn.execute(
                "UPDATE users SET plan_status='free', plan_current_period_end=?, "
                "stripe_customer_id=COALESCE(NULLIF(?, ''), stripe_customer_id), "
                "stripe_subscription_id='' WHERE id=?",
                (period_end or "", customer_id or "", uid),
            )
        else:
            conn.execute(
                "UPDATE users SET plan_status=?, "
                "plan_started_at=CASE WHEN COALESCE(plan_started_at, '') = '' THEN ? ELSE plan_started_at END, "
                "plan_current_period_end=?, "
                "stripe_customer_id=COALESCE(NULLIF(?, ''), stripe_customer_id), "
                "stripe_subscription_id=COALESCE(NULLIF(?, ''), stripe_subscription_id) "
                "WHERE id=?",
                (plan_status, started_at or _now_db(), period_end or "", customer_id or "", subscription_id or "", uid),
            )

def _saved_search_url(search_row):
    params = {}
    if search_row["kw"]:
        params["keyword"] = search_row["kw"]
    if search_row["loc"]:
        params["location"] = search_row["loc"]
    if search_row["exp_yrs"]:
        params["experience"] = search_row["exp_yrs"]
    if search_row["since"] and search_row["since"] != "any":
        params["since"] = search_row["since"]
    if search_row["category"] and search_row["category"] != "All":
        params["category"] = search_row["category"]
    if search_row["tag"]:
        params["tag"] = search_row["tag"]
    return f"{PUBLIC_BASE_URL}/app" + (f"?{urlencode(params)}" if params else "")

def _saved_search_alert_jobs(search_row, cutoff, window_end):
    kw = (search_row["kw"] or "").strip().lower()
    location = (search_row["loc"] or "").strip().lower()
    since = (search_row["since"] or "").strip()
    category, tag = migrate_saved_filter(search_row["category"], search_row["tag"])
    exp_years_raw = (search_row["exp_yrs"] or "").strip()
    exp_years = None
    if exp_years_raw:
        try:
            exp_years = max(0.0, float(exp_years_raw))
        except ValueError:
            exp_years = None

    kw_groups = keyword_groups(kw)
    clauses, params = [
        "is_open = TRUE",
        "COALESCE(first_seen, '') > ?",
        "COALESCE(first_seen, '') <= ?",
    ], [cutoff, window_end]
    if kw:
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
    if since and since != "any":
        clauses.append("COALESCE(NULLIF(posted,''), substr(first_seen,1,10)) >= ?")
        params.append(since)
    if exp_years is not None:
        clauses.append("(exp_min IS NULL OR exp_min <= ?)")
        params.append(int(exp_years) + 1)
    if category and category != "All":
        clauses.append("categories LIKE ?")
        params.append(f'%"{category}"%')

    visible_companies = _admin_companies() if search_row["role"] == "admin" else _public_companies()
    active_company_names = {company.get("name") for company in visible_companies if company.get("name")}
    if tag:
        tag_company_names = {
            company.get("name")
            for company in visible_companies
            if company.get("name") and tag in (company.get("tags") or [])
        }
        if not tag_company_names:
            return []
        tag_marks = placeholders(len(tag_company_names))
        clauses.append(f"company IN ({tag_marks})")
        params.extend(sorted(tag_company_names))
    hidden_company_names = sorted(
        company.get("name")
        for company in current_companies()
        if company.get("name") and company.get("name") not in active_company_names
    )
    if hidden_company_names:
        hidden_marks = placeholders(len(hidden_company_names))
        clauses.append(f"company NOT IN ({hidden_marks})")
        params.extend(hidden_company_names)

    sql = (
        "SELECT id, title, url, company, location, first_seen, posted, exp_label "
        f"FROM jobs WHERE {' AND '.join(clauses)} "
        f"ORDER BY {JOB_DATE_ORDER_SQL}"
    )
    with connect_db(rows=True) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "title": display_title(row["title"] or "", row["url"] or ""),
            "company": row["company"] or "",
            "location": display_location(row["location"] or "", row["title"] or "", row["url"] or ""),
            "url": row["url"] or "",
            "posted": (row["posted"] or "")[:10],
            "first_seen": (row["first_seen"] or "")[:10],
            "exp_label": row["exp_label"] or "",
        }
        for row in rows
    ]

def _send_email(to_email, subject, text_body, html_body):
    if not EMAIL_ALERTS_CONFIGURED:
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>" if EMAIL_FROM_NAME else EMAIL_FROM
    msg["To"] = to_email
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as smtp:
        if SMTP_USE_TLS:
            smtp.starttls()
        if SMTP_USER or SMTP_PASSWORD:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
        smtp.send_message(msg)
    return True

def _send_saved_search_alerts(window_end=None):
    summary = {"configured": EMAIL_ALERTS_CONFIGURED, "searches": 0, "emails": 0, "jobs": 0, "failed": 0}
    if not EMAIL_ALERTS_CONFIGURED:
        return summary
    if not _EMAIL_ALERT_LOCK.acquire(blocking=False):
        summary["skipped"] = "already_running"
        return summary
    try:
        now = window_end or datetime.now()
        window_end_text = now.strftime("%Y-%m-%d %H:%M:%S")
        with connect_auth_db(rows=True) as conn:
            rows = conn.execute(
                "SELECT s.id, s.name, s.kw, s.loc, s.exp_yrs, s.since, s.category, s.tag, "
                "s.alert_cursor_at, s.last_alert_sent_at, s.created_at, "
                "u.username, u.email, u.role "
                "FROM user_saved_searches s JOIN users u ON u.id=s.user_id "
                "WHERE s.email_alert_enabled=1 "
                "AND u.email_alerts_enabled=1 "
                "AND COALESCE(u.email, '') != '' "
                "AND (u.plan_status IN ('pro', 'trial') OR u.role='admin')"
            ).fetchall()

        for row in rows:
            summary["searches"] += 1
            last_sent_dt = _parse_db_dt(row["last_alert_sent_at"])
            # A search's first alert contains only jobs found today. After the
            # first successful alert, include everything found since that email.
            cutoff_dt = last_sent_dt or (
                now.replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(seconds=1)
            )
            cutoff = min(cutoff_dt, now).strftime("%Y-%m-%d %H:%M:%S")
            jobs = _saved_search_alert_jobs(row, cutoff, window_end_text)
            if not jobs:
                with connect_auth_db() as conn:
                    conn.execute(
                        "UPDATE user_saved_searches SET alert_cursor_at=? WHERE id=?",
                        (window_end_text, row["id"]),
                    )
                continue

            search_url = _saved_search_url(row)
            lines = [f"New JobDrop matches for {row['name']}:", ""]
            items_html = []
            for job in jobs:
                title = job["title"] or "Untitled role"
                company = job["company"] or "Unknown company"
                location = f" - {job['location']}" if job["location"] else ""
                exp = f" ({job['exp_label']})" if job["exp_label"] else ""
                plain_title = f"{title} ({job['url']})" if job["url"] else title
                lines.append(f"- {plain_title} at {company}{location}{exp}")
                title_html = html_escape(title)
                if job["url"]:
                    title_html = (
                        f"<a href=\"{html_escape(job['url'])}\">{title_html}</a>"
                    )
                items_html.append(
                    "<li>"
                    f"<strong>{title_html}</strong> at {html_escape(company)}"
                    f"{html_escape(location)}{html_escape(exp)}"
                    + "</li>"
                )
            lines.extend(["", f"Open this search: {search_url}", "", "You can turn this off from Profile or the saved-search toggle."])
            html_body = (
                f"<p><strong>{len(jobs)} new JobDrop matches</strong> for {html_escape(row['name'])}:</p>"
                f"<ul>{''.join(items_html)}</ul>"
                f"<p><a href=\"{html_escape(search_url)}\">Open this search</a></p>"
                "<p>You can turn this off from Profile or the saved-search toggle.</p>"
            )
            try:
                delivered = _send_email(
                    row["email"],
                    f"JobDrop: {len(jobs)} new jobs for {row['name']}",
                    "\n".join(lines),
                    html_body,
                )
                if delivered:
                    with connect_auth_db() as conn:
                        conn.execute(
                            "UPDATE user_saved_searches "
                            "SET alert_cursor_at=?, last_alert_sent_at=? WHERE id=?",
                            (window_end_text, window_end_text, row["id"]),
                        )
                    summary["emails"] += 1
                    summary["jobs"] += len(jobs)
                else:
                    summary["failed"] += 1
            except Exception as exc:
                summary["failed"] += 1
                print(f"Saved-search alert failed for search {row['id']}: {exc}", flush=True)
        return summary
    finally:
        _EMAIL_ALERT_LOCK.release()

def _user_id_for_subscription(subscription):
    metadata = getattr(subscription, "metadata", None) or {}
    uid = metadata.get("jobdrop_user_id")
    if uid:
        return uid
    sub_id = getattr(subscription, "id", "") or ""
    customer_id = getattr(subscription, "customer", "") or ""
    # Match only non-empty ids: '' equals the column default, so an empty id
    # would match an arbitrary user who has never touched Stripe.
    clauses, params = [], []
    if sub_id:
        clauses.append("stripe_subscription_id=?")
        params.append(sub_id)
    if customer_id:
        clauses.append("stripe_customer_id=?")
        params.append(customer_id)
    if not clauses:
        return None
    with connect_auth_db(rows=True) as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE " + " OR ".join(clauses),
            params,
        ).fetchone()
    return row["id"] if row else None

@app.before_request
def touch_user_activity():
    uid = session.get("user_id")
    if not uid:
        return None
    now_ts = int(time.time())
    # Bound writes while keeping the admin activity value close to real time.
    if now_ts - int(session.get("_last_active_touch", 0) or 0) < 60:
        return None
    session["_last_active_touch"] = now_ts
    with connect_auth_db() as conn:
        conn.execute("UPDATE users SET last_active_at=? WHERE id=?", (_now_db(), uid))
    return None

# ── Scan state + schedule ─────────────────────────────────────────────────────

scan_state = {
    "running": False, "done": False, "started_at": 0,
    "progress": [], "new_count": 0,
    "scan_run_id": None, "scan_type": "fresh", "timing": {},
}
_scan_cancel_event = threading.Event()

# ── Auto-schedule ──────────────────────────────────────────────────────────────

_last_schedule_trigger: str = ""   # "YYYY-MM-DD HH:MM" of last triggered slot
_schedule_cache: dict = {}
_SCHEDULE_FILE = Path(DATA_DIR) / "scan_schedule.json"
_SCHEDULE_QUEUE_FILE = Path(DATA_DIR) / "queued_scan.json"
_schedule_file_lock = threading.Lock()
_schedule_queue_lock = threading.Lock()


def _write_schedule_file(schedule: dict) -> None:
    """Persist the schedule without competing with scan writes to the jobs DB."""
    global _schedule_cache
    with _schedule_file_lock:
        _SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = _SCHEDULE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(schedule), encoding="utf-8")
        temporary.replace(_SCHEDULE_FILE)
        _schedule_cache = schedule


def _get_schedule() -> dict:
    """Load the file schedule, migrating the legacy DB value once if needed."""
    global _schedule_cache
    if _SCHEDULE_FILE.exists():
        try:
            with _schedule_file_lock:
                parsed = json.loads(_SCHEDULE_FILE.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                _schedule_cache = parsed
                return _schedule_cache
        except Exception:
            pass
    try:
        raw = get_app_state("scan_schedule")
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            _write_schedule_file(parsed)
    except Exception:
        pass
    return _schedule_cache


def _queue_scheduled_scan(scan_type, scheduled_for="", send_alerts_after=False):
    """Keep only the newest missed schedule while another scan is running."""
    if scan_type not in {"fresh", "full"}:
        return
    payload = {
        "scan_type": scan_type,
        "queued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scheduled_for": scheduled_for or "",
        "send_alerts_after": bool(send_alerts_after),
    }
    with _schedule_queue_lock:
        temporary = _SCHEDULE_QUEUE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.replace(_SCHEDULE_QUEUE_FILE)


def _pop_queued_scan():
    payload = {}
    with _schedule_queue_lock:
        if _SCHEDULE_QUEUE_FILE.exists():
            try:
                loaded = json.loads(_SCHEDULE_QUEUE_FILE.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    payload = loaded
            except Exception:
                payload = {}
            _SCHEDULE_QUEUE_FILE.unlink(missing_ok=True)
    # Migrate any queue left by an older deployment after the active scan has
    # released the jobs database lock.
    if not payload.get("scan_type"):
        try:
            payload["scan_type"] = (get_app_state("queued_scan_type") or "").strip()
        except Exception:
            payload["scan_type"] = ""
    try:
        set_app_state("queued_scan_type", "")
        set_app_state("queued_scan_queued_at", "")
    except Exception:
        pass
    scan_type = str(payload.get("scan_type") or "")
    return {
        "scan_type": scan_type if scan_type in {"fresh", "full"} else "",
        "scheduled_for": str(payload.get("scheduled_for") or ""),
        "send_alerts_after": bool(payload.get("send_alerts_after")),
    }


def _pop_queued_scan_type():
    """Compatibility helper for callers that need only the queued type."""
    return _pop_queued_scan()["scan_type"]


def _is_final_fresh_schedule_slot(scan_type, cfg, time_text):
    """True only for the last configured fresh-scan slot of the day."""
    times = [
        str(value) for value in (cfg.get("times") or [])
        if re.match(r"^(?:[01]\d|2[0-3]):[0-5]\d$", str(value))
    ]
    return scan_type == "fresh" and bool(cfg.get("enabled")) and bool(times) and time_text == max(times)


def _try_run_scheduled():
    """Fire a scheduled scan if the current minute matches a configured slot."""
    global _last_schedule_trigger
    current = datetime.now()
    now = current.strftime("%H:%M")
    minute_key = current.strftime("%Y-%m-%d %H:%M")
    if minute_key == _last_schedule_trigger:
        return  # already fired this minute
    schedule = _get_schedule()
    for scan_type in ("fresh", "full"):
        cfg = schedule.get(scan_type, {})
        if cfg.get("enabled") and now in (cfg.get("times") or []):
            _last_schedule_trigger = minute_key
            send_alerts_after = _is_final_fresh_schedule_slot(scan_type, cfg, now)
            names = _scheduled_scan_names()
            if not names:
                return
            if scan_state["running"] or running_scan_record():
                _queue_scheduled_scan(scan_type, minute_key, send_alerts_after)
                return
            threading.Thread(
                target=scan_runner,
                args=(names, scan_type, minute_key, send_alerts_after),
                daemon=True,
            ).start()
            break


def _scheduled_scan_names():
    """Return every company eligible for scheduled scanning."""
    return [
        c["name"] for c in current_companies()
        if c.get("name") and (c.get("scan_enabled", True) is not False or c.get("policy"))
    ]


def _schedule_loop():
    """Background thread: checks every 30 s whether a scheduled slot should fire."""
    while True:
        time.sleep(30)
        try:
            _try_run_scheduled()
        except Exception:
            pass
        try:
            # Also drain the missed-slot queue here. Draining only from a
            # finishing scan's `finally` loses the queued scan whenever that
            # single check hits a stale running-row or the app restarts.
            _start_queued_scan_if_idle()
        except Exception:
            pass


if os.environ.get("JOBRADAR_ENABLE_APP_SCHEDULER", "true").lower() in {"1", "true", "yes"}:
    threading.Thread(target=_schedule_loop, daemon=True).start()


def _resume_interrupted_scan():
    """Resume an interrupted scan instead of abandoning the remaining companies."""
    try:
        repair_abandoned_scan_totals()
        row = running_scan_record()
        if row:
            threading.Thread(target=resume_scan_runner, args=(dict(row),), daemon=True).start()
    except Exception:
        pass

def scan_runner(selected_names, scan_type="fresh", scheduled_for="", send_alerts_after=False):
    try:
        do_scan(
            selected_names,
            scan_type=scan_type,
            scheduled_for=scheduled_for,
            send_alerts_after=send_alerts_after,
        )
    except Exception as e:
        scan_state.update(running=False, done=True, new_count=0)
        for p in scan_state.get("progress", []):
            if p.get("status") in {"pending", "scanning"}:
                p.update(status="error", error=str(e), count=0, new=0)
    finally:
        _start_queued_scan_if_idle()


def resume_scan_runner(scan_run):
    scan_run_id = scan_run["id"]
    scan_type = scan_run.get("scan_type") or "fresh"
    rows = [dict(row) for row in scan_company_records(scan_run_id)]
    remaining = [
        row["company_name"] for row in rows
        if row.get("status") in {"pending", "scanning"}
    ]
    try:
        do_scan(
            remaining,
            scan_type=scan_type,
            resume_scan_run_id=scan_run_id,
            scheduled_for=scan_run.get("scheduled_for") or "",
            send_alerts_after=bool(scan_run.get("send_alerts_after")),
        )
    except Exception as e:
        scan_state.update(running=False, done=True, new_count=0)
        for p in scan_state.get("progress", []):
            if p.get("status") in {"pending", "scanning"}:
                p.update(status="error", error=str(e), count=0, new=0)
    finally:
        _start_queued_scan_if_idle()


def _start_queued_scan_if_idle():
    # Same staleness rule as start_scan: a scan "running" for 6h+ is wedged,
    # not running — don't let it block queued scheduled scans forever.
    if scan_state.get("running") and time.time() - float(scan_state.get("started_at") or 0) > 6 * 60 * 60:
        scan_state.update(running=False, done=True)
    if scan_state.get("running"):
        return
    if running_scan_record():
        return
    queued = _pop_queued_scan()
    if not queued["scan_type"]:
        return
    names = _scheduled_scan_names()
    if names:
        threading.Thread(
            target=scan_runner,
            args=(
                names,
                queued["scan_type"],
                queued["scheduled_for"],
                queued["send_alerts_after"],
            ),
            daemon=True,
        ).start()


def do_scan(selected_names, scan_type="fresh", resume_scan_run_id=None,
            scheduled_for="", send_alerts_after=False):
    _scan_cancel_event.clear()
    scan_state.update(
        running=True, done=False, started_at=time.time(),
        progress=[], new_count=0,
        scan_run_id=resume_scan_run_id, scan_type=scan_type, timing={},
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
        resume_scan_run_id=resume_scan_run_id,
        timing=scan_state["timing"],
        scheduled_for=scheduled_for,
        send_alerts_after=send_alerts_after,
    )
    scan_state.update(
        running=False,
        done=True,
        new_count=result["new_count"],
        scan_run_id=result.get("scan_run_id") or scan_state.get("scan_run_id"),
    )
    if (
        scan_type == "fresh"
        and send_alerts_after
        and result.get("status") in {"success", "partial_success"}
    ):
        try:
            alert_summary = _send_saved_search_alerts()
            print(f"Saved-search daily alerts: {alert_summary}", flush=True)
        except Exception as exc:
            # Email delivery is downstream of scanning; never turn a completed
            # scan into an error because the mail provider or auth DB is down.
            print(f"Saved-search daily alert dispatch failed: {exc}", flush=True)


_resume_interrupted_scan()
_start_queued_scan_if_idle()


# ── Routes ────────────────────────────────────────────────────────────────────

def _user_ctx():
    """Template context for the current user."""
    uid = session.get("user_id")
    if uid:
        with connect_auth_db(rows=True) as conn:
            row = conn.execute(
                "SELECT username, email, role, plan_status, plan_started_at, email_alerts_enabled FROM users WHERE id=?",
                (uid,)
            ).fetchone()
        if row:
            return {
                "username": row["username"],
                "email": row["email"] or "",
                "is_admin": row["role"] == "admin",
                "plan_status": "admin" if row["role"] == "admin" else (row["plan_status"] or "free"),
                "paid_member_days": _days_since(row["plan_started_at"]),
                "email_alerts_enabled": bool(row["email_alerts_enabled"]),
            }
    if _is_admin():
        return {
            "username": "Admin",
            "email": "",
            "is_admin": True,
            "plan_status": "admin",
            "paid_member_days": None,
            "email_alerts_enabled": False,
        }
    return {
        "username": None,
        "email": "",
        "is_admin": False,
        "plan_status": "free",
        "paid_member_days": None,
        "email_alerts_enabled": False,
    }

@app.route("/")
def public_view():
    return render_template("user.html", companies=_public_companies(),
                           is_public=True, is_admin=False, username=None,
                           has_apply_access=False, is_pro=False,
                           apply_limit=0, email="", email_alerts_enabled=False,
                           email_alerts_configured=EMAIL_ALERTS_CONFIGURED)

def _slugify(value):
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")

def _company_for_slug(slug):
    for company in _public_companies():
        if _slugify(company.get("name", "")) == slug:
            return company
    return None

def _job_query(where_sql="", params=(), limit=60):
    where = f"WHERE is_open = TRUE {where_sql}"
    sql = (
        "SELECT id, title, url, company, location, posted, first_seen, exp_label, "
        "skills, keywords, description FROM jobs "
        f"{where} "
        f"ORDER BY {JOB_DATE_ORDER_SQL} "
        "LIMIT ?"
    )
    with connect_db(rows=True) as conn:
        rows = conn.execute(sql, list(params) + [limit]).fetchall()
    jobs = []
    for row in rows:
        desc = " ".join((row["description"] or "").split())
        skills = json.loads(row["skills"] or "[]")
        keywords = json.loads(row["keywords"] or "[]")
        if not keywords:
            keywords = extract_keywords(row["title"] or "", desc, skills)
        jobs.append({
            "id": row["id"],
            "title": display_title(row["title"] or "", row["url"] or ""),
            "url": row["url"] or "",
            "company": row["company"] or "",
            "location": display_location(row["location"] or "", row["title"] or "", row["url"] or ""),
            "posted": (row["posted"] or "")[:10],
            "first_seen": (row["first_seen"] or "")[:10],
            "exp_label": row["exp_label"] or "",
            "skills": skills,
            "keywords": keywords,
            "description": desc[:320],
        })
    return jobs

@app.route("/companies")
def companies_page():
    companies = [dict(c, slug=_slugify(c.get("name", ""))) for c in _public_companies()]
    return render_template(
        "companies.html",
        companies=companies,
        canonical_url=url_for("companies_page", _external=True),
        title="Companies hiring now | JobDrop",
        description="Browse companies with current job openings tracked directly from official career pages by JobDrop.",
    )

@app.route("/companies/<slug>/jobs")
def company_jobs_page(slug):
    company = _company_for_slug(slug)
    if not company:
        return "Company not found", 404
    name = company.get("name", "")
    jobs = _job_query("AND company = ?", [name])
    return render_template(
        "seo_jobs.html",
        canonical_url=url_for("company_jobs_page", slug=slug, _external=True),
        title=f"{name} jobs | JobDrop",
        h1=f"{name} jobs",
        description=f"Current {name} job openings tracked from the company's official careers page.",
        intro=f"Browse current {name} openings that JobDrop found directly from the company's careers source.",
        jobs=jobs,
        companies=[dict(c, slug=_slugify(c.get("name", ""))) for c in _public_companies()[:24]],
    )

@app.route("/jobs/new-grad")
def new_grad_jobs_page():
    jobs = _job_query(
        "AND (LOWER(title) LIKE ? OR LOWER(title) LIKE ? OR LOWER(title) LIKE ? OR LOWER(description) LIKE ?)",
        ["%new grad%", "%entry level%", "%intern%", "%new grad%"],
    )
    return render_template(
        "seo_jobs.html",
        canonical_url=url_for("new_grad_jobs_page", _external=True),
        title="New grad jobs | JobDrop",
        h1="New grad jobs",
        description="Browse new grad, intern, and entry-level roles found directly from company career pages.",
        intro="These openings are filtered for new grad, intern, and entry-level signals from current JobDrop listings.",
        jobs=jobs,
        companies=[dict(c, slug=_slugify(c.get("name", ""))) for c in _public_companies()[:24]],
    )

@app.route("/jobs/software-engineer/remote")
def remote_software_jobs_page():
    jobs = _job_query(
        "AND LOWER(title) LIKE ? AND LOWER(location) LIKE ?",
        ["%engineer%", "%remote%"],
    )
    return render_template(
        "seo_jobs.html",
        canonical_url=url_for("remote_software_jobs_page", _external=True),
        title="Remote software engineer jobs | JobDrop",
        h1="Remote software engineer jobs",
        description="Browse remote software engineering jobs found directly from official company career pages.",
        intro="Remote software engineering openings from JobDrop's company-career-page index.",
        jobs=jobs,
        companies=[dict(c, slug=_slugify(c.get("name", ""))) for c in _public_companies()[:24]],
    )

@app.route("/jobs/software-engineer/new-york")
def new_york_software_jobs_page():
    jobs = _job_query(
        "AND LOWER(title) LIKE ? AND LOWER(location) LIKE ?",
        ["%engineer%", "%new york%"],
    )
    return render_template(
        "seo_jobs.html",
        canonical_url=url_for("new_york_software_jobs_page", _external=True),
        title="Software engineer jobs in New York | JobDrop",
        h1="Software engineer jobs in New York",
        description="Browse New York software engineering jobs found directly from official company career pages.",
        intro="Current New York software engineering openings from JobDrop's company-career-page index.",
        jobs=jobs,
        companies=[dict(c, slug=_slugify(c.get("name", ""))) for c in _public_companies()[:24]],
    )

@app.route("/robots.txt")
def robots_txt():
    sitemap_url = url_for("sitemap_xml", _external=True)
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /app\n"
        "Disallow: /login\n"
        "Disallow: /signup\n"
        "Disallow: /api/\n"
        f"Sitemap: {sitemap_url}\n"
    )
    return Response(body, content_type="text/plain; charset=utf-8")

@app.route("/sitemap.xml")
def sitemap_xml():
    urls = [
        url_for("public_view", _external=True),
        url_for("companies_page", _external=True),
        url_for("new_grad_jobs_page", _external=True),
        url_for("remote_software_jobs_page", _external=True),
        url_for("new_york_software_jobs_page", _external=True),
    ]
    urls.extend(
        url_for("company_jobs_page", slug=_slugify(c.get("name", "")), _external=True)
        for c in _public_companies()[:200]
        if c.get("name")
    )
    lastmod = datetime.now().strftime("%Y-%m-%d")
    body = '<?xml version="1.0" encoding="UTF-8"?>\n'
    body += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for loc in urls:
        body += f"  <url><loc>{html_escape(loc)}</loc><lastmod>{lastmod}</lastmod></url>\n"
    body += "</urlset>\n"
    return Response(body, content_type="application/xml; charset=utf-8")

@app.route("/app")
def app_view():
    ctx = _user_ctx()
    companies = _admin_companies() if ctx["is_admin"] else _public_companies()
    is_pro = _has_paid_access()
    return render_template("user.html", companies=companies,
                           is_public=False, is_admin=ctx["is_admin"],
                           username=ctx["username"],
                           email=ctx.get("email", ""),
                           plan_status=ctx.get("plan_status", "free"),
                           paid_member_days=ctx.get("paid_member_days"),
                           has_apply_access=bool(_current_user_id()),
                           is_pro=is_pro,
                           apply_limit=0 if is_pro else FREE_APPLY_LIMIT,
                           email_alerts_enabled=ctx.get("email_alerts_enabled", False),
                           email_alerts_configured=EMAIL_ALERTS_CONFIGURED)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        # Admin shortcut: username "admin" + env password
        if ADMIN_PASSWORD and username.lower() == "admin" and secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["admin_ok"] = True
            try:
                session["user_id"] = _ensure_password_admin_user()
                session["username"] = "admin"
                session["_last_active_touch"] = int(time.time())
            except Exception:
                app.logger.exception("Could not attach the persistent admin account during login")
            return redirect(_safe_next("admin"))
        # Regular user lookup
        with connect_auth_db(rows=True) as conn:
            row = conn.execute(
                "SELECT id, username, password_hash, role FROM users WHERE username=?",
                (username,)
            ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session["user_id"]  = row["id"]
            session["username"] = row["username"]
            now = _now_db()
            session["_last_active_touch"] = int(time.time())
            with connect_auth_db() as conn:
                conn.execute(
                    "UPDATE users SET last_login_at=?, last_active_at=? WHERE id=?",
                    (now, now, row["id"]),
                )
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
        if username.lower() == "admin":
            return render_template("auth.html", mode="signup",
                                   error="Username already taken."), 400
        if not re.match(r'^[a-zA-Z0-9_.-]{3,30}$', username):
            return render_template("auth.html", mode="signup",
                                   error="Username: 3-30 chars, letters/numbers/._- only."), 400
        if len(password) < 8:
            return render_template("auth.html", mode="signup",
                                   error="Password must be at least 8 characters."), 400
        if password != confirm:
            return render_template("auth.html", mode="signup",
                                   error="Passwords do not match."), 400
        pw_hash = generate_password_hash(password)
        now = _now_db()
        try:
            with connect_auth_db() as conn:
                conn.execute(
                    "INSERT INTO users (username, email, password_hash, last_login_at, last_active_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (username, email, pw_hash, now, now)
                )
        except Exception as exc:
            detail = str(exc).lower()
            if "unique" in detail or "duplicate" in detail:
                return render_template("auth.html", mode="signup",
                                       error="Username already taken."), 400
            app.logger.exception("Could not create signup account for %r", username)
            return render_template(
                "auth.html", mode="signup",
                error="Could not create your account right now. Please try again."
            ), 503
        # Auto-login after signup
        with connect_auth_db(rows=True) as conn:
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        session.clear()
        session["user_id"]  = row["id"]
        session["username"] = username
        session["_last_active_touch"] = int(time.time())
        return redirect(url_for("app_view"))
    return render_template("auth.html", mode="signup", error="")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("public_view"))

@app.route("/profile", methods=["GET", "POST"])
def profile():
    guard = _user_required()
    if guard:
        return guard
    row = _current_user_profile()
    if not row:
        return redirect(url_for("app_view"))
    message = ""
    error = ""
    if request.method == "POST":
        email = (request.form.get("email") or "").strip()
        alerts_enabled = request.form.get("email_alerts_enabled") == "on"
        if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            error = "Enter a valid email address."
        else:
            with connect_auth_db() as conn:
                conn.execute(
                    "UPDATE users SET email=?, email_alerts_enabled=? WHERE id=?",
                    (email, int(alerts_enabled), row["id"]),
                )
            message = "Profile updated."
            row = _current_user_profile()
    return render_template(
        "profile.html",
        user=row,
        is_pro=_has_paid_access(),
        free_apply_limit=FREE_APPLY_LIMIT,
        email_alerts_configured=EMAIL_ALERTS_CONFIGURED,
        message=message,
        error=error,
    )

@app.route("/billing/checkout", methods=["POST"])
def billing_checkout():
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    ready, message = _stripe_ready()
    if not ready:
        return message, 503
    with connect_auth_db(rows=True) as conn:
        row = conn.execute(
            "SELECT id, username, email, stripe_customer_id FROM users WHERE id=?",
            (uid,),
        ).fetchone()
    if not row:
        return "Login required", 401
    customer_id = row["stripe_customer_id"] or ""
    if not customer_id:
        customer_args = {
            "metadata": {"jobdrop_user_id": str(uid), "username": row["username"] or ""},
        }
        if row["email"]:
            customer_args["email"] = row["email"]
        if row["username"]:
            customer_args["name"] = row["username"]
        customer = stripe.Customer.create(**customer_args)
        customer_id = customer.id
        with connect_auth_db() as conn:
            conn.execute("UPDATE users SET stripe_customer_id=? WHERE id=?", (customer_id, uid))
    checkout = stripe.checkout.Session.create(
        customer=customer_id,
        mode="subscription",
        line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        success_url=url_for("billing_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=url_for("app_view", _external=True) + "?billing=cancelled",
        client_reference_id=str(uid),
        metadata={"jobdrop_user_id": str(uid)},
        subscription_data={"metadata": {"jobdrop_user_id": str(uid)}},
        allow_promotion_codes=True,
    )
    return redirect(checkout.url, code=303)

@app.route("/billing/portal", methods=["POST"])
def billing_portal():
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    ready, message = _stripe_ready()
    if not ready:
        return message, 503
    with connect_auth_db(rows=True) as conn:
        row = conn.execute("SELECT stripe_customer_id FROM users WHERE id=?", (uid,)).fetchone()
    customer_id = (row["stripe_customer_id"] or "") if row else ""
    if not customer_id:
        return redirect(url_for("app_view", billing="no_customer"))
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=url_for("app_view", _external=True),
    )
    return redirect(portal.url, code=303)

@app.route("/billing/success")
def billing_success():
    return redirect(url_for("app_view", billing="success"))

@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    ready, message = _stripe_ready(require_webhook=True)
    if not ready:
        return jsonify({"error": message}), 503
    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        uid = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("jobdrop_user_id")
        customer_id = obj.get("customer") or ""
        subscription_id = obj.get("subscription") or ""
        if uid and subscription_id:
            subscription = stripe.Subscription.retrieve(subscription_id)
            _update_user_subscription(
                uid,
                customer_id=customer_id,
                subscription_id=subscription_id,
                status=subscription.status,
                period_end=_ts_to_db(getattr(subscription, "current_period_end", None)),
                started_at=_ts_to_db(getattr(subscription, "start_date", None)),
            )

    elif event_type in {"customer.subscription.updated", "customer.subscription.deleted"}:
        uid = _user_id_for_subscription(obj)
        if uid:
            _update_user_subscription(
                uid,
                customer_id=obj.get("customer") or "",
                subscription_id=obj.get("id") or "",
                status="canceled" if event_type.endswith(".deleted") else (obj.get("status") or ""),
                period_end=_ts_to_db(obj.get("current_period_end")),
                started_at=_ts_to_db(obj.get("start_date")),
            )

    elif event_type == "invoice.payment_failed":
        subscription_id = obj.get("subscription") or ""
        customer_id = obj.get("customer") or ""
        # Match only non-empty ids: '' equals the column default, so an
        # invoice without a subscription would mark every non-Stripe user past_due.
        clauses, params = [], []
        if subscription_id:
            clauses.append("stripe_subscription_id=?")
            params.append(subscription_id)
        if customer_id:
            clauses.append("stripe_customer_id=?")
            params.append(customer_id)
        if clauses:
            with connect_auth_db() as conn:
                conn.execute(
                    "UPDATE users SET plan_status='past_due' WHERE " + " OR ".join(clauses),
                    params,
                )

    return jsonify({"received": True})

@app.route("/admin")
def admin():
    return render_template("admin.html", companies=_admin_companies())

@app.route("/api/admin/users")
def api_admin_users():
    with connect_auth_db(rows=True) as conn:
        rows = conn.execute(
            "SELECT id, username, email, role, plan_status, plan_started_at, "
            "plan_current_period_end, last_login_at, last_active_at, created_at "
            "FROM users ORDER BY created_at DESC"
        ).fetchall()
    users = []
    for row in rows:
        users.append({
            "id": row["id"],
            "username": row["username"] or "",
            "email": row["email"] or "",
            "role": row["role"] or "user",
            "plan_status": row["plan_status"] or "free",
            "plan_started_at": str(row["plan_started_at"] or ""),
            "plan_current_period_end": str(row["plan_current_period_end"] or ""),
            "last_login_at": _db_dt_iso(row["last_login_at"]),
            "last_active_at": _db_dt_iso(row["last_active_at"]),
            "created_at": str(row["created_at"] or ""),
            "member_days": _days_since(row["created_at"]),
            "paid_member_days": _days_since(row["plan_started_at"]),
        })
    return jsonify(users)

@app.route("/api/admin/users/<int:uid>/plan", methods=["PATCH"])
def api_admin_user_plan(uid):
    d = request.get_json(silent=True) or {}
    status = (d.get("plan_status") or "").strip().lower()
    if status not in {"free", "pro", "trial"}:
        return jsonify({"error": "invalid plan_status"}), 400
    now = _now_db()
    with connect_auth_db() as conn:
        if status == "free":
            conn.execute(
                "UPDATE users SET plan_status='free', plan_started_at='', "
                "plan_current_period_end='', stripe_subscription_id='' WHERE id=?",
                (uid,),
            )
        else:
            conn.execute(
                "UPDATE users SET plan_status=?, "
                "plan_started_at=CASE WHEN COALESCE(plan_started_at, '') = '' THEN ? ELSE plan_started_at END, "
                "plan_current_period_end='' WHERE id=?",
                (status, now, uid),
            )
    return jsonify({"ok": True})

@app.route("/legacy")
def legacy():
    return redirect(url_for("public_view"))

# ── Apply list API ─────────────────────────────────────────────────────────────

@app.route("/api/apply", methods=["GET"])
def get_apply():
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    if not uid and _is_admin():
        return jsonify([])
    with connect_auth_db(rows=True) as conn:
        rows = conn.execute(
            "SELECT job_id, stage, job_title, job_company, job_location, "
            "job_url, job_exp_label, job_posted, added_at FROM user_apply_list "
            "WHERE user_id=? ORDER BY added_at DESC", (uid,)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/apply", methods=["POST"])
def add_apply():
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    if not uid:
        # Password-only admin session has no user row; inserting NULL would 500.
        return jsonify({"error": "Apply list requires a user account"}), 400
    d = request.get_json(silent=True) or {}
    job_id = (d.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id required"}), 400
    with connect_auth_db() as conn:
        existing = conn.execute(
            "SELECT 1 FROM user_apply_list WHERE user_id=? AND job_id=?",
            (uid, job_id),
        ).fetchone()
        if not existing and not _has_paid_access():
            count_row = conn.execute(
                "SELECT COUNT(*) FROM user_apply_list WHERE user_id=?",
                (uid,),
            ).fetchone()
            count = count_row[0] if count_row else 0
            if count >= FREE_APPLY_LIMIT:
                return jsonify({
                    "error": f"Free Apply List limit reached ({FREE_APPLY_LIMIT}).",
                    "code": "apply_limit_reached",
                    "limit": FREE_APPLY_LIMIT,
                }), 402
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
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    stage = (request.get_json(silent=True) or {}).get("stage", "")
    valid = {"to-apply","applied","interview","offer","closed"}
    if stage not in valid:
        return jsonify({"error": "invalid stage"}), 400
    with connect_auth_db() as conn:
        conn.execute("UPDATE user_apply_list SET stage=? WHERE user_id=? AND job_id=?",
                     (stage, uid, job_id))
    return jsonify({"ok": True})

@app.route("/api/apply/<job_id>", methods=["DELETE"])
def remove_apply(job_id):
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    with connect_auth_db() as conn:
        conn.execute("DELETE FROM user_apply_list WHERE user_id=? AND job_id=?", (uid, job_id))
    return jsonify({"ok": True})

@app.route("/api/searches", methods=["GET"])
def get_saved_searches():
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    if not uid:
        return jsonify([])
    with connect_auth_db(rows=True) as conn:
        rows = conn.execute(
            "SELECT id, name, kw, loc, exp_yrs, since, category, tag, email_alert_enabled, created_at "
            "FROM user_saved_searches WHERE user_id=? ORDER BY created_at DESC, id DESC",
            (uid,),
        ).fetchall()
    return jsonify([
        {
            "id": row["id"],
            "name": row["name"] or "",
            "kw": row["kw"] or "",
            "loc": row["loc"] or "",
            "exp": row["exp_yrs"] or "",
            "since": row["since"] or "",
            "cat": row["category"] or "All",
            "tag": row["tag"] or "",
            "email_alert_enabled": bool(row["email_alert_enabled"]),
        }
        for row in rows
    ])

@app.route("/api/searches", methods=["POST"])
def add_saved_search():
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "Saved searches require a user account"}), 400
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    category, tag = migrate_saved_filter(d.get("cat") or "All", d.get("tag"))
    payload = {
        "name": name[:80],
        "kw": (d.get("kw") or "").strip()[:120],
        "loc": (d.get("loc") or "").strip()[:120],
        "exp": (d.get("exp") or "").strip()[:12],
        "since": (d.get("since") or "").strip()[:20],
        "cat": category[:80] or "All",
        "tag": tag[:80],
        "email_alert_enabled": bool(d.get("email_alert_enabled")),
    }
    if payload["email_alert_enabled"]:
        profile_row = _current_user_profile()
        if not _has_paid_access():
            return jsonify({"error": "Email alerts require Pro.", "code": "pro_required"}), 402
        if not profile_row or not (profile_row["email"] or "").strip():
            return jsonify({"error": "Add an email address before enabling alerts.", "code": "email_required"}), 400
    with connect_auth_db(rows=True) as conn:
        conn.execute(
            "INSERT INTO user_saved_searches "
            "(user_id, name, kw, loc, exp_yrs, since, category, tag, email_alert_enabled, alert_cursor_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid, payload["name"], payload["kw"], payload["loc"],
                payload["exp"], payload["since"], payload["cat"], payload["tag"],
                int(payload["email_alert_enabled"]),
                _now_db() if payload["email_alert_enabled"] else "",
            ),
        )
        row = conn.execute(
            "SELECT id, name, kw, loc, exp_yrs, since, category, tag, email_alert_enabled FROM user_saved_searches "
            "WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (uid,),
        ).fetchone()
    return jsonify({
        "id": row["id"],
        "name": row["name"] or "",
        "kw": row["kw"] or "",
        "loc": row["loc"] or "",
        "exp": row["exp_yrs"] or "",
        "since": row["since"] or "",
        "cat": row["category"] or "All",
        "tag": row["tag"] or "",
        "email_alert_enabled": bool(row["email_alert_enabled"]),
    })

@app.route("/api/searches/<int:search_id>", methods=["PATCH"])
def update_saved_search(search_id):
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "Saved searches require a user account"}), 400
    d = request.get_json(silent=True) or {}
    if "email_alert_enabled" not in d:
        return jsonify({"error": "nothing to update"}), 400
    enabled = bool(d.get("email_alert_enabled"))
    if enabled:
        profile_row = _current_user_profile()
        if not _has_paid_access():
            return jsonify({"error": "Email alerts require Pro.", "code": "pro_required"}), 402
        if not profile_row or not (profile_row["email"] or "").strip():
            return jsonify({"error": "Add an email address before enabling alerts.", "code": "email_required"}), 400
    with connect_auth_db() as conn:
        conn.execute(
            "UPDATE user_saved_searches SET email_alert_enabled=?, "
            "alert_cursor_at=CASE WHEN ?=1 THEN ? ELSE alert_cursor_at END "
            "WHERE id=? AND user_id=?",
            (int(enabled), int(enabled), _now_db(), search_id, uid),
        )
    return jsonify({"ok": True, "email_alert_enabled": enabled})

@app.route("/api/searches/<int:search_id>", methods=["DELETE"])
def delete_saved_search(search_id):
    guard = _user_required()
    if guard:
        return guard
    uid = _current_user_id()
    if not uid:
        return jsonify({"error": "Saved searches require a user account"}), 400
    with connect_auth_db() as conn:
        conn.execute(
            "DELETE FROM user_saved_searches WHERE id=? AND user_id=?",
            (search_id, uid),
        )
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
        "timing":      scan_state.get("timing") or {},
    })

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

    updated = add_or_update_company(name, config, [])
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
    return jsonify(_admin_companies() if _is_admin() else _public_companies())

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
                        if re.match(r'^(?:[01]\d|2[0-3]):[0-5]\d$', str(t))})
        schedule[scan_type] = {"enabled": bool(cfg.get("enabled")), "times": times}
    _write_schedule_file(schedule)
    return jsonify({"ok": True, "schedule": schedule})

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
    company_configs = _admin_companies() if _is_admin() else _public_companies()
    allowed_company_names = {c.get("name") for c in company_configs}
    interview_map = {c["name"]: c.get("interview") for c in company_configs if c.get("interview")}
    with connect_db(rows=True) as conn:
        row = conn.execute(
            "SELECT id, title, url, company, location, source, posted, first_seen, "
            "exp_label, exp_min, exp_max, skills, keywords, categories, employment_type, description "
            "FROM jobs WHERE id = ?",
            (job_id,)
        ).fetchone()
    if not row:
        return jsonify({"error": "Job not found"}), 404
    if row["company"] not in allowed_company_names:
        return jsonify({"error": "Job not found"}), 404
    skills = json.loads(row["skills"] or "[]") if row["skills"] else []
    keywords = json.loads(row["keywords"] or "[]") if row["keywords"] else []
    if not keywords:
        keywords = extract_keywords(row["title"] or "", row["description"] or "", skills)
    cats   = json.loads(row["categories"] or "[]") if row["categories"] else []
    return jsonify({
        "id":              row["id"],
        "title":           row["title"] or "",
        "company":         row["company"] or "",
        "location":        display_location(row["location"] or "", row["title"] or "", row["url"] or ""),
        "source":          row["source"] or "",
        "posted":          (row["posted"] or "")[:10],
        "first_seen":      (row["first_seen"] or "")[:10],
        "url":             row["url"] or "",
        "exp_label":       row["exp_label"] or "",
        "employment_type": row["employment_type"] or "",
        "skills":          skills,
        "keywords":        keywords,
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
    category, tag = migrate_saved_filter(
        request.args.get("category") or "",
        request.args.get("tag") or "",
    )
    if category == "All":
        category = ""
    companies = [c.strip() for c in request.args.getlist("company") if c.strip()]
    if not companies:
        raw_companies = (request.args.get("companies") or "").strip()
        companies = [c.strip() for c in raw_companies.split(",") if c.strip()]
    requested_company_filter = bool(companies)
    all_company_names = {company.get("name") for company in current_companies() if company.get("name")}
    company_configs = _admin_companies() if _is_admin() else _public_companies()
    active_company_names = {company.get("name") for company in company_configs if company.get("name")}
    if tag:
        active_company_names &= {
            company.get("name")
            for company in company_configs
            if company.get("name") and tag in (company.get("tags") or [])
        }
    hidden_company_names = sorted(all_company_names - active_company_names)
    no_visible_company_match = False
    if companies:
        companies = [name for name in companies if name in active_company_names]
        no_visible_company_match = requested_company_filter and not companies
    try:
        limit = min(int(request.args.get("limit", 100) or 100), 5000)
    except (ValueError, TypeError):
        limit = 100
    try:
        page = max(int(request.args.get("page", 1) or 1), 1)
    except (ValueError, TypeError):
        page = 1
    offset   = (page - 1) * limit
    if no_visible_company_match:
        return jsonify({
            "jobs": [],
            "total": 0,
            "total_lower_bound": 0,
            "total_exact": True,
            "has_more": False,
            "page": page,
            "limit": limit,
        })

    kw_groups = keyword_groups(keyword)
    from_sql = "jobs"
    clauses, params = ["is_open = TRUE"], []
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
    elif hidden_company_names:
        hidden_marks = placeholders(len(hidden_company_names))
        clauses.append(f"company NOT IN ({hidden_marks})")
        params.extend(hidden_company_names)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    company_interviews = {
        company.get("name"): company.get("interview")
        for company in company_configs
        if company.get("interview")
    }
    select_cols = (
        "id, title, url, company, location, source, categories, first_seen, posted, "
        "exp_min, exp_max, exp_label, exp_source, skills, keywords, employment_type"
    )
    sql = (f"SELECT {select_cols} FROM {from_sql} {where} "
           f"ORDER BY {JOB_DATE_ORDER_SQL} LIMIT ? OFFSET ?")
    count_sql = f"SELECT COUNT(*) FROM {from_sql} {where}"
    query_params = list(params) + [limit, offset]
    exact_count = not (keyword or location or since or exp_years is not None or category or tag or companies)

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
                "keywords":         json.loads(r["keywords"] or "[]") or extract_keywords(r["title"] or "", "", json.loads(r["skills"] or "[]")),
                "employment_type":  r["employment_type"] or "",
                "interview":        company_interviews.get(r["company"]),
                "is_new":     fs == today,
            })
        if not jobs and total == 0:
            jobs, total = jobs_from_seen_fallback(keyword, location, since, category, companies, limit, offset, today)
            for job in jobs:
                job["location"] = display_location(
                    job.get("location", ""),
                    job.get("title", ""),
                    job.get("url", ""),
                )
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
