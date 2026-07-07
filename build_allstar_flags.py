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


# Generational suffixes to strip when a plain match fails (never used to *replace*
# the exact index — only as a fallback, and only when it leaves one candidate).
SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv"}


def strip_suffix(norm):
    """Drop a single trailing generational-suffix token from a normalized name."""
    parts = norm.split()
    if len(parts) >= 2 and parts[-1] in SUFFIX_TOKENS:
        parts = parts[:-1]
    return " ".join(parts)


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_players_index():
    """Returns:
      exact:   norm(fullname)         -> sorted list of distinct personIds
      suffix:  strip_suffix(norm)     -> sorted list of distinct personIds
      names:   personId               -> display "First Last"
      surname: norm(lastName)         -> list of (personId, first_initial, display)
    """
    exact = defaultdict(set)
    suffix = defaultdict(set)
    names = {}
    surname = defaultdict(list)
    seen_surname = set()
    for r in read_csv(PLAYERS_CSV):
        pid = (r.get("personId") or "").strip()
        if not pid:
            continue
        first = (r.get("firstName") or "").strip()
        last = (r.get("lastName") or "").strip()
        full = (first + " " + last).strip()
        key = norm_name(full)
        if not key:
            continue
        exact[key].add(pid)
        suffix[strip_suffix(key)].add(pid)
        names[pid] = full
        sk = strip_suffix(norm_name(last))
        fi = (norm_name(first)[:1] or "")
        if (sk, pid) not in seen_surname:
            seen_surname.add((sk, pid))
            surname[sk].append((pid, fi, full))
    return (
        {k: sorted(v) for k, v in exact.items()},
        {k: sorted(v) for k, v in suffix.items()},
        names,
        surname,
    )


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

    exact_index, suffix_index, pid_name, surname_index = load_players_index()
    dash_ids = load_dashboard_ids()
    allstars = aggregate_allstars()
    print(f"All-Star recipients (distinct names): {len(allstars)}")

    def near_misses(name):
        """Candidate '(personId) Full Name' strings for a name that didn't match
        cleanly — suffix-stripped candidates plus same-surname/same-initial ones."""
        cands = {}
        for pid in suffix_index.get(strip_suffix(norm_name(name)), []):
            cands[pid] = pid_name.get(pid, "")
        parts = norm_name(name).split()
        if parts:
            sk = strip_suffix(norm_name(parts[-1]))
            fi = parts[0][:1]
            for pid, cfi, disp in surname_index.get(sk, []):
                if cfi == fi:
                    cands[pid] = disp
        return [f"({pid}) {nm}" for pid, nm in sorted(cands.items(), key=lambda x: x[1])][:6]

    rows = []
    resolved_dash, resolved_suffix, review = [], [], []
    for key in sorted(allstars, key=lambda k: allstars[k]["name"].lower()):
        a = allstars[key]
        name = a["name"]
        pids = exact_index.get(key, [])
        pid = None
        if len(pids) == 1:
            pid = pids[0]
        elif len(pids) > 1:
            in_dash = [p for p in pids if p in dash_ids]
            if len(in_dash) == 1:
                pid = in_dash[0]
                resolved_dash.append((name, pids, pid))
            else:
                review.append((name, "ambiguous (exact)", near_misses(name)))
                continue
        else:
            # No exact match — fall back to suffix stripping, but only auto-merge
            # when exactly one distinct personId remains (never when two real
            # players could both match).
            spids = suffix_index.get(strip_suffix(key), [])
            if len(spids) == 1:
                pid = spids[0]
                resolved_suffix.append((name, pid))
            elif len(spids) > 1:
                review.append((name, "ambiguous (suffix strip)", near_misses(name)))
                continue
            else:
                review.append((name, "no match", near_misses(name)))
                continue
        rows.append({
            "personId": pid,
            "playerName": name,
            "times_selected": a["times_selected"],
            "first_year": a["first_year"] if a["first_year"] is not None else "",
            "last_year": a["last_year"] if a["last_year"] is not None else "",
        })

    # ---- auto-merges (transparent, never silent) ----
    if resolved_dash:
        print(f"\nResolved {len(resolved_dash)} name(s) via the dashboard player set:")
        for name, pids, pid in resolved_dash:
            print(f"  {name}: candidates {pids} -> chose {pid} (only one in dashboard data)")
    if resolved_suffix:
        print(f"\nResolved {len(resolved_suffix)} name(s) via generational-suffix strip:")
        for name, pid in resolved_suffix:
            print(f"  {name} -> ({pid}) {pid_name.get(pid, '')}")

    # ---- consolidated review list: every name still unmatched/ambiguous ----
    print(f"\n=== FULL REVIEW LIST — {len(review)} name(s) not matched "
          "(awards name -> reason -> near-miss candidates) ===")
    for name, reason, cands in sorted(review, key=lambda x: x[0].lower()):
        c = "; ".join(cands) if cands else "no near-miss candidates"
        print(f"  {name}  [{reason}]  ->  {c}")

    out = os.path.join(args.out_dir, "allstar_players.csv")
    fieldnames = ["personId", "playerName", "times_selected", "first_year", "last_year"]
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {out} ({len(rows)} matched All-Star players)")


if __name__ == "__main__":
    main()
