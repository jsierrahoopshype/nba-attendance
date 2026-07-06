#!/usr/bin/env python3
"""Aggregate player-arena records up to the city level.

Cities are derived from the ``city`` field already present in the building
records (which comes from arena_mapping.csv). All buildings that share a city are
combined — e.g. every arena in "Las Vegas" folds into one Las Vegas record.
Municipalities are kept distinct exactly as the city field has them, so Brooklyn
stays separate from New York and Inglewood stays separate from Los Angeles.

Reads:
    output/player_arena_records_2007.csv   per player x building x gameType

Writes (utf-8-sig):
    output/city_records.csv                per player x city x gameType:
        games, wins, losses, win_pct, total_points, ppg, career_high,
        first_season, last_season

Note on "games": each row is one player's own game count in that city. A player
is only ever in one building per game, so summing a player's building rows within
a city yields that player's distinct games there. City-wide *total* game counts
(across all players) are derived separately from unique gameIds in the dashboard
generator, never by summing these per-player rows.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, "output")
RECORDS_CSV = os.path.join(OUTPUT_DIR, "player_arena_records_2007.csv")


def as_int(v):
    return int(float(v)) if v not in (None, "") else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=OUTPUT_DIR)
    args = ap.parse_args()
    if not os.path.exists(RECORDS_CSV):
        sys.exit(f"ERROR: file not found: {RECORDS_CSV}")
    os.makedirs(args.out_dir, exist_ok=True)

    agg = defaultdict(lambda: {
        "games": 0, "wins": 0, "losses": 0, "total_points": 0,
        "career_high": 0, "first_season": 9999, "last_season": 0,
        "playerName": "",
    })

    with open(RECORDS_CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            city = r["city"]
            if not city:
                continue
            key = (r["personId"], city, r["gameType"])
            a = agg[key]
            a["games"] += as_int(r["games"])
            a["wins"] += as_int(r["wins"])
            a["losses"] += as_int(r["losses"])
            a["total_points"] += as_int(r["total_points"])
            a["career_high"] = max(a["career_high"], as_int(r["career_high"]))
            a["first_season"] = min(a["first_season"], as_int(r["first_season"]))
            a["last_season"] = max(a["last_season"], as_int(r["last_season"]))
            a["playerName"] = r["playerName"]

    rows = []
    for (pid, city, gtype), a in agg.items():
        g = a["games"]
        rows.append({
            "personId": pid,
            "playerName": a["playerName"],
            "city": city,
            "gameType": gtype,
            "games": g,
            "wins": a["wins"],
            "losses": a["losses"],
            "win_pct": round(a["wins"] / g, 3) if g else 0.0,
            "total_points": a["total_points"],
            "ppg": round(a["total_points"] / g, 1) if g else 0.0,
            "career_high": a["career_high"],
            "first_season": a["first_season"],
            "last_season": a["last_season"],
        })
    rows.sort(key=lambda r: (r["playerName"], r["city"], r["gameType"]))

    out = os.path.join(args.out_dir, "city_records.csv")
    fieldnames = ["personId", "playerName", "city", "gameType", "games", "wins",
                  "losses", "win_pct", "total_points", "ppg", "career_high",
                  "first_season", "last_season"]
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    cities = sorted({r["city"] for r in rows})
    print(f"Wrote {out} ({len(rows):,} rows, {len(cities)} cities)")


if __name__ == "__main__":
    main()
