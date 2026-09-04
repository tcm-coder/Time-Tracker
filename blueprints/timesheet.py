import csv
import io
from datetime import date, timedelta

from flask import Blueprint, render_template, request, Response, redirect, url_for, abort

import db

bp = Blueprint("timesheet", __name__)


def _resolve_week_start():
    week_param = request.args.get("week")
    if week_param:
        try:
            ref_date = date.fromisoformat(week_param)
        except ValueError:
            ref_date = date.today()
    else:
        ref_date = date.today()
    week_start, _ = db.week_bounds(ref_date)
    return week_start


@bp.route("/timesheet")
def index():
    week_start = _resolve_week_start()
    week_end = week_start + timedelta(days=6)
    grid = db.timesheet_grid(week_start)
    clock_summary = db.clock_week_summary(week_start)

    return render_template(
        "timesheet.html",
        grid=grid,
        clock_summary=clock_summary,
        week_start=week_start,
        week_end=week_end,
        prev_week=(week_start - timedelta(days=7)).isoformat(),
        next_week=(week_start + timedelta(days=7)).isoformat(),
        today=date.today(),
    )


def _parse_iso_date(iso_date):
    try:
        return date.fromisoformat(iso_date)
    except ValueError:
        abort(404)


@bp.route("/timesheet/clock/<iso_date>")
def clock_day(iso_date):
    day = _parse_iso_date(iso_date)
    now = db.parse_ts(db.now_iso())
    rows = []
    for seg in db.list_clock_segments_for_day(day):
        duration = None
        if seg["end_ts"]:
            duration = (db.parse_ts(seg["end_ts"]) - db.parse_ts(seg["start_ts"])).total_seconds()
        rows.append({"row": seg, "duration": duration})

    return render_template(
        "clock_day.html",
        day=day,
        rows=rows,
        prev_day=(day - timedelta(days=1)).isoformat(),
        next_day=(day + timedelta(days=1)).isoformat(),
        week_start=db.week_bounds(day)[0].isoformat(),
    )


@bp.route("/timesheet/clock/<iso_date>/add", methods=["POST"])
def add_clock_segment(iso_date):
    day = _parse_iso_date(iso_date)
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    if start_time:
        start_ts = db.combine_day_time(day, start_time)
        end_ts = db.combine_day_time(day, end_time) if end_time else None
        if not end_ts or end_ts > start_ts:
            db.add_clock_segment(start_ts, end_ts)
    return redirect(url_for("timesheet.clock_day", iso_date=iso_date))


@bp.route("/timesheet/clock/segment/<int:segment_id>/update", methods=["POST"])
def update_clock_segment(segment_id):
    seg = db.get_clock_segment(segment_id)
    if seg is None:
        abort(404)
    day = db.parse_ts(seg["start_ts"]).date()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    if start_time:
        start_ts = db.combine_day_time(day, start_time)
        end_ts = db.combine_day_time(day, end_time) if end_time else None
        if not end_ts or end_ts > start_ts:
            db.update_clock_segment(segment_id, start_ts, end_ts)
    return redirect(url_for("timesheet.clock_day", iso_date=day.isoformat()))


@bp.route("/timesheet/clock/segment/<int:segment_id>/delete", methods=["POST"])
def delete_clock_segment(segment_id):
    seg = db.get_clock_segment(segment_id)
    if seg is None:
        abort(404)
    day = db.parse_ts(seg["start_ts"]).date()
    db.delete_clock_segment(segment_id)
    return redirect(url_for("timesheet.clock_day", iso_date=day.isoformat()))


@bp.route("/timesheet/export.csv")
def export_csv():
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None
    raw_segments = db.list_all_segments(start_date, end_date)
    now = db.parse_ts(db.now_iso())

    job_order = []
    job_totals = {}
    for s in raw_segments:
        end = db.parse_ts(s["end_ts"]) if s["end_ts"] else now
        duration_hours = (end - db.parse_ts(s["start_ts"])).total_seconds() / 3600
        job_id = s["job_id"]
        if job_id not in job_totals:
            job_totals[job_id] = {
                "job_number": s["job_number"],
                "customer_name": s["customer_name"],
                "summary": s["summary"],
                "status": s["status"],
                "hours": 0.0,
            }
            job_order.append(job_id)
        job_totals[job_id]["hours"] += duration_hours

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Job Number", "Customer", "Summary", "Status", "Duration (hours)"])
    for job_id in job_order:
        t = job_totals[job_id]
        writer.writerow(
            [t["job_number"], t["customer_name"], t["summary"], t["status"], f"{t['hours']:.2f}"]
        )

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=timesheet_export.csv"
    return response
