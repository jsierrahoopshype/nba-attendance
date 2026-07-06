#!/usr/bin/env python3
"""Build team-building win/loss records (2007-present) keyed by canonical
building, using arena_mapping.csv to unify arenaIds across name eras.

Every team's appearances at a building — home games and road games combined —
are counted. The win indicator is Games.csv's ``winner`` column, which holds the
winning team's id; a row is a win for a team when ``winner`` equals that team's
id (compared against both the home and away side of each game).

Usage:
    python build_team_building_records.py <data_dir> [--out-dir DIR]

Reads:
    <data_dir>/Games.csv          (gameId, home/away team + score, winner,
                                    gameType, arenaId, gameDate, ...)
    <data_dir>/arena_mapping.csv  (arenaId, building, city, type, note)

Writes (utf-8-sig):
    <out-dir>/team_building_records.csv
        per team per building per gameType: games, wins, losses, win_pct,
        first/last season appearing there.
"""

import argparse
import os
import sys

import pandas as pd

GAME_TYPES = ("Regular Season", "Playoffs")


def derive_season(dt: pd.Series) -> pd.Series:
    """NBA season-ending year: Aug..Dec belongs to the next year's season."""
    return dt.dt.year + (dt.dt.month >= 8).astype("int64")


def load_games(data_dir: str) -> pd.DataFrame:
    """One qualifying row per game with building/season attached."""
    games_path = os.path.join(data_dir, "Games.csv")
    map_path = os.path.join(data_dir, "arena_mapping.csv")
    for p in (games_path, map_path):
        if not os.path.exists(p):
            sys.exit(f"ERROR: file not found: {p}")

    g = pd.read_csv(games_path, low_memory=False)
    g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
    g["season"] = derive_season(g["gameDate"])
    g["arenaId"] = pd.to_numeric(g["arenaId"], errors="coerce")
    g["winner"] = pd.to_numeric(g["winner"], errors="coerce")

    m = pd.read_csv(map_path)
    m["arenaId"] = pd.to_numeric(m["arenaId"], errors="coerce")

    mask = (
        (g["season"] >= 2007)
        & g["gameType"].isin(GAME_TYPES)
        & g["arenaId"].isin(set(m["arenaId"].dropna()))
        & g["winner"].notna()
    )
    g = g[mask].merge(
        m[["arenaId", "building", "city", "type"]], on="arenaId", how="left"
    )
    print(
        f"Qualifying games 2007+: {len(g):,} across "
        f"{g['building'].nunique()} buildings"
    )
    return g


def team_appearances(g: pd.DataFrame) -> pd.DataFrame:
    """Explode each game into two rows — the home team and the away team — each
    carrying that team's id/city/name, whether it won, and the building."""
    common = ["building", "city", "type", "gameType", "season", "winner"]
    home = g[common + ["hometeamId", "hometeamCity", "hometeamName"]].rename(
        columns={
            "hometeamId": "teamId",
            "hometeamCity": "teamCity",
            "hometeamName": "teamName",
        }
    )
    away = g[common + ["awayteamId", "awayteamCity", "awayteamName"]].rename(
        columns={
            "awayteamId": "teamId",
            "awayteamCity": "teamCity",
            "awayteamName": "teamName",
        }
    )
    apps = pd.concat([home, away], ignore_index=True)
    apps["teamId"] = pd.to_numeric(apps["teamId"], errors="coerce")
    apps = apps[apps["teamId"].notna()].copy()
    apps["won"] = (apps["teamId"] == apps["winner"]).astype("int64")
    return apps


def build_records(apps: pd.DataFrame) -> pd.DataFrame:
    # Current-branding city/name per team: take the most recent appearance.
    latest = (
        apps.sort_values("season")
        .groupby("teamId")
        .agg(teamCity=("teamCity", "last"), teamName=("teamName", "last"))
    )

    grp = apps.groupby(["teamId", "building", "gameType"], dropna=False).agg(
        city=("city", "first"),
        buildingType=("type", "first"),
        games=("won", "size"),
        wins=("won", "sum"),
        first_season=("season", "min"),
        last_season=("season", "max"),
    )
    grp["losses"] = grp["games"] - grp["wins"]
    grp["win_pct"] = (grp["wins"] / grp["games"]).round(3)
    grp = grp.reset_index().merge(latest, on="teamId", how="left")

    grp["teamId"] = grp["teamId"].astype("int64")
    out = grp[
        [
            "teamId",
            "teamCity",
            "teamName",
            "building",
            "city",
            "buildingType",
            "gameType",
            "games",
            "wins",
            "losses",
            "win_pct",
            "first_season",
            "last_season",
        ]
    ].sort_values(["teamName", "building", "gameType"]).reset_index(drop=True)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir")
    ap.add_argument("--out-dir", default="output")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    g = load_games(args.data_dir)
    apps = team_appearances(g)
    df = build_records(apps)

    out = os.path.join(args.out_dir, "team_building_records.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(
        f"Wrote {out} ({len(df):,} rows, {df['teamId'].nunique()} teams, "
        f"{df['building'].nunique()} buildings)"
    )


if __name__ == "__main__":
    main()
