# Time Tracker

A fast, local-first job and time tracking tool.

Built by [ApnaFilter](https://ko-fi.com/fpnafilter) — designed and
developed with AI-assisted development (Claude Code). I made the product
and architecture decisions, diagnosed and fixed bugs, and did the QA; the
AI handled implementation.

## What it does

- **Job timers** — Start, stop, and resume a single running timer per job,
  or add time manually after the fact.
- **Rich-text descriptions & notes** — Formatted text and tables, with
  direct paste support from Excel and Word.
- **Search** — Find any job instantly by number, customer, summary, or
  note content.
- **Day clock** — Clock in, pause for lunch, and clock out, independent of
  job timers, with automatic end-of-day cutoff.
- **Weekly timesheet** — A Mon–Sun grid of hours per job, with CSV export.
- **Dashboard** — Today's and this week's hours at a glance, plus a
  reminder for whichever job has gone the longest without attention.
- **Staleness indicator** — A color-coded dot shows how long it's been
  since a job last had activity.
- **Copy details** — One click to copy a job's full details to your
  clipboard.

## Running it

### As a server

```bash
git clone https://github.com/tcm-coder/time-tracker.git
cd time-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

The app serves on `http://0.0.0.0:5000` by default.

By default, the SQLite database is stored alongside the app. To store it
elsewhere (recommended for server deployments), set:

```bash
export TIMETRACKER_DB_DIR=/path/to/data-directory
```


### Running persistently (Linux/systemd)

A sample service file is included at `deploy/timetracker.service`. Adjust
the paths inside it to match where you cloned the repo, then:

```bash
sudo cp deploy/timetracker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now timetracker
```

Set `IS_SERVER=1` (already included in the sample service file) to hide
the desktop-only Quit button and disable its route.

**Note on timezone:** fresh Linux installations often default to UTC. If
timestamps look wrong, set your server's timezone before running:

```bash
sudo timedatectl set-timezone Your/Timezone
```

### As a desktop app

This app also builds as a standalone desktop executable via PyInstaller
(see `app.spec`). Desktop builds include a Quit button and store their
database next to the executable; server deployments (set `IS_SERVER=1`)
hide the Quit button and disable that route.

## Support

If this is useful to you, consider [supporting development on
Ko-fi](https://ko-fi.com/apnafilter).

## License

MIT
