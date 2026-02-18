# CalBot (Google Calendar Booking Watcher) 🤖📅

CalBot is a Python + Playwright bot that opens a **Google Calendar booking link** (`calendar.app.google/...`) and finds the **earliest available appointment date**. It can run once (to fetch the earliest date) or **poll** on an interval and notify you when availability changes.

Notification options:
- ✅ Print to terminal
- ✅ Windows toast notifications (optional, via `winotify`)
- ✅ Twilio SMS text messages (optional; configure via environment variables)

This project is optimized for speed and robustness:
- Attempts **“Jump to the next bookable date”** first (when available)
- Scans **week-by-week inside a month** so availability in later weeks isn’t missed
- Handles **“No available times in the next year”** by fast-forwarding months and trying again
- Adds **timing jitter (±10%)** to reduce brittle behavior

> ⚠️ **Disclaimer:** Automating web UIs can violate terms of service for some websites. Use responsibly, respect rate limits, and understand the rules for the booking service you’re interacting with.

---

## Features

- ✅ Find earliest available date from a booking URL
- ✅ Week-by-week scanning inside a month (avoids missing later-week availability)
- ✅ Fast-forward handling for “No available times in the next year”
- ✅ Configurable timeouts / pacing via `.env`
- ✅ Optional polling loop with notify-on-change
- ✅ Notifications: terminal, Windows toasts, Twilio SMS
- ✅ Timing jitter (±10%) for waits and polling intervals

---

## Project Layout

- `gcal_bot.py` — main logic + polling loop
- `test_gcal_bot.py` — simple test runner for one or more booking links
- `.env` — local configuration (ignored by git)
- `.env.example` — example configuration (committed)

---

## Requirements

- Python 3.10+ recommended
- Playwright (Chromium)
- python-dotenv
- (Optional) winotify (Windows toast)
- (Optional) twilio (SMS)

---

## Setup

### 1) Create and activate an environment (optional but recommended)

**Conda:**
```bash
conda create -n calbot python=3.11 -y
conda activate calbot
```

venv:

    python -m venv .venv

Windows PowerShell:

    .venv\Scripts\Activate.ps1


### 2) Install dependencies

    pip install -r requirements.txt


### 3) Install Playwright browsers

    playwright install


---

## Configuration (.env)

Create a file named `.env` in the project root (keep it local; do not commit it).

Minimum (for script usage):

    BOOKING_URL=https://calendar.app.google/yourlink


Common settings:

    # Browser
    HEADLESS=1

    # Core timing
    TIMEOUT_MS=60000
    POLL_SECONDS=120

    # Navigation bounds
    MAX_MONTH_FORWARD=48
    MAX_WEEKS_PER_MONTH=10

    # Performance tuning
    MAX_TIME_CLICKS_PER_WEEK=3
    WAIT_STEP_MS=8500
    WEEK_STEP_PAUSE_MS=130
    MONTH_STEP_PAUSE_MS=180
    WEEK_PROBE_STEPS=3

    # Handling "No available times in the next year"
    NO_TIMES_NEXT_YEAR_TEXT=No available times in the next year
    FAST_FORWARD_MONTHS_ON_NEXT_YEAR=10

    # Random jitter ± fraction (0.10 = ±10%)
    JITTER_FRACTION=0.10


### Twilio SMS (optional)

To enable SMS notifications via Twilio, add these to `.env`:

    TWILIO_ACCOUNT_SID=...
    TWILIO_AUTH_TOKEN=...
    TWILIO_FROM_NUMBER=+15551234567
    TWILIO_TO_NUMBER=+15557654321


---

## Test Usage (Local Runner)

`test_gcal_bot.py` is a convenience script to quickly test one or more booking URLs.

1) Edit `LINKS` in `test_gcal_bot.py`:

    LINKS = [
        "https://calendar.app.google/eAdwLgoFB71AnZ5m9",
    ]

2) Run:

```bash
    python -m tests.test_gcal_bot
```

This will:
- open each booking link
- find the earliest available date
- print the result
- optionally show a Windows toast notification (if installed)


---

## Actual Usage

### A) Run once (library function)

Use `get_earliest_available_date()` to fetch the earliest date one time:

```python
    from gcal_bot import get_earliest_available_date

    r = get_earliest_available_date(
        "https://calendar.app.google/yourlink",
        headless=True,
        debug=False,
    )

    print(r.iso_date if r else None)
```

Return type:
- `None` if no availability found in scan window
- otherwise `EarliestAvailability(iso_date="YYYY-MM-DD", source="modal_date_min")`


### B) Run as a polling bot (script)

Set `BOOKING_URL` in `.env`, then run:

```bash
    python -m src.gcal_bot
```

The poller will:
- check availability every `POLL_SECONDS` seconds (with jitter)
- notify only when the earliest date changes (default)
- print to terminal
- optionally send Windows toast notifications
- optionally send Twilio SMS notifications (if configured)


---

## How It Works (High Level)

The bot waits until one of these becomes true:
- there are time-slot buttons (e.g., “9:30am”)
- “No availability during these dates”
- “No available times in the next year”

Then it:
1. Attempts to click "Jump to the next bookable date"
2. If time slots exist, clicks a few time buttons and reads the modal dialog text to parse the appointment date
3. If it finds a “seed” date, scans that month week-by-week to find the minimum date in the month
4. If it sees "No available times in the next year", it fast-forwards months and retries jump-to-next-bookable

Because web UIs can change, this logic uses heuristics and regex patterns for date parsing.


---

## Troubleshooting

### Playwright browser missing

Run:

    playwright install


### Date parsing failures

If Google changes the modal date format, update:
- `FULL_DATE_RE`
- `MODAL_DAY_RE`


### Bot seems slow

You can reduce:
- `WAIT_STEP_MS`
- `WEEK_STEP_PAUSE_MS`
- `MONTH_STEP_PAUSE_MS`

Keep some delay to avoid UI flakiness or rate-limiting.


---

## Security

- Do NOT commit `.env`.
- If you accidentally committed it (especially with Twilio credentials), remove it from history and rotate secrets.


---

## License

MIT — see `LICENSE`.


---

## Author

Daniel Frees