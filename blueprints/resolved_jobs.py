from flask import Blueprint, render_template, redirect, url_for

import db

bp = Blueprint("resolved_jobs", __name__)


@bp.route("/resolved")
def index():
    jobs = db.list_jobs("resolved")
    rows = [
        {"job": job, "duration_seconds": db.total_duration_seconds(job["id"])}
        for job in jobs
    ]
    return render_template("resolved_jobs.html", rows=rows)


@bp.route("/job/<int:job_id>/reopen", methods=["POST"])
def reopen(job_id):
    db.reopen_job(job_id)
    return redirect(url_for("active_jobs.index"))
