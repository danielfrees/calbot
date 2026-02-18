# gcal_bot.py
from __future__ import annotations

import os
import re
import time
import random
from dataclasses import dataclass
from datetime import date
from typing import Optional, Set, Tuple, List

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv()

# ==============================================================================
# Configuration (env overrides)
# ==============================================================================
HEADLESS_DEFAULT = os.getenv("HEADLESS", "1").strip().lower() not in {"0", "false"}
TIMEOUT_MS_DEFAULT = int(os.getenv("TIMEOUT_MS", "60000"))
POLL_SECONDS_DEFAULT = int(os.getenv("POLL_SECONDS", "120"))

MAX_MONTH_FORWARD_DEFAULT = int(os.getenv("MAX_MONTH_FORWARD", "48"))
MAX_WEEKS_PER_MONTH_DEFAULT = int(os.getenv("MAX_WEEKS_PER_MONTH", "10"))

# FAST defaults (override in .env if needed)
MAX_TIME_CLICKS_PER_WEEK_DEFAULT = int(os.getenv("MAX_TIME_CLICKS_PER_WEEK", "3"))
WAIT_STEP_MS_DEFAULT = int(os.getenv("WAIT_STEP_MS", "8500"))
WEEK_STEP_PAUSE_MS_DEFAULT = int(os.getenv("WEEK_STEP_PAUSE_MS", "130"))
MONTH_STEP_PAUSE_MS_DEFAULT = int(os.getenv("MONTH_STEP_PAUSE_MS", "180"))

WEEK_PROBE_STEPS_DEFAULT = int(os.getenv("WEEK_PROBE_STEPS", "3"))

# NEW: "no times in next year" handling
NO_TIMES_NEXT_YEAR_TEXT = os.getenv("NO_TIMES_NEXT_YEAR_TEXT", "No available times in the next year")
FAST_FORWARD_MONTHS_ON_NEXT_YEAR = int(os.getenv("FAST_FORWARD_MONTHS_ON_NEXT_YEAR", "10"))

NO_AVAIL_TEXT = "No availability during these dates"
JUMP_TEXT = "Jump to the next bookable date"

TIME_SLOT_RE = re.compile(r"^\s*\d{1,2}:\d{2}\s*(?:am|pm)?\s*$", re.IGNORECASE)

MONTH_YEAR_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
    re.IGNORECASE,
)

FULL_DATE_RE = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"([1-9]|[12]\d|3[01]),\s*(20\d{2})\b",
    re.IGNORECASE,
)

MODAL_DAY_RE = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"([1-9]|[12]\d|3[01])\b",
    re.IGNORECASE,
)

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

# ==============================================================================
# Timing jitter (±10%)
# ==============================================================================
JITTER_FRACTION_DEFAULT = float(os.getenv("JITTER_FRACTION", "0.10"))

def _jitter_ms(ms: int, frac: float = JITTER_FRACTION_DEFAULT) -> int:
    """
    Returns ms varied randomly by ±frac. Ensures at least 1ms.
    Example: ms=1000, frac=0.1 -> uniform in [900, 1100]
    """
    if ms <= 0:
        return 0
    lo = 1.0 - max(0.0, frac)
    hi = 1.0 + max(0.0, frac)
    return max(1, int(ms * random.uniform(lo, hi)))

def _jitter_s(seconds: int | float, frac: float = JITTER_FRACTION_DEFAULT) -> float:
    """
    Returns seconds varied randomly by ±frac. Ensures at least 0.001s.
    """
    if seconds <= 0:
        return 0.0
    lo = 1.0 - max(0.0, frac)
    hi = 1.0 + max(0.0, frac)
    return max(0.001, float(seconds) * random.uniform(lo, hi))

def _wait_ms(page, ms: int) -> None:
    page.wait_for_timeout(_jitter_ms(ms))

# ==============================================================================
# Public types
# ==============================================================================
@dataclass(frozen=True)
class EarliestAvailability:
    iso_date: str
    source: str  # "modal_date_min"

# ==============================================================================
# Notifications
# ==============================================================================
def toast(title: str, message: str) -> None:
    """
    Windows toast via winotify (Win11). Install: pip install winotify
    """
    try:
        from winotify import Notification, audio
        t = Notification(app_id="Appointment Bot", title=title, msg=message)
        t.set_audio(audio.Default, loop=False)
        t.show()
    except Exception as e:
        print("[TOAST FAILED]", repr(e))

def notify(
    title: str,
    message: str,
    *,
    print_to_terminal: bool = True,
    toast_alert: bool = False,
) -> None:
    if print_to_terminal:
        print(f"{title}: {message}")
    if toast_alert:
        toast(title, message)

