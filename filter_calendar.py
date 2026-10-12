#!/usr/bin/env python3
"""
Downloads the Bocconi iCal feed, keeps only Class 38 sessions,
and saves the result to docs/class38.ics for GitHub Pages.
"""
import os
import re
import sys
import urllib.request

FEED_URL = "https://unibocconi.prod.up.cineca.it/api/FiltriICal/impegniICal?id=6acc95e2c9049e0019f38110"
MY_CLASS = 38            # your class number
KEEP_UNTAGGED = True     # keep sessions that mention no "Class NN" at all
OUTPUT_FILE = "docs/class38.ics"
CAL_NAME = "IMB9 Class 38"

MINE = re.compile(rf"\bclass\s*0*{MY_CLASS}\b", re.IGNORECASE)
ANY_CLASS = re.compile(r"\bclass\s*0*\d+\b", re.IGNORECASE)


def load_feed():
    # Optional: pass a local .ics file as the first argument for testing
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            return f.read()
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def unfold(lines):
    """Join folded iCal lines (continuations start with a space or tab)."""
    out = []
    for line in lines:
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return " ".join(out)


def keep_event(event_lines):
    text = unfold(event_lines)
    if MINE.search(text):
        return True
    if KEEP_UNTAGGED and not ANY_CLASS.search(text):
        return True
    return False


def summary_of(event_lines):
    for line in unfold(event_lines).split(" SUMMARY"):
        pass
    for l in event_lines:
        if l.startswith("SUMMARY"):
            return l.split(":", 1)[-1][:70]
    return "(no title)"


def main():
    raw = load_feed()
    lines = raw.splitlines()

    header, footer, events = [], [], []
    current = None
    for line in lines:
        if line.strip() == "BEGIN:VEVENT":
            current = [line]
        elif line.strip() == "END:VEVENT" and current is not None:
            current.append(line)
            events.append(current)
            current = None
        elif current is not None:
            current.append(line)
        elif events:
            footer.append(line)
        else:
            header.append(line)

    if not events:
        print("ERROR: no sessions found in the feed. Leaving the old file untouched.")
        sys.exit(1)

    header = [l for l in header if not l.startswith("X-WR-CALNAME")]
    header.insert(1, f"X-WR-CALNAME:{CAL_NAME}")

    kept = [e for e in events if keep_event(e)]

    print(f"Total sessions in feed: {len(events)}")
    print(f"Kept for Class {MY_CLASS}: {len(kept)}")
    print(f"Dropped: {len(events) - len(kept)}")
    print("Sample of kept sessions:")
    for e in kept[:5]:
        print("  -", summary_of(e))

    if not kept:
        print("ERROR: nothing matched. The class label may be formatted differently.")
        sys.exit(1)

    out = header + [l for e in kept for l in e] + footer
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(out) + "\r\n")
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
