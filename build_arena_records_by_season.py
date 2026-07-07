#!/usr/bin/env python3
"""Season-level player-arena records (2007-present), keyed by canonical building.

Identical join logic to build_arena_records_historical.py (Games.csv +
arena_mapping.csv + chunked PlayerStatistics.csv) — it reuses that module's
game-context loader directly — but keeps ``season`` as an extra groupby key
alongside personId / building / gameType. The season-level rows sum back to the
all-time totals in player_arena_records_2007.csv.

This is additive: build_arena_records_historical.py and its output CSV are not
modified or read/written.

Reads:
    <data_dir>/Games.csv
    <data_dir>/arena_mapping.csv
    <data_dir>/PlayerStatistics.csv   (streamed in chunks)

Writes (utf-8-sig):
    <out-dir>/player_arena_records_by_season.csv
        per player per building per gameType per season: games, wins, losses,
        win_pct, total_points, ppg, career_high
"""

import argparse
import os
import sys
from collections import defaultdict

import pandas as pd

import build_arena_records_historical as bar


def build_records_by_season(player_path, ctx, chunksize):
    rec = defaultdict(lambda: {
        "wins": 0, "losses": 0, "games": 0, "points": 0.0, "career_high": 0.0,
    })
    names = {}
    meta = {}  # building -> (city, type)

    usecols = ["personId", "firstName", "lastName", "gameId", "win", "numMinutes", "points"]
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
        chunk["points"] = pd.to_numeric(chunk["points"], errors="coerce").fillna(0.0)
        chunk["win"] = pd.to_numeric(chunk["win"], errors="coerce")
        chunk["playerName"] = (
            chunk["firstName"].fillna("").str.strip() + " "
            + chunk["lastName"].fillna("").str.strip()
        ).str.strip()

        for (pid, bld, gtype, season), sub in chunk.groupby(
            ["personId", "building", "gameType", "season"], dropna=False
        ):
            r = rec[(pid, bld, gtype, int(season))]
            r["wins"] += int((sub["win"] == 1).sum())
            r["losses"] += int((sub["win"] == 0).sum())
            r["games"] += len(sub)
            r["points"] += float(sub["points"].sum())
            r["career_high"] = max(r["career_high"], float(sub["points"].max()))
            names[pid] = sub["playerName"].iloc[0]
            meta[bld] = (sub["city"].iloc[0], sub["type"].iloc[0])

    rows = []
    for (pid, bld, gtype, season), r in rec.items():
        gms = r["games"]
        city, btype = meta.get(bld, ("", ""))
        rows.append({
            "personId": pid,
            "playerName": names.get(pid, ""),
            "building": bld,
            "city": city,
            "buildingType": btype,
            "gameType": gtype,
            "season": season,
            "games": gms,
            "wins": r["wins"],
            "losses": r["losses"],
            "win_pct": round(r["wins"] / gms, 3) if gms else 0.0,
            "total_points": int(round(r["points"])),
            "ppg": round(r["points"] / gms, 1) if gms else 0.0,
            "career_high": int(round(r["career_high"])),
        })
    df = pd.DataFrame(rows).sort_values(
        ["playerName", "building", "gameType", "season"]
    ).reset_index(drop=True)
    print(f"Read {total:,} player rows | season-level records: {len(df):,}")
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

    ctx = bar.load_game_context(args.data_dir)   # includes gameId, building, city, type, gameType, season
    df = build_records_by_season(player_path, ctx, args.chunksize)

    out = os.path.join(args.out_dir, "player_arena_records_by_season.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(df):,} rows)")


if __name__ == "__main__":
    main()