# ==============================================================================
# UI primitives
# ==============================================================================
def _dismiss_modal(page) -> None:
    try:
        dialog = page.locator("[role='dialog']").first
        if dialog.count() > 0 and dialog.is_visible():
            cancel = dialog.get_by_role("button", name=re.compile(r"cancel", re.I))
            if cancel.count() > 0 and cancel.is_visible():
                cancel.click()
                _wait_ms(page, 80)
                return
            page.keyboard.press("Escape")
            _wait_ms(page, 80)
    except Exception:
        pass

def _page_has_text(page, text: str) -> bool:
    try:
        loc = page.get_by_text(text, exact=False)
        return loc.count() > 0 and loc.is_visible()
    except Exception:
        return False

def _click_jump_to_next_bookable(page, debug: bool = False) -> bool:
    try:
        jump = page.get_by_text(JUMP_TEXT, exact=False)
        if jump.count() > 0 and jump.is_visible():
            if debug:
                print("[debug] clicking Jump to next bookable")
            jump.click()
            return True
    except Exception:
        pass
    return False

def _has_no_availability_message(page) -> bool:
    return _page_has_text(page, NO_AVAIL_TEXT)

def _has_no_times_next_year_message(page) -> bool:
    return _page_has_text(page, NO_TIMES_NEXT_YEAR_TEXT)

def _wait_until_times_or_messages(page, timeout_ms: int) -> str:
    """
    Returns:
        - "times"
        - "noavail"
        - "nxty"      (no times in next year)
        - "timeout"
    """
    start = time.time()
    # jitter the overall timeout slightly too (optional but consistent with your ask)
    timeout_ms_j = _jitter_ms(timeout_ms)
    while (time.time() - start) * 1000 < timeout_ms_j:
        if _has_no_times_next_year_message(page):
            return "nxty"
        if _has_no_availability_message(page):
            return "noavail"
        try:
            if _get_time_buttons(page).count() > 0:
                return "times"
        except Exception:
            pass
        _wait_ms(page, 60)
    return "timeout"

def _infer_year_from_page(page) -> Optional[int]:
    try:
        body = page.inner_text("body")
        m = MONTH_YEAR_RE.search(body)
        if m:
            return int(m.group(2))
    except Exception:
        pass
    return None

def _get_time_buttons(page):
    return page.get_by_role("button").filter(has_text=TIME_SLOT_RE)

def _parse_date_from_dialog_text(dialog_text: str, fallback_year: Optional[int]) -> Optional[date]:
    mf = FULL_DATE_RE.search(dialog_text or "")
    if mf:
        month_num = MONTHS[mf.group(2).lower()]
        return date(int(mf.group(4)), month_num, int(mf.group(3)))

    m = MODAL_DAY_RE.search(dialog_text or "")
    if not m or not fallback_year:
        return None
    month_num = MONTHS[m.group(2).lower()]
    day_num = int(m.group(3))
    return date(fallback_year, month_num, day_num)

def _click_time_and_read_date(page, idx: int, debug: bool = False) -> Optional[date]:
    _dismiss_modal(page)

    tb = _get_time_buttons(page)
    n = tb.count()
    if n == 0 or idx >= n:
        return None

    btn = tb.nth(idx)
    if not btn.is_visible():
        return None

    time_label = (btn.inner_text() or "").strip()
    if debug:
        print(f"[debug] click time idx={idx} '{time_label}'")

    btn.click(timeout=2500)
    _wait_ms(page, 120)

    dialog = page.locator("[role='dialog']").first
    if dialog.count() == 0 or not dialog.is_visible():
        if debug:
            print(f"[debug] '{time_label}' -> dialog did not open")
        return None

    txt = dialog.inner_text()
    year_guess = _infer_year_from_page(page)
    d = _parse_date_from_dialog_text(txt, fallback_year=year_guess)

    if debug:
        print(f"[debug] parsed_date={d} year_guess={year_guess}")

    _dismiss_modal(page)
    return d

# ==============================================================================
# Navigation helpers
# ==============================================================================
def _month_nav_buttons(page):
    prev_btn = page.locator('button[aria-label*="Previous month"], button[aria-label*="previous month"]').first
    next_btn = page.locator('button[aria-label*="Next month"], button[aria-label*="next month"]').first
    return prev_btn, next_btn

def _click_next_month(page, debug: bool = False) -> bool:
    _, next_btn = _month_nav_buttons(page)
    if next_btn.count() > 0 and next_btn.is_visible():
        if debug:
            print("[debug] clicking Next month")
        next_btn.click()
        return True
    return False

