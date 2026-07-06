#!/usr/bin/env python3
"""Build an All-Star flag table from data/awards.csv.

Reads:
    data/awards.csv          award history; the ``AWARD`` column carries the award
                             name and ``PLAYER / COACH`` the recipient's name.
    data/Players.csv         personId <-> firstName/lastName master (the personId
                             space used everywhere else on the site, e.g. 2544 =
                             LeBron James).

Filters to AWARD == 'All-Star', aggregates per recipient (times selected, first /
last year), and matches each name to a personId via the Players master using a
case- and accent-insensitive comparison.

Writes (utf-8-sig):
    output/allstar_players.csv   personId, playerName, times_selected,
                                 first_year, last_year

Ambiguity is never guessed silently: every name that matches zero personIds, or
more than one, is printed so it can be reviewed. When a name maps to several
personIds but exactly one of them actually appears in the dashboard's player
records, that one is used (and the choice is reported).
"""

import argparse
import csv
import os
import sys
import unicodedata
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
OUTPUT_DIR = os.path.join(HERE, "output")

AWARDS_CSV = os.path.join(DATA_DIR, "awards.csv")
PLAYERS_CSV = os.path.join(DATA_DIR, "Players.csv")
# Used only to disambiguate names that map to several personIds.
DASHBOARD_RECORDS_CSV = os.path.join(OUTPUT_DIR, "player_arena_records_2007.csv")

ALLSTAR_AWARD = "All-Star"


def norm_name(name):
    """Case- and accent-insensitive normalization for name matching.

    Strips diacritics, lowercases, drops punctuation (so "P.J. Tucker" ==
    "PJ Tucker"), and collapses whitespace."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    # Drop apostrophes/periods so "Amar'e" == "Amare" and "P.J." == "PJ";
    # turn any other punctuation (hyphen, slash) into a space.
    s = s.replace("'", "").replace("’", "").replace(".", "")
    s = "".join(c if c.isalnum() or c.isspace() else " " for c in s)
    return " ".join(s.split())


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_players_index():
    """norm(fullname) -> sorted list of distinct personId strings."""
    index = defaultdict(set)
    for r in read_csv(PLAYERS_CSV):
        pid = (r.get("personId") or "").strip()
        if not pid:
            continue
        full = (r.get("firstName") or "").strip() + " " + (r.get("lastName") or "").strip()
        key = norm_name(full)
        if key:
            index[key].add(pid)
    return {k: sorted(v) for k, v in index.items()}


def load_dashboard_ids():
    """personIds that actually appear in the site's player-arena records."""
    ids = set()
    if not os.path.exists(DASHBOARD_RECORDS_CSV):
        return ids
    for r in read_csv(DASHBOARD_RECORDS_CSV):
        pid = (r.get("personId") or "").strip()
        if pid:
            ids.add(pid)
    return ids


def aggregate_allstars():
    """name -> {'name', 'times_selected', 'first_year', 'last_year'} keyed by
    normalized name, preserving one display spelling."""
    agg = {}
    for r in read_csv(AWARDS_CSV):
        if (r.get("AWARD") or "").strip() != ALLSTAR_AWARD:
            continue
        name = (r.get("PLAYER / COACH") or "").strip()
        if not name:
            continue
        key = norm_name(name)
        year = None
        yr = (r.get("YEAR") or "").strip()
        if yr.isdigit():
            year = int(yr)
        a = agg.get(key)
        if a is None:
            a = agg[key] = {"name": name, "times_selected": 0,
                            "first_year": None, "last_year": None}
        a["times_selected"] += 1
        if year is not None:
            a["first_year"] = year if a["first_year"] is None else min(a["first_year"], year)
            a["last_year"] = year if a["last_year"] is None else max(a["last_year"], year)
    return agg


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=OUTPUT_DIR)
    args = ap.parse_args()
    for p in (AWARDS_CSV, PLAYERS_CSV):
        if not os.path.exists(p):
            sys.exit(f"ERROR: file not found: {p}")
    os.makedirs(args.out_dir, exist_ok=True)

    players = load_players_index()
    dash_ids = load_dashboard_ids()
    allstars = aggregate_allstars()
    print(f"All-Star recipients (distinct names): {len(allstars)}")

    rows = []
    unmatched, ambiguous, resolved = [], [], []
    for key in sorted(allstars, key=lambda k: allstars[k]["name"].lower()):
        a = allstars[key]
        pids = players.get(key, [])
        if len(pids) == 0:
            unmatched.append(a["name"])
            continue
        if len(pids) == 1:
            pid = pids[0]
        else:
            in_dash = [p for p in pids if p in dash_ids]
            if len(in_dash) == 1:
                pid = in_dash[0]
                resolved.append((a["name"], pids, pid))
            else:
                ambiguous.append((a["name"], pids, in_dash))
                continue
        rows.append({
            "personId": pid,
            "playerName": a["name"],
            "times_selected": a["times_selected"],
            "first_year": a["first_year"] if a["first_year"] is not None else "",
            "last_year": a["last_year"] if a["last_year"] is not None else "",
        })

    # ---- report ambiguous / unmatched (never guess silently) ----
    if resolved:
        print(f"\nResolved {len(resolved)} name(s) via the dashboard player set:")
        for name, pids, pid in resolved:
            print(f"  {name}: candidates {pids} -> chose {pid} (only one in dashboard data)")
    if ambiguous:
        print(f"\nAMBIGUOUS — matched multiple personIds, skipped ({len(ambiguous)}):")
        for name, pids, in_dash in ambiguous:
            extra = f" (in dashboard: {in_dash})" if in_dash else ""
            print(f"  {name}: {pids}{extra}")
    if unmatched:
        print(f"\nNO MATCH — matched zero personIds, skipped ({len(unmatched)}):")
        for name in unmatched:
            print(f"  {name}")

    out = os.path.join(args.out_dir, "allstar_players.csv")
    fieldnames = ["personId", "playerName", "times_selected", "first_year", "last_year"]
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {out} ({len(rows)} matched All-Star players)")


if __name__ == "__main__":
    main()
