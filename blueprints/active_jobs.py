import re
from html import unescape

from flask import Blueprint, render_template, request, redirect, url_for, abort

import db

bp = Blueprint("active_jobs", __name__)


def _html_to_text(html):
    """Flatten the Quill description editor's HTML into plain text for the
    clipboard copy pack."""
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>|</li>|</blockquote>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _redirect_target(job_id):
    """Redirect to a caller-supplied 'next' path if it's a safe same-site
    relative URL, otherwise fall back to the job detail page."""
    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for("active_jobs.detail", job_id=job_id)


@bp.route("/active")
def index():
    jobs = db.list_jobs("open")
    running_job_id = db.get_running_job_id()
    running_segment = db.get_running_segment() if running_job_id else None

    rows = []
    for job in jobs:
        running = job["id"] == running_job_id
        rows.append({
            "job": job,
            "color": db.color_for_job(job["id"]),
            "running": running,
            "base_seconds": db.closed_duration_seconds(job["id"]) if running else None,
            "duration_seconds": None if running else db.total_duration_seconds(job["id"]),
            "start_ts": running_segment["start_ts"] if running else None,
        })

    return render_template("active_jobs.html", rows=rows)


@bp.route("/job/new", methods=["POST"])
def new_job():
    job_number = request.form.get("job_number", "").strip()
    customer_name = request.form.get("customer_name", "").strip()
    summary = request.form.get("summary", "").strip()
    description = request.form.get("description", "").strip()
    if not (job_number or customer_name or summary or description):
        abort(400, "Enter at least one field.")
    job_id = db.create_job(job_number, customer_name, summary, description)
    return redirect(url_for("active_jobs.detail", job_id=job_id))


@bp.route("/job/<int:job_id>")
def detail(job_id):
    job = db.get_job(job_id)
    if job is None:
        abort(404)
    running_job_id = db.get_running_job_id()
    running = job_id == running_job_id
    running_segment = db.get_running_segment() if running else None

    segments = []
    for s in reversed(db.list_segments(job_id)):
        duration = None
        if s["end_ts"]:
            duration = (db.parse_ts(s["end_ts"]) - db.parse_ts(s["start_ts"])).total_seconds()
        segments.append({"row": s, "duration": duration})

    notes = db.list_notes(job_id)

    copy_lines = []
    if job["job_number"]:
        copy_lines.append(f"Job #: {job['job_number']}")
    if job["customer_name"]:
        copy_lines.append(f"Customer: {job['customer_name']}")
    if job["summary"]:
        copy_lines.append(f"Summary: {job['summary']}")
    if job["description"]:
        copy_lines.append(f"Description:\n{_html_to_text(job['description'])}")
    if notes:
        copy_lines.append("Notes:")
        for n in reversed(notes):
            copy_lines.append(f"- [{db.format_ts(n['created_at'])}] {n['text']}")
    copy_text = "\n".join(copy_lines)

    return render_template(
        "job_detail.html",
        job=job,
        running=running,
        base_seconds=db.closed_duration_seconds(job_id) if running else None,
        duration_seconds=None if running else db.total_duration_seconds(job_id),
        start_ts=running_segment["start_ts"] if running else None,
        notes=notes,
        segments=segments,
        color=db.color_for_job(job_id) if job["status"] == "open" else None,
        copy_text=copy_text,
    )


@bp.route("/job/<int:job_id>/update", methods=["POST"])
def update(job_id):
    job = db.get_job(job_id)
    if job is None:
        abort(404)
    job_number = request.form.get("job_number", "").strip()
    customer_name = request.form.get("customer_name", "").strip()
    summary = request.form.get("summary", "").strip()
    description = request.form.get("description", "").strip()
    if job_number or customer_name or summary or description:
        db.update_job(job_id, job_number, customer_name, summary, description)
    return redirect(url_for("active_jobs.detail", job_id=job_id))


@bp.route("/job/<int:job_id>/start", methods=["POST"])
def start(job_id):
    job = db.get_job(job_id)
    if job is None:
        abort(404)
    if job["status"] != "open":
        abort(400, "Cannot start the timer on a resolved job.")
    db.start_timer(job_id)
    return redirect(_redirect_target(job_id))


@bp.route("/job/<int:job_id>/stop", methods=["POST"])
def stop(job_id):
    db.stop_timer(job_id)
    return redirect(_redirect_target(job_id))


@bp.route("/job/<int:job_id>/resolve", methods=["POST"])
def resolve(job_id):
    db.resolve_job(job_id)
    return redirect(url_for("resolved_jobs.index"))


@bp.route("/job/<int:job_id>/note", methods=["POST"])
def note(job_id):
    text = request.form.get("text", "").strip()
    if text:
        db.add_note(job_id, text)
    return redirect(url_for("active_jobs.detail", job_id=job_id))


@bp.route("/job/<int:job_id>/note/<int:note_id>/delete", methods=["POST"])
def delete_note(job_id, note_id):
    db.delete_note(note_id)
    return redirect(url_for("active_jobs.detail", job_id=job_id))


@bp.route("/job/<int:job_id>/delete", methods=["POST"])
def delete(job_id):
    job = db.get_job(job_id)
    if job is None:
        abort(404)
    status = job["status"]
    db.delete_job(job_id)
    next_url = request.form.get("next", "")
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("resolved_jobs.index" if status == "resolved" else "active_jobs.index"))


@bp.route("/job/<int:job_id>/manual-time", methods=["POST"])
def manual_time(job_id):
    try:
        hours = int(request.form.get("hours") or 0)
        minutes = int(request.form.get("minutes") or 0)
    except ValueError:
        hours = minutes = 0
    if hours or minutes:
        db.add_manual_time(job_id, hours, minutes)
    return redirect(url_for("active_jobs.detail", job_id=job_id))
