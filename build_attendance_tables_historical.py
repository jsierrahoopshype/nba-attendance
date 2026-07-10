#!/usr/bin/env python3
"""Season-spanning attendance / draw tables (2007-2026).

Additive companion to build_attendance_tables.py — that script and its outputs
are NOT modified. This one reuses the same leave-one-out logic but:

  * merges data/historical_attendance.csv (real per-game attendance for
    2007-2023, columns gameId,attendance) into the games context alongside
    Games.csv's native 2026 attendance, so every season that carries attendance
    is covered, not just 2026;
  * keeps the existing per-(arena, season, gameType) coverage threshold (default
    70%), so gap seasons — e.g. 2013 or partial 2021 — self-exclude when they
    lack enough attended games;
  * keeps `season` as a key on every output, and keys the per-arena draws by the
    canonical building (arena_mapping.csv) so all name eras of one arena combine.

Reads:
    <data_dir>/Games.csv
    <data_dir>/historical_attendance.csv   (gameId, attendance; 2007-2023)
    <data_dir>/arena_mapping.csv           (arenaId -> building)
    <data_dir>/PlayerStatistics.csv        (streamed in chunks)

Writes (utf-8-sig) into --out-dir:
    arena_baselines_all.csv          per arena-season-gametype qualifying baseline
    team_draw_by_season.csv          per season per visiting team, LOO delta
    player_draw_by_season.csv        per season per visiting player, LOO delta
    team_arena_draw_by_season.csv    per season per building per visiting team
    player_arena_draw_by_season.csv  per season per building per visiting player
"""

import argparse
import os
import sys
from collections import defaultdict

import pandas as pd

GAME_TYPES = ("Regular Season", "Playoffs")


def derive_season(dt: pd.Series) -> pd.Series:
    return dt.dt.year + (dt.dt.month >= 8).astype("int64")


def load_games(games_path, historical_path, mapping_path, coverage_threshold):
    """Games.csv + historical attendance + arena_mapping, with the same
    arena-season-gametype baseline / leave-one-out context the original builder
    computes. Returns (games_ctx, baselines).

    The baseline group key is (building, season, gameType). Pre-2026 games carry
    no arenaName in this data (only an arenaId), so the canonical building from
    arena_mapping is the arena identity that works across every season and
    naturally combines an arena's name eras. The leave-one-out math is otherwise
    identical to build_attendance_tables.py.
    """
    g = pd.read_csv(games_path, low_memory=False)
    g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
    g["season"] = derive_season(g["gameDate"])
    g["attendance"] = pd.to_numeric(g["attendance"], errors="coerce")

    # ---- merge historical attendance, preferring native (2026) figures ----
    if os.path.exists(historical_path):
        hist = pd.read_csv(historical_path, low_memory=False)
        hist = hist.rename(columns={"attendance": "hist_attendance"})
        hist["gameId"] = pd.to_numeric(hist["gameId"], errors="coerce")
        hist["hist_attendance"] = pd.to_numeric(hist["hist_attendance"], errors="coerce")
        g = g.merge(hist[["gameId", "hist_attendance"]], on="gameId", how="left")
        native = g["attendance"]
        g["attendance"] = native.where(native.fillna(0) > 0, g["hist_attendance"])
    else:
        print(f"NOTE: {historical_path} not found — using only Games.csv attendance.")

    g["has_att"] = g["attendance"].fillna(0) > 0

    # ---- canonical building (arena_mapping); fall back to raw arenaName ----
    if os.path.exists(mapping_path):
        m = pd.read_csv(mapping_path)
        m["arenaId"] = pd.to_numeric(m["arenaId"], errors="coerce")
        g["arenaId"] = pd.to_numeric(g["arenaId"], errors="coerce")
        g = g.merge(m[["arenaId", "building"]], on="arenaId", how="left")
    else:
        g["building"] = pd.NA
    g["building"] = g["building"].fillna(g["arenaName"])

    # Key on the canonical building (arenaName is null pre-2026). Require a known
    # building so neutral/unmapped sites don't form spurious baseline groups.
    mask = g["gameType"].isin(GAME_TYPES) & g["building"].notna()
    gm = g[mask].copy()

    keys = ["building", "season", "gameType"]
    grp = gm.groupby(keys, dropna=False).agg(
        games=("gameId", "size"),
        games_with_attendance=("has_att", "sum"),
        sum_attendance=("attendance", "sum"),
    )
    grp["coverage_pct"] = 100.0 * grp["games_with_attendance"] / grp["games"]
    grp["mean_attendance"] = grp["sum_attendance"] / grp["games_with_attendance"]
    grp["qualifies"] = grp["coverage_pct"] > (coverage_threshold * 100.0)

    baselines = (
        grp[grp["qualifies"]]
        .reset_index()[[
            "building", "season", "gameType", "games",
            "games_with_attendance", "coverage_pct", "mean_attendance",
        ]]
        .sort_values(["season", "gameType", "building"])
        .reset_index(drop=True)
    )
    baselines["coverage_pct"] = baselines["coverage_pct"].round(1)
    baselines["mean_attendance"] = baselines["mean_attendance"].round(1)

    grp_small = grp.reset_index()[keys + ["games_with_attendance", "sum_attendance", "qualifies"]]
    gm = gm.merge(grp_small, on=keys, how="left")
    n = gm["games_with_attendance"]
    gm["loo_baseline"] = (gm["sum_attendance"] - gm["attendance"]) / (n - 1)
    gm["loo_ok"] = gm["qualifies"] & gm["has_att"] & (n >= 2)
    gm.loc[~gm["loo_ok"], "loo_baseline"] = pd.NA

    games_ctx = gm[[
        "gameId", "season", "gameType", "arenaName", "building", "attendance",
        "awayteamId", "awayteamCity", "awayteamName", "loo_baseline", "loo_ok",
    ]].copy()
    seasons = sorted(int(s) for s in baselines["season"].unique())
    print(f"  qualifying arena-season-gametype baselines: {len(baselines)} "
          f"across seasons {seasons[:3]}..{seasons[-3:] if len(seasons) > 3 else ''} "
          f"| games with usable LOO delta: {int(games_ctx['loo_ok'].sum())}")
    return games_ctx, baselines


