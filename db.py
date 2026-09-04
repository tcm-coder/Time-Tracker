import logging
import os
import sys
import sqlite3
from pathlib import Path
from datetime import date, datetime, time, timedelta

import bleach
from bleach.css_sanitizer import CSSSanitizer
from flask import g

logger = logging.getLogger(__name__)

# When frozen by PyInstaller, __file__ resolves inside the bundle's extracted
# temp/internal directory -- fine for read-only resources like schema.sql,
# but a database placed there wouldn't survive between runs. The db instead
# lives next to the .exe itself, so each colleague's data persists and stays
# separate from whatever machine built the bundle.
if getattr(sys, "frozen", False):
    _BUNDLE_DIR = Path(sys._MEIPASS)
    _APP_DIR = Path(sys.executable).parent
else:
    _BUNDLE_DIR = Path(__file__).parent
    _APP_DIR = Path(__file__).parent

# Server/dev deployments can point the database at a directory outside the
# repo (e.g. so it never ends up in git) via TIMETRACKER_DB_DIR -- set in
# the systemd unit for this deployment. Unset for the desktop PyInstaller
# build, which keeps storing its database next to the .exe as before.
_DB_DIR_OVERRIDE = os.environ.get("TIMETRACKER_DB_DIR")
_DB_DIR = Path(_DB_DIR_OVERRIDE) if _DB_DIR_OVERRIDE else _APP_DIR
_DB_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = _DB_DIR / "timetracker.db"
SCHEMA_PATH = _BUNDLE_DIR / "schema.sql"

# Formatting the Quill editor's toolbar is restricted to (bold, italic,
# underline, strike, lists, blockquote, highlight color, tables) maps to
# this tag/attribute allowlist. Anything else pasted or injected is stripped
# here before it ever reaches the database.
#
# Table tags/attrs match what the quill-table-up module actually emits (verified
# against its real output, not guessed): a table is wrapped in a plain <div>,
# column widths live on <col width="..."> (a plain attribute, not CSS), and
# each cell's content sits in a nested <div> before the <p>. quill-table-up
# regenerates its own data-table-id/data-row-id/contenteditable etc. from
# this structure on load, so none of those need to survive sanitization.
_DESCRIPTION_TAGS = [
    "p", "br", "strong", "b", "em", "i", "u", "s", "strike",
    "ul", "ol", "li", "blockquote", "span",
    "div", "table", "colgroup", "col", "thead", "tbody", "tfoot", "tr", "td", "th",
]
_DESCRIPTION_ATTRS = {
    "span": ["style"],
    "strong": ["style"],
    "b": ["style"],
    "em": ["style"],
    "i": ["style"],
    "u": ["style"],
    "s": ["style"],
    "strike": ["style"],
    "div": ["class"],
    # Quill 2.x (unlike 1.x) represents both bullet and ordered lists as <ol>,
    # distinguished only by data-list -- without it a saved bullet list
    # silently becomes indistinguishable from a numbered one on reload.
    "ol": ["data-list"],
    "li": ["data-list"],
    "table": ["class", "style", "cellpadding", "cellspacing"],
    "col": ["width"],
    "tr": ["class"],
    "td": ["class", "colspan", "rowspan", "style"],
    "th": ["class", "colspan", "rowspan", "style"],
}
# Excel's paste HTML drives cell appearance from a generic `td {...}` rule
# resolved to inline styles (see pasteStyleSheet above), which touches far
# more properties than the editor's own toolbar does. All of these are
# presentational only -- deliberately no url()-capable property (background-image,
# border-image, list-style-image, cursor) is allowed, since bleach's CSS
# sanitizer parses values but doesn't vet URL schemes inside them.
#
# font-family is deliberately excluded: bleach/html5lib mishandles the
# &quot;-escaped quotes a quoted font stack (e.g. "Aptos Narrow", sans-serif)
# produces inside a style attribute, corrupting whatever CSS declaration
# comes right after it -- confirmed against real Excel paste output. Not
# worth carrying Excel's per-cell font choice at the cost of that.
_DESCRIPTION_CSS = CSSSanitizer(allowed_css_properties=[
    "background-color", "background", "color",
    "font-weight", "font-style", "font-size",
    "text-align", "text-decoration", "vertical-align", "white-space",
    "width", "height",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "border-width", "border-style", "border-color", "border-collapse",
])


def sanitize_description(html):
    return bleach.clean(
        html or "",
        tags=_DESCRIPTION_TAGS,
        attributes=_DESCRIPTION_ATTRS,
        css_sanitizer=_DESCRIPTION_CSS,
        strip=True,
    )


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, "r") as f:
        conn.executescript(f.read())
    _migrate(conn)
    conn.commit()
    conn.close()


