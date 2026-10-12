#!/usr/bin/env python3
"""
Builds a personal calendar feed (docs/class38.ics) from the Bocconi University
Planner public calendar, keeping only your class section and batch.

Runs on GitHub Actions. For testing, you can also run it against a saved
response:  python3 filter_calendar.py --from-json response.json
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- settings
API_URL = "https://unibocconi.prod.up.cineca.it/api/Impegni/getImpegniCalendarioPubblico"
PAGE_URL = "https://unibocconi.prod.up.cineca.it/calendarioPubblico/linkCalendarioId=69fca9d36e27dd001eb36371"
LINK_CALENDARIO_ID = "69fca9d36e27dd001eb36371"
CLIENTE_ID = "5a7313911017d5807879b4b4"

MY_CLASS_CODE = "CL038"      # matches "Classe 38"
MY_BATCH = "yellow"          # yellow, green, red or blue
KEEP_UNLABELLED = True       # keep extra events that name no batch (club events, holidays)

START = datetime(2026, 10, 3, 18, 30, tzinfo=timezone.utc)   # 4 Oct 2026, 00:00 IST
END = datetime(2027, 3, 31, 18, 30, tzinfo=timezone.utc)
CHUNK_DAYS = 14
PAUSE_SECONDS = 1.0

OUTPUT_FILE = "docs/class38.ics"
CAL_NAME = "IMB9 Class 38"
# ------------------------------------------------------------------------

BATCHES = ("yellow", "green", "red", "blue")
GENERIC_TITLES = {
    "ATTIVITA' EXTRA CURRICULARI", "EXTRACURRICULAR ACTIVITY",
    "ATTIVITA' DIDATTICA", "TEACHING ACTIVITY",
}
ALL_HANDS = re.compile(r"all batches|all batch|for all|full class|imb ?9 all|all of imb")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def waf_stop(reason, headers=None):
    print("STOPPING: the server did not return calendar data to an automated request.")
    print("Reason:", reason)
    if headers and headers.get("x-amzn-waf-action"):
        print("The site answered with a bot-protection challenge (x-amzn-waf-action:",
              headers.get("x-amzn-waf-action") + ").")
    print("This script does not try to get around bot protection. Existing file left untouched.")
    sys.exit(2)


def fetch_range(start, end):
    body = {
        "mostraImpegniAnnullati": False,
        "mostraIndisponibilitaTotali": False,
        "filtroSfondoId": None,
        "linkCalendarioId": LINK_CALENDARIO_ID,
        "clienteId": CLIENTE_ID,
        "pianificazioneTemplate": False,
        "dataInizio": iso(start),
        "dataFine": iso(end),
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://unibocconi.prod.up.cineca.it",
            "Referer": PAGE_URL,
            "User-Agent": "personal-timetable-sync/1.0 (low volume, personal use)",
        },
        method="POST",
    )
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                headers = {k.lower(): v for k, v in resp.headers.items()}
                if resp.status != 200 or headers.get("x-amzn-waf-action"):
                    waf_stop(f"HTTP {resp.status}", headers)
                try:
                    data = json.loads(raw)
                except ValueError:
                    waf_stop("response was not JSON", headers)
                if not isinstance(data, list):
                    waf_stop("unexpected response shape", headers)
                return data
        except urllib.error.HTTPError as e:
            headers = {k.lower(): v for k, v in e.headers.items()}
            if e.code in (202, 401, 403, 405) or headers.get("x-amzn-waf-action"):
                waf_stop(f"HTTP {e.code}", headers)
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        time.sleep(3 * (attempt + 1))
    print("ERROR: could not fetch", iso(start), "to", iso(end), "->", last_err)
    sys.exit(1)


def fetch_all():
    items, seen = [], set()
    cur = START
    while cur < END:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), END)
        for imp in fetch_range(cur, nxt):
            key = imp.get("id") or json.dumps(imp, sort_keys=True)[:200]
            if key not in seen:
                seen.add(key)
                items.append(imp)
        cur = nxt
        time.sleep(PAUSE_SECONDS)
    return items


# ------------------------------------------------------------ filtering
def partitions(imp):
    out = []
    ev = imp.get("evento") or {}
    for d in ev.get("dettagliDidattici") or []:
        p = d.get("partizione") or {}
        out.append(((p.get("codice") or "").upper(), p.get("descrizione") or ""))
    for f in imp.get("fattoreDiPartizione") or []:
        for p in f.get("partizioni") or []:
            out.append(((p.get("codice") or "").upper(), p.get("descrizione") or ""))
    return out


def is_my_class(imp):
    return any(MY_CLASS_CODE in code for code, _ in partitions(imp))


def note_of(imp):
    return (imp.get("notePubbliche_EN") or imp.get("notePubbliche") or "").strip()


def title_of(imp):
    return (imp.get("nome_EN") or imp.get("nome") or "Session").strip()


def decide(imp):
    """Returns (keep, reason)."""
    if imp.get("attivo") is False or imp.get("stato") == "A":
        return False, "cancelled"
    note = note_of(imp).lower()
    mentioned = {b for b in BATCHES if re.search(rf"\b{b}\b", note)}
    all_hands = bool(ALL_HANDS.search(note))
    titles = {(imp.get("nome") or "").upper(), (imp.get("nome_EN") or "").upper()}
    generic = bool(titles & GENERIC_TITLES)

    if generic or mentioned or all_hands:
        if MY_BATCH in mentioned or all_hands:
            return True, "my batch / everyone"
        if mentioned:
            return False, "other batch"
        return KEEP_UNLABELLED, "no batch named"
    return (True, "my class") if is_my_class(imp) else (False, "other class")


# ------------------------------------------------------------ ics output
def pretty(s):
    return re.sub(r"\b([A-Z])([A-Z']+)\b", lambda m: m.group(1) + m.group(2).lower(), s)


def esc(s):
    return (s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
             .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line):
    raw = line.encode("utf-8")
    parts, limit = [], 75
    while len(raw) > limit:
        cut = limit
        while cut > 0 and (raw[cut] & 0xC0) == 0x80:
            cut -= 1
        parts.append(raw[:cut].decode("utf-8"))
        raw = raw[cut:]
        limit = 74
    parts.append(raw.decode("utf-8"))
    return "\r\n ".join(parts)


def ts(s):
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").strftime("%Y%m%dT%H%M%SZ")


def to_vevent(imp):
    title = pretty(title_of(imp))
    note = note_of(imp)
    titles = {(imp.get("nome") or "").upper(), (imp.get("nome_EN") or "").upper()}
    summary = f"{title}: {note}" if (titles & GENERIC_TITLES and note) else title

    rooms = [a.get("descrizione") for a in imp.get("aule") or [] if a.get("descrizione")]
    building = ""
    for a in imp.get("aule") or []:
        building = (a.get("edificio") or {}).get("descrizione") or building
    location = ", ".join(rooms) + (f" - {building}" if rooms and building else "")

    profs = []
    for d in imp.get("docenti") or []:
        name = f"{d.get('nome', '')} {d.get('cognome', '')}".strip()
        if name and name.upper() != "DEFINIRE DA":
            profs.append(name.title())
    desc = []
    if profs:
        desc.append("Professor: " + ", ".join(profs))
    if note:
        desc.append("Note: " + note)

    stamp = ts(imp.get("dataModifica") or imp.get("dataCreazione") or imp["dataInizio"])
    lines = [
        "BEGIN:VEVENT",
        f"UID:{imp.get('id', imp['dataInizio'])}@bocconi-planner",
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{stamp}",
        f"DTSTART:{ts(imp['dataInizio'])}",
        f"DTEND:{ts(imp['dataFine'])}",
        f"SUMMARY:{esc(summary)}",
    ]
    if location:
        lines.append(f"LOCATION:{esc(location)}")
    if desc:
        lines.append(f"DESCRIPTION:{esc(chr(10).join(desc))}")
    lines += ["STATUS:CONFIRMED", "END:VEVENT"]
    return [fold(l) for l in lines]


def build_ics(events):
    head = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//personal//bocconi-timetable//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{CAL_NAME}",
        "X-WR-TIMEZONE:Asia/Kolkata",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H",
        "X-PUBLISHED-TTL:PT6H",
    ]
    body = [l for e in events for l in to_vevent(e)]
    return "\r\n".join(head + body + ["END:VCALENDAR"]) + "\r\n"


def main():
    if "--from-json" in sys.argv:
        path = sys.argv[sys.argv.index("--from-json") + 1]
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
    else:
        items = fetch_all()

    if not items:
        print("ERROR: no sessions came back. Existing file left untouched.")
        sys.exit(1)

    print("Statuses seen:", dict(Counter(i.get("stato") for i in items)))
    kept, dropped = [], Counter()
    kept_titles = Counter()
    for imp in items:
        keep, why = decide(imp)
        if keep:
            kept.append(imp)
            kept_titles[title_of(imp)] += 1
        else:
            dropped[(title_of(imp), why)] += 1

    kept.sort(key=lambda i: i["dataInizio"])
    print(f"Total sessions: {len(items)} | kept: {len(kept)} | dropped: {len(items) - len(kept)}")
    print("Kept by title:")
    for t, n in kept_titles.most_common():
        print(f"  {n:3d}  {t}")
    print("Dropped by title and reason:")
    for (t, why), n in dropped.most_common():
        print(f"  {n:3d}  {t}  [{why}]")

    if not kept:
        print("ERROR: nothing matched your class. Existing file left untouched.")
        sys.exit(1)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(kept))
    print("Saved", OUTPUT_FILE)


if __name__ == "__main__":
    main()