def build_team_draw_by_season(games_ctx):
    d = games_ctx[games_ctx["loo_ok"]].copy()
    d["delta"] = d["attendance"] - d["loo_baseline"]
    out = (
        d.groupby(["season", "awayteamId", "awayteamCity", "awayteamName"])
        .agg(games=("delta", "size"), mean_delta=("delta", "mean"),
             median_delta=("delta", "median"), total_delta=("delta", "sum"))
        .reset_index()
        .rename(columns={"awayteamId": "teamId", "awayteamCity": "teamCity",
                         "awayteamName": "teamName"})
        .sort_values(["season", "mean_delta"], ascending=[True, False])
        .reset_index(drop=True)
    )
    out["teamId"] = out["teamId"].astype("int64")
    for c in ("mean_delta", "median_delta", "total_delta"):
        out[c] = out[c].round(1)
    return out


def build_team_arena_draw_by_season(games_ctx):
    d = games_ctx[games_ctx["loo_ok"]].copy()
    d["delta"] = d["attendance"] - d["loo_baseline"]
    out = (
        d.groupby(["season", "building", "awayteamId", "awayteamCity", "awayteamName"])
        .agg(games=("delta", "size"), mean_delta=("delta", "mean"))
        .reset_index()
        .rename(columns={"awayteamId": "teamId", "awayteamCity": "teamCity",
                         "awayteamName": "teamName"})
        .sort_values(["season", "building", "mean_delta"], ascending=[True, True, False])
        .reset_index(drop=True)
    )
    out["teamId"] = out["teamId"].astype("int64")
    out["mean_delta"] = out["mean_delta"].round(1)
    return out