def _migrate(conn):
    """Add columns to pre-existing databases created before schema.sql grew them."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()]
    if "description" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN description TEXT NOT NULL DEFAULT ''")


def init_app(app):
    app.teardown_appcontext(close_db)
    init_db()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def parse_ts(ts):
    return datetime.fromisoformat(ts)


def format_ts(ts):
    return parse_ts(ts).strftime("%Y-%m-%d %I:%M %p")


def format_duration(seconds):
    seconds = int(seconds)
    if seconds < 0:
        logger.warning(
            "format_duration got a negative duration (%ds) -- end time is before start time",
            seconds,
        )
        return "invalid"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


# ---------------------------------------------------------------------------
# Timer / segment logic. Invariant: at most one time_segments row has
# end_ts IS NULL at any moment (the single global clock).
# ---------------------------------------------------------------------------

def stop_all_running_segments():
    db = get_db()
    db.execute("UPDATE time_segments SET end_ts = ? WHERE end_ts IS NULL", (now_iso(),))
    db.commit()


def get_running_segment():
    db = get_db()
    return db.execute("SELECT * FROM time_segments WHERE end_ts IS NULL LIMIT 1").fetchone()


def get_running_job_id():
    row = get_running_segment()
    return row["job_id"] if row else None


def start_timer(job_id):
    stop_all_running_segments()
    db = get_db()
    db.execute(
        "INSERT INTO time_segments (job_id, start_ts, source) VALUES (?, ?, 'timer')",
        (job_id, now_iso()),
    )
    db.commit()


resume_timer = start_timer


def stop_timer(job_id):
    db = get_db()
    db.execute(
        "UPDATE time_segments SET end_ts = ? WHERE job_id = ? AND end_ts IS NULL",
        (now_iso(), job_id),
    )
    db.commit()


def add_manual_time(job_id, hours, minutes):
    db = get_db()
    delta = timedelta(hours=hours or 0, minutes=minutes or 0)
    end = datetime.now()
    start = end - delta
    db.execute(
        "INSERT INTO time_segments (job_id, start_ts, end_ts, source) VALUES (?, ?, ?, 'manual')",
        (job_id, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds")),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(job_number, customer_name, summary, description=""):
    db = get_db()
    cur = db.execute(
        "INSERT INTO jobs (job_number, customer_name, summary, description, status, created_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        (job_number, customer_name, summary, sanitize_description(description), now_iso()),
    )
    db.commit()
    job_id = cur.lastrowid
    start_timer(job_id)
    return job_id


def update_job(job_id, job_number, customer_name, summary, description):
    db = get_db()
    db.execute(
        "UPDATE jobs SET job_number = ?, customer_name = ?, summary = ?, description = ? WHERE id = ?",
        (job_number, customer_name, summary, sanitize_description(description), job_id),
    )
    db.commit()


def resolve_job(job_id):
    stop_timer(job_id)
    db = get_db()
    db.execute(
        "UPDATE jobs SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (now_iso(), job_id),
    )
    db.commit()


def reopen_job(job_id):
    db = get_db()
    db.execute("UPDATE jobs SET status = 'open', resolved_at = NULL WHERE id = ?", (job_id,))
    db.commit()


def delete_job(job_id):
    """Stop any running timer on this job, then remove it and everything
    tied to it (time segments, notes) — a hard, unrecoverable delete."""
    stop_timer(job_id)
    db = get_db()
    db.execute("DELETE FROM time_segments WHERE job_id = ?", (job_id,))
    db.execute("DELETE FROM notes WHERE job_id = ?", (job_id,))
    db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    db.commit()


def get_job(job_id):
    db = get_db()
    return db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def list_jobs(status):
    db = get_db()
    return db.execute(
        "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC", (status,)
    ).fetchall()


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def add_note(job_id, text):
    db = get_db()
    db.execute(
        "INSERT INTO notes (job_id, text, created_at) VALUES (?, ?, ?)",
        (job_id, text, now_iso()),
    )
    db.commit()


def list_notes(job_id):
    db = get_db()
    return db.execute(
        "SELECT * FROM notes WHERE job_id = ? ORDER BY created_at DESC", (job_id,)
    ).fetchall()


def delete_note(note_id):
    db = get_db()
    db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_jobs(query, scope="all"):
    """Search job number, customer, summary and note text. scope is 'all',
    'open' or 'resolved'."""
    db = get_db()
    like = f"%{query}%"
    sql = (
        "SELECT DISTINCT j.* FROM jobs j "
        "LEFT JOIN notes n ON n.job_id = j.id "
        "WHERE (j.job_number LIKE ? OR j.customer_name LIKE ? OR j.summary LIKE ? "
        "OR j.description LIKE ? OR n.text LIKE ?)"
    )
    params = [like, like, like, like, like]
    if scope in ("open", "resolved"):
        sql += " AND j.status = ?"
        params.append(scope)
    sql += " ORDER BY j.created_at DESC"
    return db.execute(sql, params).fetchall()


def matching_note(job_id, query):
    """First note on job_id whose text matches query, for showing a snippet
    in search results."""
    db = get_db()
    like = f"%{query}%"
    return db.execute(
        "SELECT * FROM notes WHERE job_id = ? AND text LIKE ? ORDER BY created_at DESC LIMIT 1",
        (job_id, like),
    ).fetchone()


# ---------------------------------------------------------------------------
# Segments / durations / staleness color
# ---------------------------------------------------------------------------

def list_segments(job_id):
    db = get_db()
    return db.execute(
        "SELECT * FROM time_segments WHERE job_id = ? ORDER BY start_ts ASC", (job_id,)
    ).fetchall()


def total_duration_seconds(job_id):
    total = 0.0
    now = datetime.now()
    for seg in list_segments(job_id):
        start = parse_ts(seg["start_ts"])
        end = parse_ts(seg["end_ts"]) if seg["end_ts"] else now
        total += (end - start).total_seconds()
    return total


def closed_duration_seconds(job_id):
    """Sum of only the already-closed segments (excludes a currently-running one)."""
    total = 0.0
    for seg in list_segments(job_id):
        if seg["end_ts"]:
            start = parse_ts(seg["start_ts"])
            end = parse_ts(seg["end_ts"])
            total += (end - start).total_seconds()
    return total


def last_activity_ts(job_id):
    job = get_job(job_id)
    timestamps = [job["created_at"]]
    for n in list_notes(job_id):
        timestamps.append(n["created_at"])
    for s in list_segments(job_id):
        timestamps.append(s["start_ts"])
        if s["end_ts"]:
            timestamps.append(s["end_ts"])
    return max(parse_ts(t) for t in timestamps)


def color_for_job(job_id):
    last = last_activity_ts(job_id)
    days = (datetime.now() - last).total_seconds() / 86400
    if days < 3:
        return "green"
    if days <= 5:
        return "orange"
    return "red"


# ---------------------------------------------------------------------------
# Timesheet / dashboard aggregate queries
# ---------------------------------------------------------------------------

def list_all_segments(start_date=None, end_date=None):
    db = get_db()
    query = (
        "SELECT ts.*, j.job_number, j.customer_name, j.summary, j.status "
        "FROM time_segments ts JOIN jobs j ON j.id = ts.job_id WHERE 1=1"
    )
    params = []
    if start_date:
        query += " AND date(ts.start_ts) >= date(?)"
        params.append(start_date)
    if end_date:
        query += " AND date(ts.start_ts) <= date(?)"
        params.append(end_date)
    query += " ORDER BY ts.start_ts DESC"
    return db.execute(query, params).fetchall()


def dashboard_stats():
    db = get_db()
    now = datetime.now()
    today = now.date()
    week_start = today - timedelta(days=today.weekday())

    def sum_since(since_date):
        total = 0.0
        floor = datetime.combine(since_date, datetime.min.time())
        for seg in db.execute("SELECT start_ts, end_ts FROM time_segments").fetchall():
            start = parse_ts(seg["start_ts"])
            end = parse_ts(seg["end_ts"]) if seg["end_ts"] else now
            clip_start = max(start, floor)
            if end <= clip_start:
                continue
            total += (end - clip_start).total_seconds()
        return total

    hours_today = sum_since(today) / 3600
    hours_week = sum_since(week_start) / 3600

    open_count = db.execute("SELECT COUNT(*) c FROM jobs WHERE status='open'").fetchone()["c"]
    resolved_count = db.execute("SELECT COUNT(*) c FROM jobs WHERE status='resolved'").fetchone()["c"]

    running_job_id = get_running_job_id()
    running_job = get_job(running_job_id) if running_job_id else None

    open_jobs = list_jobs("open")
    oldest_job = None
    if open_jobs:
        oldest_job = min(open_jobs, key=lambda j: last_activity_ts(j["id"]))

    return {
        "hours_today": hours_today,
        "hours_week": hours_week,
        "open_count": open_count,
        "resolved_count": resolved_count,
        "running_job": running_job,
        "oldest_job": oldest_job,
    }


# ---------------------------------------------------------------------------
# Timesheet weekly grid
# ---------------------------------------------------------------------------

def week_bounds(ref_date):
    """Return (monday, sunday) date objects for the week containing ref_date."""
    start = ref_date - timedelta(days=ref_date.weekday())
    end = start + timedelta(days=6)
    return start, end


def timesheet_grid(week_start):
    """Build a Mon-Sun grid of hours per job for the week starting week_start (a date)."""
    week_end = week_start + timedelta(days=6)
    days = [week_start + timedelta(days=i) for i in range(7)]

    conn = get_db()
    segments = conn.execute(
        "SELECT ts.*, j.job_number, j.customer_name, j.status, j.resolved_at "
        "FROM time_segments ts JOIN jobs j ON j.id = ts.job_id "
        "WHERE date(ts.start_ts) <= date(?) AND (ts.end_ts IS NULL OR date(ts.end_ts) >= date(?))",
        (week_end.isoformat(), week_start.isoformat()),
    ).fetchall()

    now = datetime.now()
    jobs = {}
    for seg in segments:
        job_id = seg["job_id"]
        entry = jobs.setdefault(job_id, {
            "job_id": job_id,
            "job_number": seg["job_number"],
            "customer_name": seg["customer_name"],
            "status": seg["status"],
            "resolved_at": seg["resolved_at"],
            "day_seconds": {d.isoformat(): 0.0 for d in days},
        })
        seg_start = parse_ts(seg["start_ts"])
        seg_end = parse_ts(seg["end_ts"]) if seg["end_ts"] else now
        for d in days:
            day_start = datetime.combine(d, datetime.min.time())
            day_end = day_start + timedelta(days=1)
            overlap_start = max(seg_start, day_start)
            overlap_end = min(seg_end, day_end)
            if overlap_end > overlap_start:
                entry["day_seconds"][d.isoformat()] += (overlap_end - overlap_start).total_seconds()

    for entry in jobs.values():
        entry["total_seconds"] = sum(entry["day_seconds"].values())

    rows = sorted(jobs.values(), key=lambda j: (j["status"] != "open", j["job_number"]))
    day_totals = {d.isoformat(): sum(r["day_seconds"][d.isoformat()] for r in rows) for d in days}
    week_total = sum(day_totals.values())

    return {
        "days": days,
        "rows": rows,
        "day_totals": day_totals,
        "week_total": week_total,
    }


# ---------------------------------------------------------------------------
# Independent day clock (clock in / pause for lunch / resume). Entirely
# separate from job time_segments and its single-running-timer invariant.
# ---------------------------------------------------------------------------

# A running clock segment auto-closes at this hour (24h clock) if nobody
# clocks out manually first. One line to change if the schedule shifts.
CLOCK_CUTOFF_HOUR = 19


def enforce_clock_cutoff():
    """Force-close a running clock segment that has crossed the cutoff hour
    or spans past midnight — a segment must never cover more than one
    calendar day. Cheap enough to call on every request; no background job
    needed since there's at most one running segment ever."""
    seg = clock_running_segment()
    if seg is None:
        return
    start = parse_ts(seg["start_ts"])
    now = datetime.now()
    if now.date() > start.date():
        close_at = datetime.combine(start.date(), time(23, 59, 59))
    else:
        # If the clock-in itself happened at or after the cutoff hour (e.g.
        # an evening/night shift), the same-day cutoff would fall before
        # start_ts -- push it to the cutoff hour on the following day so
        # close_at is never before start_ts. (In practice the day-rollover
        # branch above will close the segment at 23:59:59 long before this
        # next-day cutoff is ever reached, which is fine -- it just means
        # this branch never force-closes an evening shift mid-evening.)
        cutoff_date = start.date() + timedelta(days=1) if start.hour >= CLOCK_CUTOFF_HOUR else start.date()
        cutoff_dt = datetime.combine(cutoff_date, time(CLOCK_CUTOFF_HOUR, 0, 0))
        if now < cutoff_dt:
            return
        close_at = cutoff_dt
    db = get_db()
    db.execute(
        "UPDATE clock_segments SET end_ts = ? WHERE id = ?",
        (close_at.isoformat(timespec="seconds"), seg["id"]),
    )
    db.commit()


