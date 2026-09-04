import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from flask import Flask, abort, redirect, url_for, render_template, jsonify

import db
import version
from blueprints.active_jobs import bp as active_jobs_bp
from blueprints.resolved_jobs import bp as resolved_jobs_bp
from blueprints.dashboard import bp as dashboard_bp
from blueprints.timesheet import bp as timesheet_bp

LOCK_PORT = 47391  # arbitrary local-only port used purely as a single-instance mutex

# Set by the systemd unit (Environment="IS_SERVER=1") for the persistent
# server deployment. Unset for the desktop PyInstaller build, which keeps
# the original quit-the-app-window behavior.
IS_SERVER = os.environ.get("IS_SERVER") == "1"

mdns_state = {}

# Flask's own __name__-based root_path guessing is unreliable once frozen
# (the main module's __file__ points somewhere inside PyInstaller's bundle,
# not necessarily where it resolves templates/ and static/ from), so point
# it at the bundle directory explicitly.
_BUNDLE_DIR = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent


def create_app():
    app = Flask(
        __name__,
        template_folder=str(_BUNDLE_DIR / "templates"),
        static_folder=str(_BUNDLE_DIR / "static"),
    )
    db.init_app(app)

    app.jinja_env.filters["fmtdur"] = db.format_duration
    app.jinja_env.filters["fmtts"] = db.format_ts
    app.jinja_env.filters["fmttime"] = db.format_time_only
    app.jinja_env.filters["fmttime24"] = db.format_time_input

    app.register_blueprint(active_jobs_bp)
    app.register_blueprint(resolved_jobs_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(timesheet_bp)

    app.jinja_env.globals["is_server"] = IS_SERVER

    @app.before_request
    def _enforce_clock_cutoff():
        db.enforce_clock_cutoff()

    @app.context_processor
    def inject_clock_state():
        segment = db.clock_running_segment()
        running = segment is not None
        day_started = db.clock_day_started()
        if running:
            state = "running"
        elif day_started:
            state = "paused"
        else:
            state = "not-started"
        return {
            "clock_state": state,
            "clock_running": running,
            "clock_day_started": day_started,
            "clock_start_ts": segment["start_ts"] if running else None,
            "clock_base_seconds": db.clock_closed_seconds_today() if running else None,
            "clock_display_seconds": None if running else db.clock_total_seconds_today(),
        }

    @app.route("/")
    def index():
        return redirect(url_for("dashboard.index"))

    @app.route("/about")
    def about_page():
        return render_template(
            "about.html",
            author=version.AUTHOR,
            build_id=version.BUILD_ID,
            build_date=version.BUILD_DATE,
            app_version=version.APP_VERSION,
        )

    @app.route("/_about")
    def about_json():
        return jsonify(
            {
                "name": version.AUTHOR,
                "build_id": version.BUILD_ID,
                "build_date": version.BUILD_DATE,
                "app_version": version.APP_VERSION,
            }
        )

    @app.route("/quit", methods=["POST"])
    def quit_app():
        if IS_SERVER:
            abort(403, "Quit is not available when running in server mode.")

        def shutdown():
            time.sleep(0.3)
            broadcaster = mdns_state.get("broadcaster")
            if broadcaster is not None:
                try:
                    broadcaster.stop()
                except Exception:
                    pass
            os._exit(0)

        threading.Thread(target=shutdown, daemon=True).start()
        return render_template("quit.html")

    return app


def acquire_single_instance_lock():
    """Bind a local-only port as a mutex. Returns the socket if this is the
    only running instance, or None if another instance already holds it."""
    lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock_socket.bind(("127.0.0.1", LOCK_PORT))
        lock_socket.listen(1)
        return lock_socket
    except OSError:
        lock_socket.close()
        return None


def open_browser_later(url, delay=0.6):
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=_open, daemon=True).start()


if __name__ == "__main__":
    # Required before anything else on Windows: the mDNS broadcaster runs in
    # its own child process (see mdns.py) -- isolates it from a network
    # interface change that can otherwise stall zeroconf's Windows
    # networking calls -- and a frozen PyInstaller exe needs this to
    # correctly bootstrap that child instead of relaunching the whole app.
    # (Note: an earlier attempt also isolated the Flask/Waitress server
    # itself into a supervised worker process, aiming to actively recover
    # from that same class of network stall. Reverted -- confirmed by
    # testing that (a) it didn't actually help, since even a separate
    # watcher process's own Python networking calls are subject to the same
    # stall, and (b) each extra process roughly multiplies this machine's
    # antivirus/EDR per-process startup overhead, which made cold starts
    # far worse. mDNS alone is one extra process and stays.)
    multiprocessing.freeze_support()

    port = int(os.environ.get("PORT", 5000))
    url = f"http://localhost:{port}/"

    lock = acquire_single_instance_lock()
    if lock is None:
        print("Time Tracker is already running - opening it in your browser instead.", flush=True)
        webbrowser.open(url)
        raise SystemExit(0)

    app = create_app()

    try:
        from mdns import MdnsBroadcaster

        broadcaster = MdnsBroadcaster(port)
        broadcaster.start()
        mdns_state["broadcaster"] = broadcaster
        print(f"Broadcasting mDNS hostname: http://tt.local:{port}", flush=True)
    except Exception as e:
        print(f"Could not start mDNS broadcast ({e}).", flush=True)
        print("Fallback: add '127.0.0.1 tt.local' to your hosts file.", flush=True)

    print(f"Time Tracker running at http://localhost:{port} and http://tt.local:{port}", flush=True)
    open_browser_later(url)

    from waitress import serve

    serve(app, host="0.0.0.0", port=port)
