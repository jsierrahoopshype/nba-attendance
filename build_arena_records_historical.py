#!/usr/bin/env python3
"""Build historical player-arena records (2007-present) keyed by canonical
building, using arena_mapping.csv to unify arenaIds across name eras.

Usage:
    python build_arena_records_historical.py <data_dir> [--out-dir DIR]
                                                        [--chunksize N]

Reads:
    <data_dir>/Games.csv
    <data_dir>/arena_mapping.csv     (arenaId,building,city,type,note)
    <data_dir>/PlayerStatistics.csv  (streamed in chunks)

Writes (utf-8-sig):
    <out-dir>/player_arena_records_2007.csv
        per player per building per gameType: games, W-L, win%,
        total points, ppg, career high, first/last season played there
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
    """Return one row per qualifying game: gameId, building, city, type,
    gameType, season."""
    games_path = os.path.join(data_dir, "Games.csv")
    map_path = os.path.join(data_dir, "arena_mapping.csv")
    for p in (games_path, map_path):
        if not os.path.exists(p):
            sys.exit(f"ERROR: file not found: {p}")

    g = pd.read_csv(games_path, low_memory=False)
    g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
    g["season"] = derive_season(g["gameDate"])

    m = pd.read_csv(map_path)
    m["arenaId"] = pd.to_numeric(m["arenaId"], errors="coerce")

    g = g[
        (g["season"] >= 2007)
        & g["gameType"].isin(GAME_TYPES)
        & g["arenaId"].isin(set(m["arenaId"]))
    ].copy()

    g = g.merge(m[["arenaId", "building", "city", "type"]], on="arenaId", how="left")
    ctx = g[["gameId", "building", "city", "type", "gameType", "season"]]
    print(
        f"Qualifying games 2007+: {len(ctx):,} across "
        f"{ctx['building'].nunique()} buildings"
    )
    return ctx


def build_records(player_path: str, ctx: pd.DataFrame, chunksize: int) -> pd.DataFrame:
    rec = defaultdict(
        lambda: {
            "wins": 0,
            "losses": 0,
            "games": 0,
            "points": 0.0,
            "career_high": 0.0,
            "first_season": 9999,
            "last_season": 0,
        }
    )
    names = {}
    meta = {}  # building -> (city, type)

    usecols = [
        "personId", "firstName", "lastName",
        "gameId", "win", "numMinutes", "points",
    ]
    total = 0
    for chunk in pd.read_csv(
        player_path, usecols=usecols, chunksize=chunksize, low_memory=False
    ):
        total += len(chunk)
        chunk = chunk.merge(ctx, on="gameId", how="inner")
        if chunk.empty:
            continue

        chunk["numMinutes"] = pd.to_numeric(chunk["numMinutes"], errors="coerce")
        chunk = chunk[chunk["numMinutes"] > 0]
        if chunk.empty:
            continue
        chunk["points"] = pd.to_numeric(chunk["points"], errors="coerce").fillna(0.0)
        chunk["win"] = pd.to_numeric(chunk["win"], errors="coerce")
        chunk["playerName"] = (
            chunk["firstName"].fillna("").str.strip()
            + " "
            + chunk["lastName"].fillna("").str.strip()
        ).str.strip()

        for (pid, bld, gtype), sub in chunk.groupby(
            ["personId", "building", "gameType"], dropna=False
        ):
            r = rec[(pid, bld, gtype)]
            r["wins"] += int((sub["win"] == 1).sum())
            r["losses"] += int((sub["win"] == 0).sum())
            r["games"] += len(sub)
            r["points"] += float(sub["points"].sum())
            r["career_high"] = max(r["career_high"], float(sub["points"].max()))
            r["first_season"] = min(r["first_season"], int(sub["season"].min()))
            r["last_season"] = max(r["last_season"], int(sub["season"].max()))
            names[pid] = sub["playerName"].iloc[0]
            meta[bld] = (sub["city"].iloc[0], sub["type"].iloc[0])

    rows = []
    for (pid, bld, gtype), r in rec.items():
        gms = r["games"]
        city, btype = meta.get(bld, ("", ""))
        rows.append(
            {
                "personId": pid,
                "playerName": names.get(pid, ""),
                "building": bld,
                "city": city,
                "buildingType": btype,
                "gameType": gtype,
                "games": gms,
                "wins": r["wins"],
                "losses": r["losses"],
                "win_pct": round(r["wins"] / gms, 3) if gms else 0.0,
                "total_points": int(round(r["points"])),
                "ppg": round(r["points"] / gms, 1) if gms else 0.0,
                "career_high": int(round(r["career_high"])),
                "first_season": r["first_season"],
                "last_season": r["last_season"],
            }
        )
    df = (
        pd.DataFrame(rows)
        .sort_values(["playerName", "building", "gameType"])
        .reset_index(drop=True)
    )
    print(f"Read {total:,} player rows | records: {len(df):,}")
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--chunksize", type=int, default=100_000)
    args = ap.parse_args()

    player_path = os.path.join(args.data_dir, "PlayerStatistics.csv")
    if not os.path.exists(player_path):
        sys.exit(f"ERROR: file not found: {player_path}")
    os.makedirs(args.out_dir, exist_ok=True)

    ctx = load_game_context(args.data_dir)
    df = build_records(player_path, ctx, args.chunksize)

    out = os.path.join(args.out_dir, "player_arena_records_2007.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(df):,} rows)")


if __name__ == "__main__":
    main()