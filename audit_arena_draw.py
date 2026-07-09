#!/usr/bin/env python3
"""Additive audit for the per-arena visiting-player draw (build_attendance_tables
.build_player_arena_draw), focused on Crypto.com Arena.

It does NOT change any builder or output CSV. It reuses build_attendance_tables'
exact game-context / leave-one-out logic and prints:

  1. Every visiting player who logged qualifying minutes at Crypto.com Arena this
     season, their computed mean attendance-draw delta, and whether they are
     flagged All-Star (output/allstar_players.csv) — so any exclusion from the
     site's All-Star-default draw view is visible and explainable, not inferred.

  2. The numbers behind the big "+145.8" tie: the actual attendance and the
     leave-one-out baseline for a handful of the games that produce that exact
     delta, to confirm whether it is a real sellout-attendance coincidence or a
     computation problem. (Diagnostic only — nothing is changed.)

Usage:
    python audit_arena_draw.py [data_dir] [--player-filename NAME] [--arena NAME]
"""

import argparse
import os
import sys
from collections import defaultdict

import pandas as pd

import build_attendance_tables as bat

HERE = os.path.dirname(os.path.abspath(__file__))
ALLSTAR_CSV = os.path.join(HERE, "output", "allstar_players.csv")


def load_allstar_ids():
    ids = set()
    if os.path.exists(ALLSTAR_CSV):
        import csv
        for r in csv.DictReader(open(ALLSTAR_CSV, encoding="utf-8-sig")):
            pid = (r.get("personId") or "").strip()
            if pid:
                ids.add(pid)
    return ids


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir", nargs="?", default=os.path.join(HERE, "data"))
    ap.add_argument("--player-filename", default="PlayerStatistics_sample.csv")
    ap.add_argument("--arena", default="Crypto.com Arena")
    ap.add_argument("--chunksize", type=int, default=100_000)
    ap.add_argument("--coverage-threshold", type=float, default=0.70)
    args = ap.parse_args()

    games_path = os.path.join(args.data_dir, "Games.csv")
    player_path = os.path.join(args.data_dir, args.player_filename)
    for p in (games_path, player_path):
        if not os.path.exists(p):
            sys.exit(f"ERROR: file not found: {p}")

    allstar = load_allstar_ids()
    games_ctx, _ = bat.load_games(games_path, args.coverage_threshold)

    # ---- Crypto game-level context (same fields build_player_arena_draw uses) ----
    ga = games_ctx[games_ctx["arenaName"] == args.arena].copy()
    ga["delta"] = ga["attendance"] - ga["loo_baseline"]
    ok = ga[ga["loo_ok"]].copy()
    print("=" * 78)
    print(f"AUDIT — per-arena visiting-player draw: {args.arena}")
    print("=" * 78)
    print(f"{args.arena}: {len(ga)} games in group, {len(ok)} with a usable "
          f"leave-one-out delta (loo_ok).")
    gids = set(ok["gameId"].tolist())

    # ---- stream players; accumulate visiting (home==0, minutes>0) deltas per person
    per_player = defaultdict(list)   # pid -> [game deltas]
    name = {}
    per_game_players = defaultdict(list)  # gameId -> [(name, pid)]
    gdelta = dict(zip(ok["gameId"], ok["delta"]))
    gatt = dict(zip(ok["gameId"], ok["attendance"]))
    gbase = dict(zip(ok["gameId"], ok["loo_baseline"]))
    usecols = ["personId", "firstName", "lastName", "gameId", "home", "numMinutes"]
    for chunk in pd.read_csv(player_path, usecols=usecols, chunksize=args.chunksize, low_memory=False):
        chunk = chunk[chunk["gameId"].isin(gids)]
        if chunk.empty:
            continue
        chunk["numMinutes"] = pd.to_numeric(chunk["numMinutes"], errors="coerce")
        chunk["home"] = pd.to_numeric(chunk["home"], errors="coerce")
        sub = chunk[(chunk["numMinutes"] > 0) & (chunk["home"] == 0)]
        for row in sub.itertuples(index=False):
            pid = str(row.personId)
            nm = (str(row.firstName or "").strip() + " " + str(row.lastName or "").strip()).strip()
            d = float(gdelta[row.gameId])
            per_player[pid].append(d)
            name[pid] = nm
            per_game_players[row.gameId].append((nm, pid))

    # ---- (1) every visiting player: mean delta + All-Star flag ----
    rows = []
    for pid, deltas in per_player.items():
        mean_delta = round(sum(deltas) / len(deltas), 1)
        rows.append((name[pid], pid, len(deltas), mean_delta, pid in allstar))
    rows.sort(key=lambda x: (-x[3], x[0].lower()))
    n_as = sum(1 for x in rows if x[4])
    print(f"\n(1) VISITING PLAYERS WITH QUALIFYING MINUTES: {len(rows)} "
          f"({n_as} All-Star, {len(rows) - n_as} non-All-Star)")
    print(f"    Site draw default = All-Stars only, so the {len(rows) - n_as} "
          f"non-All-Stars are the ones hidden until 'Show all players' is toggled.")
    print(f"\n    {'Player':<26}{'personId':>12}{'G':>4}{'meanDelta':>12}  AllStar")
    print("    " + "-" * 62)
    for nm, pid, g, md, isas in rows:
        print(f"    {nm[:25]:<26}{pid:>12}{g:>4}{md:>12}  {'YES' if isas else 'no'}")

    # ---- (2) the numbers behind the biggest tie ----
    # find the delta value shared by the most single-game players
    tie_counts = defaultdict(int)
    for nm, pid, g, md, isas in rows:
        tie_counts[md] += 1
    tie_val, tie_n = max(tie_counts.items(), key=lambda kv: kv[1])
    print("\n" + "=" * 78)
    print(f"(2) TIE AUDIT — mean delta {tie_val:+} is shared by {tie_n} players.")
    print("    Games whose game-level delta rounds to that value (attendance vs LOO baseline):")
    print(f"\n    {'gameId':>12}{'attendance':>12}{'LOO_baseline':>14}{'delta':>10}{'visitors':>10}")
    print("    " + "-" * 58)
    shown = 0
    for gid in ok.sort_values("gameId")["gameId"]:
        if round(float(gdelta[gid]), 1) != tie_val:
            continue
        att = float(gatt[gid]); base = float(gbase[gid]); dl = float(gdelta[gid])
        nv = len(per_game_players.get(gid, []))
        print(f"    {gid:>12}{att:>12,.0f}{base:>14,.1f}{dl:>10,.1f}{nv:>10}")
        shown += 1
        if shown >= 4:
            break

    # distinct attendance values across the whole Crypto group (to show sellouts)
    att_vals = sorted(set(round(float(a)) for a in ok["attendance"]))
    print(f"\n    Distinct attendance figures across the {len(ok)} Crypto games: {att_vals}")
    print("    If these cluster on one or two sellout numbers, identical attendance")
    print("    across many games yields identical leave-one-out deltas — a real")
    print("    attendance coincidence, not a computation error.")
    print("=" * 78)


if __name__ == "__main__":
    main()
