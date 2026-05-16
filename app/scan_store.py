"""Persistent scan run and per-company status storage."""
from datetime import datetime

try:
    from .api.database import IS_POSTGRES, connect_db
except ImportError:
    from api.database import IS_POSTGRES, connect_db


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_scan_run(scan_type, total_companies):
    with connect_db() as conn:
        if IS_POSTGRES:
            row = conn.execute(
                "INSERT INTO scan_runs (scan_type, total_companies) VALUES (?, ?) RETURNING id",
                (scan_type, total_companies),
            ).fetchone()
            return row[0]
        cur = conn.execute(
            "INSERT INTO scan_runs (scan_type, total_companies, started_at) VALUES (?, ?, ?)",
            (scan_type, total_companies, _now()),
        )
        return cur.lastrowid


def finish_scan_run(scan_run_id, status, total_jobs_found=0, new_jobs_found=0,
                    updated_jobs_found=0, failed_companies=0, error_message=None):
    with connect_db() as conn:
        conn.execute(
            "UPDATE scan_runs SET status=?, finished_at=?, total_jobs_found=?, "
            "new_jobs_found=?, updated_jobs_found=?, failed_companies=?, error_message=? "
            "WHERE id=?",
            (
                status, _now(), total_jobs_found, new_jobs_found,
                updated_jobs_found, failed_companies, error_message, scan_run_id,
            ),
        )


def update_scan_counts(scan_run_id, companies_scanned):
    with connect_db() as conn:
        conn.execute(
            "UPDATE scan_runs SET companies_scanned=? WHERE id=?",
            (companies_scanned, scan_run_id),
        )


def create_company_scan_status(scan_run_id, company_name):
    with connect_db() as conn:
        if IS_POSTGRES:
            row = conn.execute(
                "INSERT INTO scan_company_status "
                "(scan_run_id, company_name, status, started_at) "
                "VALUES (?, ?, 'scanning', ?) RETURNING id",
                (scan_run_id, company_name, _now()),
            ).fetchone()
            return row[0]
        cur = conn.execute(
            "INSERT INTO scan_company_status "
            "(scan_run_id, company_name, status, started_at) VALUES (?, ?, 'scanning', ?)",
            (scan_run_id, company_name, _now()),
        )
        return cur.lastrowid


def finish_company_scan_status(status_id, status, jobs_found=0, new_jobs_found=0,
                               error_message=None):
    with connect_db() as conn:
        conn.execute(
            "UPDATE scan_company_status SET status=?, jobs_found=?, new_jobs_found=?, "
            "finished_at=?, error_message=? WHERE id=?",
            (status, jobs_found, new_jobs_found, _now(), error_message, status_id),
        )


def latest_scan():
    with connect_db(rows=True) as conn:
        return conn.execute(
            "SELECT * FROM scan_runs ORDER BY started_at DESC, id DESC LIMIT 1"
        ).fetchone()


def scan_companies(scan_run_id):
    with connect_db(rows=True) as conn:
        return conn.execute(
            "SELECT * FROM scan_company_status WHERE scan_run_id=? "
            "ORDER BY started_at ASC, id ASC",
            (scan_run_id,),
        ).fetchall()