def clock_running_segment():
    db = get_db()
    return db.execute("SELECT * FROM clock_segments WHERE end_ts IS NULL LIMIT 1").fetchone()


def clock_in():
    if clock_running_segment():
        return
    db = get_db()
    db.execute("INSERT INTO clock_segments (start_ts) VALUES (?)", (now_iso(),))
    db.commit()


def clock_out():
    db = get_db()
    db.execute("UPDATE clock_segments SET end_ts = ? WHERE end_ts IS NULL", (now_iso(),))
    db.commit()


def _clock_seconds_today(closed_only):
    db = get_db()
    floor = datetime.combine(date.today(), datetime.min.time())
    now = datetime.now()
    query = "SELECT start_ts, end_ts FROM clock_segments"
    if closed_only:
        query += " WHERE end_ts IS NOT NULL"
    total = 0.0
    for seg in db.execute(query).fetchall():
        start = parse_ts(seg["start_ts"])
        end = parse_ts(seg["end_ts"]) if seg["end_ts"] else now
        clip_start = max(start, floor)
        if end <= clip_start:
            continue
        total += (end - clip_start).total_seconds()
    return total


def clock_closed_seconds_today():
    """Today's clocked time from already-closed segments only (excludes a
    currently running one) — used as the base for the live client ticker."""
    return _clock_seconds_today(closed_only=True)


