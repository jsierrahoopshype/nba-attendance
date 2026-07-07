#!/usr/bin/env python3
"""Season-level team-building win/loss records (2007-present), keyed by canonical
building — the season-retaining companion to team_building_records.csv.

Reuses build_team_building_records.py's game loading and home/away explosion
directly (imported, not modified), then keeps ``season`` as an extra groupby key
alongside teamId / building / gameType. Season-level rows sum back to the
all-time totals in team_building_records.csv.

Additive: build_team_building_records.py and its output CSV are untouched.

Reads:
    <data_dir>/Games.csv
    <data_dir>/arena_mapping.csv

Writes (utf-8-sig):
    <out-dir>/team_building_records_by_season.csv
        per team per building per gameType per season: games, wins, losses,
        win_pct, first/last season are just the season itself.
"""

import argparse
import os

import build_team_building_records as btr


def build_records_by_season(apps):
    # Current-branding city/name per team: most recent appearance (same as the
    # all-time builder).
    latest = (
        apps.sort_values("season")
        .groupby("teamId")
        .agg(teamCity=("teamCity", "last"), teamName=("teamName", "last"))
    )
    grp = apps.groupby(["teamId", "building", "gameType", "season"], dropna=False).agg(
        city=("city", "first"),
        buildingType=("type", "first"),
        games=("won", "size"),
        wins=("won", "sum"),
    )
    grp["losses"] = grp["games"] - grp["wins"]
    grp["win_pct"] = (grp["wins"] / grp["games"]).round(3)
    grp = grp.reset_index().merge(latest, on="teamId", how="left")
    grp["teamId"] = grp["teamId"].astype("int64")
    return grp[[
        "teamId", "teamCity", "teamName", "building", "city", "buildingType",
        "gameType", "season", "games", "wins", "losses", "win_pct",
    ]].sort_values(["teamName", "building", "gameType", "season"]).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir")
    ap.add_argument("--out-dir", default="output")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    g = btr.load_games(args.data_dir)
    apps = btr.team_appearances(g)
    df = build_records_by_season(apps)

    out = os.path.join(args.out_dir, "team_building_records_by_season.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(df):,} rows, {df['teamId'].nunique()} teams, "
          f"{df['building'].nunique()} buildings, "
          f"{df['season'].min()}-{df['season'].max()} seasons)")


if __name__ == "__main__":
    main()
