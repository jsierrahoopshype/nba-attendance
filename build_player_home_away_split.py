#!/usr/bin/env python3
"""Per (player, arena) home-vs-away game split, 2007-present.

For every canonical building a player logged minutes at, counts how many of those
games their team was the building's home tenant (home == 1) vs games they were the
visiting team (home == 0). Buildings are canonical (arena_mapping.csv), so all
name eras of one arena combine — needed to surface players who visited a building
for many years without ever suiting up there for the home club.

Reads:
    <data_dir>/Games.csv
    <data_dir>/arena_mapping.csv       (arenaId -> building, city, type)
    <data_dir>/PlayerStatistics.csv    (streamed in chunks)

Writes (utf-8-sig), additive (new file):
    <out-dir>/player_arena_home_away.csv
        personId, playerName, arenaName, home_games, away_games, games,
        total_points, first_season, last_season
    (arenaName holds the canonical building name.)
"""

import argparse
import os
import sys
from collections import defaultdict

import pandas as pd

GAME_TYPES = ("Regular Season", "Playoffs")


def derive_season(dt: pd.Series) -> pd.Series:
    return dt.dt.year + (dt.dt.month >= 8).astype("int64")


def load_game_context(data_dir: str) -> pd.DataFrame:
    games_path = os.path.join(data_dir, "Games.csv")
    map_path = os.path.join(data_dir, "arena_mapping.csv")
    for p in (games_path, map_path):
        if not os.path.exists(p):
            sys.exit(f"ERROR: file not found: {p}")

    g = pd.read_csv(games_path, low_memory=False)
    g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
    g["season"] = derive_season(g["gameDate"])
    g["arenaId"] = pd.to_numeric(g["arenaId"], errors="coerce")

    m = pd.read_csv(map_path)
    m["arenaId"] = pd.to_numeric(m["arenaId"], errors="coerce")

    g = g[
        (g["season"] >= 2007)
        & g["gameType"].isin(GAME_TYPES)
        & g["arenaId"].isin(set(m["arenaId"].dropna()))
    ].merge(m[["arenaId", "building"]], on="arenaId", how="left")
    return g[["gameId", "building", "season"]]


def build(player_path: str, ctx: pd.DataFrame, chunksize: int) -> pd.DataFrame:
    rec = defaultdict(lambda: {
        "home_games": 0, "away_games": 0, "points": 0.0,
        "first_season": 9999, "last_season": 0, "playerName": "",
    })

    usecols = ["personId", "firstName", "lastName", "gameId", "home", "numMinutes", "points"]
    total = 0
    for chunk in pd.read_csv(player_path, usecols=usecols, chunksize=chunksize, low_memory=False):
        total += len(chunk)
        chunk = chunk.merge(ctx, on="gameId", how="inner")
        if chunk.empty:
            continue
        chunk["numMinutes"] = pd.to_numeric(chunk["numMinutes"], errors="coerce")
        chunk = chunk[chunk["numMinutes"] > 0]
        if chunk.empty:
            continue
        chunk["home"] = pd.to_numeric(chunk["home"], errors="coerce")
        chunk["points"] = pd.to_numeric(chunk["points"], errors="coerce").fillna(0.0)
        chunk["playerName"] = (
            chunk["firstName"].fillna("").str.strip() + " "
            + chunk["lastName"].fillna("").str.strip()
        ).str.strip()

        for row in chunk.itertuples(index=False):
            r = rec[(row.personId, row.building)]
            if row.home == 1:
                r["home_games"] += 1
            elif row.home == 0:
                r["away_games"] += 1
            r["points"] += float(row.points)
            r["first_season"] = min(r["first_season"], int(row.season))
            r["last_season"] = max(r["last_season"], int(row.season))
            r["playerName"] = row.playerName

    rows = []
    for (pid, bld), r in rec.items():
        games = r["home_games"] + r["away_games"]
        rows.append({
            "personId": pid,
            "playerName": r["playerName"],
            "arenaName": bld,
            "home_games": r["home_games"],
            "away_games": r["away_games"],
            "games": games,
            "total_points": int(round(r["points"])),
            "first_season": r["first_season"],
            "last_season": r["last_season"],
        })
    df = pd.DataFrame(rows).sort_values(
        ["playerName", "arenaName"]
    ).reset_index(drop=True)
    print(f"Read {total:,} player rows | (player, arena) rows: {len(df):,}")
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir")
    ap.add_argument("--player-filename", default="PlayerStatistics.csv")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--chunksize", type=int, default=100_000)
    args = ap.parse_args()

    player_path = os.path.join(args.data_dir, args.player_filename)
    if not os.path.exists(player_path):
        sys.exit(f"ERROR: file not found: {player_path}")
    os.makedirs(args.out_dir, exist_ok=True)

    ctx = load_game_context(args.data_dir)
    df = build(player_path, ctx, args.chunksize)

    out = os.path.join(args.out_dir, "player_arena_home_away.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    never_home = df[(df["home_games"] == 0) & (df["away_games"] >= 3)]
    print(f"Wrote {out} ({len(df):,} rows); "
          f"never-home (home==0 & away>=3): {len(never_home):,}")


if __name__ == "__main__":
    main()