def clock_total_seconds_today():
    """Today's total clocked time including any currently running segment."""
    return _clock_seconds_today(closed_only=False)


def clock_day_started():
    """Whether any clock segment (running or already closed) started today —
    distinguishes 'paused for lunch' from 'never clocked in today'."""
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM clock_segments WHERE date(start_ts) = date(?) LIMIT 1",
        (now_iso(),),
    ).fetchone()
    return row is not None


def clock_week_summary(week_start):
    """Per-day first-clock-in / last-clock-out for the week starting
    week_start. Day boundary is midnight: a segment only ever counts toward
    the day it started on. A segment that's still open, or that closes on a
    later calendar day, shows as open-ended for *that* day only — it never
    creates a phantom row on the following day."""
    days = [week_start + timedelta(days=i) for i in range(7)]
    db = get_db()
    result = {}
    for d in days:
        d_iso = d.isoformat()
        segments = db.execute(
            "SELECT start_ts, end_ts FROM clock_segments WHERE date(start_ts) = ? ORDER BY start_ts ASC",
            (d_iso,),
        ).fetchall()
        if not segments:
            result[d_iso] = None
            continue

        start_ts = segments[0]["start_ts"]
        last = segments[-1]
        if last["end_ts"] is None:
            status, end_ts = "in_progress", None
        elif parse_ts(last["end_ts"]).date().isoformat() == d_iso:
            status, end_ts = "closed", last["end_ts"]
        else:
            status, end_ts = "open_ended", None
        result[d_iso] = {"start_ts": start_ts, "end_ts": end_ts, "status": status}
    return result


