#!/usr/bin/env python3
"""Per-arena visiting-TEAM attendance draw (2026-only, like the per-player one).

Uses the exact same leave-one-out attendance-delta logic as the per-arena player
draw in build_attendance_tables.build_player_arena_draw, but groups by the
visiting (away) team instead of by person. Because the delta is game-level, this
needs only Games.csv — no PlayerStatistics pass.

Reads:
    <data_dir>/Games.csv

Writes (utf-8-sig), additive (new file, never overwrites an existing output):
    <out-dir>/team_arena_draw.csv
        arenaName, teamId, teamCity, teamName, games, mean_delta
"""

import argparse
import os
import sys

import pandas as pd

import build_attendance_tables as bat


def build_arena_team_draw(games_ctx: pd.DataFrame) -> pd.DataFrame:
    """Per (arenaName, away team): mean game-level LOO attendance delta + games.

    Mirrors build_player_arena_draw's grouping — per arena, per visiting side —
    only the key is the team rather than the person."""
    d = games_ctx[games_ctx["loo_ok"]].copy()
    d["delta"] = d["attendance"] - d["loo_baseline"]
    out = (
        d.groupby(["arenaName", "awayteamId", "awayteamCity", "awayteamName"])
        .agg(games=("delta", "size"), mean_delta=("delta", "mean"))
        .reset_index()
        .rename(columns={
            "awayteamId": "teamId",
            "awayteamCity": "teamCity",
            "awayteamName": "teamName",
        })
    )
    out["teamId"] = out["teamId"].astype("int64")
    out["mean_delta"] = out["mean_delta"].round(1)
    return out.sort_values(
        ["arenaName", "mean_delta"], ascending=[True, False]
    ).reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--coverage-threshold", type=float, default=0.70)
    args = ap.parse_args()

    games_path = os.path.join(args.data_dir, "Games.csv")
    if not os.path.exists(games_path):
        sys.exit(f"ERROR: file not found: {games_path}")
    os.makedirs(args.out_dir, exist_ok=True)

    games_ctx, _ = bat.load_games(games_path, args.coverage_threshold)
    df = build_arena_team_draw(games_ctx)

    out = os.path.join(args.out_dir, "team_arena_draw.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"Wrote {out} ({len(df)} rows, {df['arenaName'].nunique()} arenas, "
          f"{df['teamId'].nunique()} teams)")


if __name__ == "__main__":
    main()
