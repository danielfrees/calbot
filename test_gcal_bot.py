"""
test_gcal_bot.py
"""

from gcal_bot import get_earliest_available_date, toast

LINKS = [
   "https://calendar.app.google/eAdwLgoFB71AnZ5m9",
]

def main():
    for url in LINKS:
        print("=" * 80)
        print("Testing:", url)

        r = get_earliest_available_date(url, headless=False, debug=True)

        print("Result:", r)
        earliest = r.iso_date if r else None
        print("Earliest ISO date:", earliest)

        toast("GCal bot test", f"Earliest: {earliest}")

if __name__ == "__main__":
    main()