def format_time_only(ts):
    return parse_ts(ts).strftime("%I:%M %p").lstrip("0")


def format_time_input(ts):
    """HH:MM (24h), the value format required by <input type="time">."""
    return parse_ts(ts).strftime("%H:%M")


def combine_day_time(day, time_str):
    """Combine a date with an HH:MM string from a time input into an ISO
    timestamp string on that day."""
    hh, mm = time_str.split(":")
    return datetime.combine(day, time(int(hh), int(mm))).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Day clock segment management (manual edit/delete/add for a single day) —
# fixes for a wrong auto-close or a forgotten clock-in/out.
# ---------------------------------------------------------------------------

def list_clock_segments_for_day(day):
    db = get_db()
    return db.execute(
        "SELECT * FROM clock_segments WHERE date(start_ts) = ? ORDER BY start_ts ASC",
        (day.isoformat(),),
    ).fetchall()


def get_clock_segment(segment_id):
    db = get_db()
    return db.execute("SELECT * FROM clock_segments WHERE id = ?", (segment_id,)).fetchone()


def add_clock_segment(start_ts, end_ts):
    db = get_db()
    db.execute(
        "INSERT INTO clock_segments (start_ts, end_ts) VALUES (?, ?)",
        (start_ts, end_ts),
    )
    db.commit()


def update_clock_segment(segment_id, start_ts, end_ts):
    db = get_db()
    db.execute(
        "UPDATE clock_segments SET start_ts = ?, end_ts = ? WHERE id = ?",
        (start_ts, end_ts, segment_id),
    )
    db.commit()


def delete_clock_segment(segment_id):
    db = get_db()
    db.execute("DELETE FROM clock_segments WHERE id = ?", (segment_id,))
    db.commit()