def _click_next_week(page, debug: bool = False) -> bool:
    _, month_next = _month_nav_buttons(page)
    month_bb = month_next.bounding_box() if month_next.count() else None

    candidates = []
    for b in page.locator("button[aria-label]").all():
        try:
            if not b.is_visible():
                continue
            aria = (b.get_attribute("aria-label") or "").lower()
            if "next" not in aria:
                continue
            if "month" in aria:
                continue
            bb = b.bounding_box()
            if not bb:
                continue
            if month_bb and abs(bb["x"] - month_bb["x"]) < 3 and abs(bb["y"] - month_bb["y"]) < 3:
                continue
            candidates.append((bb["x"], aria, b))
        except Exception:
            continue

    if not candidates:
        return False

    candidates.sort(key=lambda t: t[0])
    x, aria, btn = candidates[-1]
    if debug:
        print(f"[debug] clicking Next-week aria='{aria}' x={x:.1f}")
    btn.click()
    return True

def _fast_forward_months(page, n: int, debug: bool = False) -> bool:
    """
    Click next-month n times. Returns False if it can't continue.
    """
    if debug:
        print(f"[debug] fast-forwarding {n} months")
    for i in range(n):
        if not _click_next_month(page, debug=debug):
            if debug:
                print(f"[debug] failed fast-forward at i={i}")
            return False
        _wait_ms(page, MONTH_STEP_PAUSE_MS_DEFAULT)
    return True

# ==============================================================================
# Core logic
# ==============================================================================
def _probe_week_dates(page, *, max_clicks: int, debug: bool = False) -> List[date]:
    tb = _get_time_buttons(page)
    n = tb.count()
    if debug:
        labels = [(tb.nth(i).inner_text() or "").strip() for i in range(min(n, 6))]
        print(f"[debug] probe: n_time_buttons={n} labels={labels}")

    out: List[date] = []
    seen = set()
    for i in range(min(n, max_clicks)):
        d = _click_time_and_read_date(page, i, debug=debug)
        if d and d not in seen:
            out.append(d)
            seen.add(d)

    if debug:
        print("[debug] probe parsed:", [d.isoformat() for d in out])
    return out

def _scan_month_week_by_week(
    page,
    month_ym: Tuple[int, int],
    *,
    max_weeks: int,
    max_time_clicks_per_week: int,
    seed_min: date,
    debug: bool = False,
) -> Set[date]:
    """
    Scan weeks in the month; always advance weeks even if you see NO_AVAIL on current week
    (availability might be later in the month).
    Stop when representative dates move outside month.
    """
    found: Set[date] = set()
    last_rep: Optional[date] = None

    for w in range(max_weeks):
        _dismiss_modal(page)
        status = _wait_until_times_or_messages(page, timeout_ms=WAIT_STEP_MS_DEFAULT)
        if debug:
            print(f"[debug] month_ym={month_ym} week={w} status={status}")

        rep: Optional[date] = None

        if status == "nxty":
            if debug:
                print("[debug] encountered 'no times next year' during week scan; breaking month scan")
            break

        if status == "times":
            week_dates = _probe_week_dates(page, max_clicks=max_time_clicks_per_week, debug=debug)
            if week_dates:
                rep = min(week_dates)
                for d in week_dates:
                    if (d.year, d.month) == month_ym:
                        found.add(d)
                        if d == seed_min:
                            if debug:
                                print("[debug] early-exit: found seed_min within month")
                            return found

        if rep is not None and (rep.year, rep.month) != month_ym:
            if debug:
                print(f"[debug] STOP month scan: rep {rep.isoformat()} outside month_ym {month_ym}")
            break

        if rep is not None and last_rep is not None and rep == last_rep:
            if debug:
                print(f"[debug] STOP month scan: rep repeated {rep.isoformat()} -> stuck")
            break
        if rep is not None:
            last_rep = rep

        if not _click_next_week(page, debug=debug):
            break
        _wait_ms(page, WEEK_STEP_PAUSE_MS_DEFAULT)

    return found

