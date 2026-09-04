from flask import Blueprint, render_template, request, redirect, url_for

import db

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
def index():
    stats = db.dashboard_stats()
    running_job = stats["running_job"]
    running_segment = db.get_running_segment() if running_job else None

    oldest_job = stats["oldest_job"]
    oldest_running = bool(oldest_job) and bool(running_job) and oldest_job["id"] == running_job["id"]
    oldest_base_seconds = oldest_start_ts = oldest_duration_seconds = None
    if oldest_job:
        if oldest_running:
            oldest_base_seconds = db.closed_duration_seconds(oldest_job["id"])
            oldest_start_ts = running_segment["start_ts"]
        else:
            oldest_duration_seconds = db.total_duration_seconds(oldest_job["id"])

    return render_template(
        "dashboard.html",
        stats=stats,
        running_start_ts=running_segment["start_ts"] if running_segment else None,
        running_base_seconds=db.closed_duration_seconds(running_job["id"]) if running_job else None,
        oldest_running=oldest_running,
        oldest_base_seconds=oldest_base_seconds,
        oldest_start_ts=oldest_start_ts,
        oldest_duration_seconds=oldest_duration_seconds,
    )


def _clock_redirect_target():
    """Send the user back to whatever page they clocked in/out from (the
    clock widget lives in the header, so it's reachable from any page),
    guarding against an off-site redirect."""
    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for("dashboard.index")


@bp.route("/clock/in", methods=["POST"])
def clock_in():
    db.clock_in()
    return redirect(_clock_redirect_target())


@bp.route("/clock/out", methods=["POST"])
def clock_out():
    db.clock_out()
    return redirect(_clock_redirect_target())


@bp.route("/search")
def search():
    q = request.args.get("q", "").strip()
    scope = request.args.get("scope", "all")
    if scope not in ("all", "open", "resolved"):
        scope = "all"

    results = []
    if q:
        for job in db.search_jobs(q, scope):
            results.append({
                "job": job,
                "duration_seconds": db.total_duration_seconds(job["id"]),
                "matched_note": db.matching_note(job["id"], q),
            })

    return render_template("search_results.html", q=q, scope=scope, results=results)