def build_player_draws(player_path, games_ctx, chunksize):
    """One streaming pass over PlayerStatistics producing both the league-wide
    per-season player draw and the per-building per-season player draw."""
    gctx = games_ctx[games_ctx["loo_ok"]][
        ["gameId", "season", "building", "attendance", "loo_baseline"]
    ].copy()

    league = defaultdict(list)   # (season, pid) -> [deltas]
    arena = defaultdict(list)    # (season, building, pid) -> [deltas]
    name = {}

    usecols = ["personId", "firstName", "lastName", "gameId", "home", "numMinutes"]
    for chunk in pd.read_csv(player_path, usecols=usecols, chunksize=chunksize, low_memory=False):
        chunk = chunk.merge(gctx, on="gameId", how="inner")
        if chunk.empty:
            continue
        chunk["numMinutes"] = pd.to_numeric(chunk["numMinutes"], errors="coerce")
        chunk["home"] = pd.to_numeric(chunk["home"], errors="coerce")
        sub = chunk[(chunk["numMinutes"] > 0) & (chunk["home"] == 0)]
        if sub.empty:
            continue
        sub = sub.copy()
        sub["delta"] = sub["attendance"] - sub["loo_baseline"]
        sub["playerName"] = (
            sub["firstName"].fillna("").str.strip() + " "
            + sub["lastName"].fillna("").str.strip()
        ).str.strip()
        for row in sub.itertuples(index=False):
            pid = str(row.personId)
            s = int(row.season)
            d = float(row.delta)
            league[(s, pid)].append(d)
            arena[(s, row.building, pid)].append(d)
            name[pid] = row.playerName

    league_rows = []
    for (s, pid), ds in league.items():
        ser = pd.Series(ds)
        league_rows.append({
            "season": s, "personId": pid, "playerName": name.get(pid, ""),
            "games": len(ds), "mean_delta": round(ser.mean(), 1),
            "median_delta": round(ser.median(), 1), "total_delta": round(ser.sum(), 1),
        })
    player_draw = pd.DataFrame(league_rows,
        columns=["season", "personId", "playerName", "games", "mean_delta",
                 "median_delta", "total_delta"])
    if len(player_draw):
        player_draw = player_draw.sort_values(
            ["season", "mean_delta"], ascending=[True, False]).reset_index(drop=True)

    arena_rows = []
    for (s, bld, pid), ds in arena.items():
        ser = pd.Series(ds)
        arena_rows.append({
            "season": s, "building": bld, "personId": pid,
            "playerName": name.get(pid, ""), "games": len(ds),
            "mean_delta": round(ser.mean(), 1),
        })
    player_arena_draw = pd.DataFrame(arena_rows,
        columns=["season", "building", "personId", "playerName", "games", "mean_delta"])
    if len(player_arena_draw):
        player_arena_draw = player_arena_draw.sort_values(
            ["season", "building", "mean_delta"], ascending=[True, True, False]
        ).reset_index(drop=True)
    return player_draw, player_arena_draw


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir")
    ap.add_argument("--player-filename", default="PlayerStatistics.csv")
    ap.add_argument("--historical-filename", default="historical_attendance.csv")
    ap.add_argument("--mapping-filename", default="arena_mapping.csv")
    ap.add_argument("--out-dir", default="output")
    ap.add_argument("--chunksize", type=int, default=100_000)
    ap.add_argument("--coverage-threshold", type=float, default=0.70)
    args = ap.parse_args()

    games_path = os.path.join(args.data_dir, "Games.csv")
    player_path = os.path.join(args.data_dir, args.player_filename)
    historical_path = os.path.join(args.data_dir, args.historical_filename)
    mapping_path = os.path.join(args.data_dir, args.mapping_filename)
    for p in (games_path, player_path):
        if not os.path.exists(p):
            sys.exit(f"ERROR: file not found: {p}")
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"Loading games from {games_path} (+ historical attendance) ...")
    games_ctx, baselines = load_games(games_path, historical_path, mapping_path,
                                      args.coverage_threshold)

    team_draw = build_team_draw_by_season(games_ctx)
    team_arena_draw = build_team_arena_draw_by_season(games_ctx)
    print(f"  team_draw_by_season rows: {len(team_draw)} | "
          f"team_arena_draw_by_season rows: {len(team_arena_draw)}")

    print(f"Streaming players from {player_path} (chunksize={args.chunksize}) ...")
    player_draw, player_arena_draw = build_player_draws(
        player_path, games_ctx, args.chunksize)
    print(f"  player_draw_by_season rows: {len(player_draw)} | "
          f"player_arena_draw_by_season rows: {len(player_arena_draw)}")

    outputs = {
        "arena_baselines_all.csv": baselines,
        "team_draw_by_season.csv": team_draw,
        "player_draw_by_season.csv": player_draw,
        "team_arena_draw_by_season.csv": team_arena_draw,
        "player_arena_draw_by_season.csv": player_arena_draw,
    }
    for name, df in outputs.items():
        path = os.path.join(args.out_dir, name)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  wrote {path} ({len(df)} rows)")
    print("Done.")


if __name__ == "__main__":
    main()