def get_earliest_available_date(
    booking_url: str,
    *,
    headless: bool = HEADLESS_DEFAULT,
    timeout_ms: int = TIMEOUT_MS_DEFAULT,
    max_month_forward: int = MAX_MONTH_FORWARD_DEFAULT,
    max_weeks_per_month: int = MAX_WEEKS_PER_MONTH_DEFAULT,
    max_time_clicks_per_week: int = MAX_TIME_CLICKS_PER_WEEK_DEFAULT,
    debug: bool = False,
) -> Optional[EarliestAvailability]:
    """
    Behavior:
        - If "No available times in the next year" appears:
            - fast-forward FAST_FORWARD_MONTHS_ON_NEXT_YEAR
            - try Jump-to-next-bookable again
        - Otherwise:
            - move month-by-month, but within each month week-step if needed (fast),
            so we don’t miss availability in later weeks even if the first week shows NO_AVAIL.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(booking_url, wait_until="networkidle", timeout=timeout_ms)

        _click_jump_to_next_bookable(page, debug=debug)
        _wait_ms(page, 220)

        for month_step in range(max_month_forward + 1):
            _dismiss_modal(page)

            status = _wait_until_times_or_messages(page, timeout_ms=WAIT_STEP_MS_DEFAULT)
            if debug:
                print(f"[debug] ===== month_step={month_step} status={status} =====")

            if status == "nxty":
                if debug:
                    print("[debug] 'No available times in the next year' detected")
                if not _fast_forward_months(page, FAST_FORWARD_MONTHS_ON_NEXT_YEAR, debug=debug):
                    browser.close()
                    return None
                _wait_ms(page, 800)
                _click_jump_to_next_bookable(page, debug=debug)
                _wait_ms(page, 800)
                continue

            seed_dates: List[date] = []

            if status == "times":
                seed_dates = _probe_week_dates(page, max_clicks=min(2, max_time_clicks_per_week), debug=debug)
            else:
                for w in range(WEEK_PROBE_STEPS_DEFAULT):
                    if not _click_next_week(page, debug=debug):
                        break
                    _wait_ms(page, WEEK_STEP_PAUSE_MS_DEFAULT)
                    s2 = _wait_until_times_or_messages(page, timeout_ms=WAIT_STEP_MS_DEFAULT)
                    if debug:
                        print(f"[debug] month_step={month_step} week_probe={w} status={s2}")

                    if s2 == "nxty":
                        if debug:
                            print("[debug] 'No available times in the next year' detected during probe")
                        if not _fast_forward_months(page, FAST_FORWARD_MONTHS_ON_NEXT_YEAR, debug=debug):
                            browser.close()
                            return None
                        _wait_ms(page, 220)
                        _click_jump_to_next_bookable(page, debug=debug)
                        _wait_ms(page, 220)
                        seed_dates = []
                        break

                    if s2 == "times":
                        seed_dates = _probe_week_dates(page, max_clicks=min(2, max_time_clicks_per_week), debug=debug)
                        break

            if seed_dates:
                seed_min = min(seed_dates)
                month_ym = (seed_min.year, seed_min.month)
                if debug:
                    print(f"[debug] seeded month_ym={month_ym} seed_min={seed_min.isoformat()}")

                found = _scan_month_week_by_week(
                    page,
                    month_ym,
                    max_weeks=max_weeks_per_month,
                    max_time_clicks_per_week=max_time_clicks_per_week,
                    seed_min=seed_min,
                    debug=debug,
                )
                if found:
                    earliest = min(found)
                    browser.close()
                    return EarliestAvailability(iso_date=earliest.isoformat(), source="modal_date_min")

            if month_step >= max_month_forward:
                break
            if not _click_next_month(page, debug=debug):
                break
            _wait_ms(page, MONTH_STEP_PAUSE_MS_DEFAULT)

        browser.close()
        return None

def poll_earliest_and_notify(
    booking_url: str,
    *,
    poll_seconds: int = POLL_SECONDS_DEFAULT,
    print_to_terminal: bool = True,
    toast_alert: bool = False,
    only_notify_on_change: bool = True,
    headless: bool = HEADLESS_DEFAULT,
    debug: bool = False,
) -> None:
    last_seen: Optional[str] = None
    print(f"Polling every {poll_seconds}s | URL: {booking_url}")

    while True:
        r = get_earliest_available_date(
            booking_url,
            headless=headless,
            debug=debug,
        )
        current = r.iso_date if r else None

        if current and (not only_notify_on_change or current != last_seen):
            notify(
                "Appointment Bot",
                f"Earliest available date: {current} (source={r.source})\n"
                f"Book here: {booking_url}",
                print_to_terminal=print_to_terminal,
                toast_alert=toast_alert,
            )
            last_seen = current

        time.sleep(_jitter_s(poll_seconds))

if __name__ == "__main__":
    url = os.getenv("BOOKING_URL", "").strip()
    if not url:
        print("Set BOOKING_URL in .env to run as a script.")
    else:
        poll_earliest_and_notify(
            url,
            poll_seconds=POLL_SECONDS_DEFAULT,
            print_to_terminal=True,
            toast_alert=True,
            only_notify_on_change=True,
            headless=HEADLESS_DEFAULT,
            debug=True,
        )
