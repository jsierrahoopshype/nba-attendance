#!/usr/bin/env python3
"""Build attendance / draw / player-arena tables from the NBA data folder.

Usage:
    python build_attendance_tables.py <data_dir> [--player-filename NAME]
                                                 [--out-dir DIR]
                                                 [--chunksize N]
                                                 [--coverage-threshold F]

Reads:
    <data_dir>/Games.csv                 (small, loaded fully)
    <data_dir>/PlayerStatistics.csv      (380MB, read in chunks)

Writes (utf-8-sig) into --out-dir (default: current working directory):
    arena_baselines.csv        mean attendance per arena-season-gametype
    team_draw.csv              per visiting-team leave-one-out attendance delta
    player_draw.csv            per visiting-player (minutes>0) LOO attendance delta
    player_arena_records.csv   per player per arena W-L / points, split by gametype

Data notes (see the audit in the accompanying summary):
  * Attendance and arena *names* are only populated for the 2026 season, so the
    attendance-driven tables (baselines / team_draw / player_draw) and the
    arena records are effectively 2026-only given the current data. The logic is
    season-generic and will pick up any future season that carries attendance.
  * gameType distinguishes 'Regular Season' vs 'Playoffs' directly; those are the
    only two game types used here (kept separate throughout).
"""

import argparse
import os
import sys
from collections import defaultdict

import pandas as pd

# Only these game types participate; regular and playoffs are always kept apart.
GAME_TYPES = ("Regular Season", "Playoffs")


def derive_season(dt: pd.Series) -> pd.Series:
    """NBA season-ending year. A game played Aug..Dec belongs to the next year's
    season (e.g. Oct 2025 -> 2026); Jan..Jul belongs to that same calendar year."""
    return dt.dt.year + (dt.dt.month >= 8).astype("int64")


def load_games(games_path: str, coverage_threshold: float):
    """Load Games.csv and derive, per game, its arena-season-gametype baseline
    context: the group's attendance mean, whether the group qualifies (>threshold
    coverage), and the leave-one-out baseline for the game itself.

    Returns (games_df, baselines_df).
    """
    g = pd.read_csv(games_path, low_memory=False)
    g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
    g["season"] = derive_season(g["gameDate"])
    g["attendance"] = pd.to_numeric(g["attendance"], errors="coerce")
    g["has_att"] = g["attendance"].fillna(0) > 0

    # Restrict to the two game types we baseline, and to games with a known arena.
    mask = g["gameType"].isin(GAME_TYPES) & g["arenaName"].notna()
    gm = g[mask].copy()

    keys = ["arenaName", "season", "gameType"]
    # Group stats: total games, games with attendance, and sum of attendance
    # (over games that actually have attendance) for the mean / leave-one-out.
    grp = gm.groupby(keys, dropna=False).agg(
        games=("gameId", "size"),
        games_with_attendance=("has_att", "sum"),
        sum_attendance=("attendance", "sum"),  # NaN attendance is skipped by sum
    )
    grp["coverage_pct"] = 100.0 * grp["games_with_attendance"] / grp["games"]
    grp["mean_attendance"] = grp["sum_attendance"] / grp["games_with_attendance"]
    grp["qualifies"] = grp["coverage_pct"] > (coverage_threshold * 100.0)

    baselines = (
        grp[grp["qualifies"]]
        .reset_index()[
            [
                "arenaName",
                "season",
                "gameType",
                "games",
                "games_with_attendance",
                "coverage_pct",
                "mean_attendance",
            ]
        ]
        .sort_values(["season", "gameType", "arenaName"])
        .reset_index(drop=True)
    )
    baselines["coverage_pct"] = baselines["coverage_pct"].round(1)
    baselines["mean_attendance"] = baselines["mean_attendance"].round(1)

    # Attach group aggregates back to each game so we can compute a per-game
    # leave-one-out baseline: (group_sum - att_i) / (n_with_att - 1).
    grp_small = grp.reset_index()[
        keys + ["games_with_attendance", "sum_attendance", "qualifies"]
    ]
    gm = gm.merge(grp_small, on=keys, how="left")

    n = gm["games_with_attendance"]
    gm["loo_baseline"] = (gm["sum_attendance"] - gm["attendance"]) / (n - 1)
    # A usable per-game delta needs: qualifying group, this game has attendance,
    # and at least one *other* attended game in the group to average over.
    gm["loo_ok"] = gm["qualifies"] & gm["has_att"] & (n >= 2)
    gm.loc[~gm["loo_ok"], "loo_baseline"] = pd.NA

    games_ctx = gm[
        [
            "gameId",
            "season",
            "gameType",
            "arenaName",
            "attendance",
            "awayteamId",
            "awayteamCity",
            "awayteamName",
            "loo_baseline",
            "loo_ok",
        ]
    ].copy()
    return games_ctx, baselines


def build_team_draw(games_ctx: pd.DataFrame) -> pd.DataFrame:
    """Per visiting (away) team: mean/median/total of the game-level leave-one-out
    attendance delta. One delta per qualifying game, attributed to the visitor."""
    d = games_ctx[games_ctx["loo_ok"]].copy()
    d["delta"] = d["attendance"] - d["loo_baseline"]
    out = (
        d.groupby(["awayteamId", "awayteamCity", "awayteamName"])
        .agg(
            games=("delta", "size"),
            mean_delta=("delta", "mean"),
            median_delta=("delta", "median"),
            total_delta=("delta", "sum"),
        )
        .reset_index()
        .rename(
            columns={
                "awayteamId": "teamId",
                "awayteamCity": "teamCity",
                "awayteamName": "teamName",
            }
        )
        .sort_values("mean_delta", ascending=False)
        .reset_index(drop=True)
    )
    for c in ("mean_delta", "median_delta", "total_delta"):
        out[c] = out[c].round(1)
    return out


def build_player_tables(
    player_path: str, games_ctx: pd.DataFrame, chunksize: int
):
    """Stream PlayerStatistics in chunks, joining each chunk to game context.

    Accumulates two things:
      * player_draw: for visiting players (home==0) who logged minutes>0 in a
        qualifying game, the game's leave-one-out attendance delta.
      * player_arena_records: for every game a player logged minutes>0 at a known
        arena, W-L / points / career-high, split by game type.
    """
    game_cols = [
        "gameId",
        "arenaName",
        "gameType",
        "attendance",
        "loo_baseline",
        "loo_ok",
    ]
    gctx = games_ctx[game_cols]

    # player_draw accumulators: deltas per person (bounded to visiting 2026 games).
    draw_deltas = defaultdict(list)
    draw_name = {}

    # player_arena_records accumulators keyed by (personId, arenaName, gameType).
    rec = defaultdict(
        lambda: {"wins": 0, "losses": 0, "games": 0, "points": 0.0, "career_high": 0.0}
    )
    rec_name = {}

    usecols = [
        "personId",
        "firstName",
        "lastName",
        "gameId",
        "win",
        "home",
        "numMinutes",
        "points",
    ]

    reader = pd.read_csv(
        player_path, usecols=usecols, chunksize=chunksize, low_memory=False
    )
    total_rows = 0
    for chunk in reader:
        total_rows += len(chunk)
        chunk = chunk.merge(gctx, on="gameId", how="inner")
        if chunk.empty:
            continue

        chunk["numMinutes"] = pd.to_numeric(chunk["numMinutes"], errors="coerce")
        chunk["points"] = pd.to_numeric(chunk["points"], errors="coerce").fillna(0.0)
        chunk["home"] = pd.to_numeric(chunk["home"], errors="coerce")
        chunk["win"] = pd.to_numeric(chunk["win"], errors="coerce")
        played = chunk["numMinutes"] > 0
        chunk["playerName"] = (
            chunk["firstName"].fillna("").str.strip()
            + " "
            + chunk["lastName"].fillna("").str.strip()
        ).str.strip()

        # ---- player_draw: visiting players who played, in qualifying games ----
        dsub = chunk[played & (chunk["home"] == 0) & chunk["loo_ok"]].copy()
        if not dsub.empty:
            dsub["delta"] = dsub["attendance"] - dsub["loo_baseline"]
            for pid, name, delta in zip(
                dsub["personId"], dsub["playerName"], dsub["delta"]
            ):
                draw_deltas[pid].append(float(delta))
                draw_name[pid] = name

        # ---- player_arena_records: any game played at a known arena ----
        rsub = chunk[played & chunk["arenaName"].notna()].copy()
        if not rsub.empty:
            grouped = rsub.groupby(
                ["personId", "arenaName", "gameType"], dropna=False
            )
            for (pid, arena, gtype), sub in grouped:
                r = rec[(pid, arena, gtype)]
                r["wins"] += int((sub["win"] == 1).sum())
                r["losses"] += int((sub["win"] == 0).sum())
                r["games"] += len(sub)
                r["points"] += float(sub["points"].sum())
                r["career_high"] = max(r["career_high"], float(sub["points"].max()))
                rec_name[pid] = sub["playerName"].iloc[0]

    # ---- finalize player_draw ----
    draw_rows = []
    for pid, deltas in draw_deltas.items():
        s = pd.Series(deltas)
        draw_rows.append(
            {
                "personId": pid,
                "playerName": draw_name.get(pid, ""),
                "games": len(deltas),
                "mean_delta": round(s.mean(), 1),
                "median_delta": round(s.median(), 1),
                "total_delta": round(s.sum(), 1),
            }
        )
    player_draw = (
        pd.DataFrame(draw_rows)
        .sort_values("mean_delta", ascending=False)
        .reset_index(drop=True)
        if draw_rows
        else pd.DataFrame(
            columns=[
                "personId",
                "playerName",
                "games",
                "mean_delta",
                "median_delta",
                "total_delta",
            ]
        )
    )

    # ---- finalize player_arena_records ----
    rec_rows = []
    for (pid, arena, gtype), r in rec.items():
        games = r["games"]
        rec_rows.append(
            {
                "personId": pid,
                "playerName": rec_name.get(pid, ""),
                "arenaName": arena,
                "gameType": gtype,
                "games": games,
                "wins": r["wins"],
                "losses": r["losses"],
                "win_pct": round(r["wins"] / games, 3) if games else 0.0,
                "total_points": int(round(r["points"])),
                "ppg": round(r["points"] / games, 1) if games else 0.0,
                "career_high": int(round(r["career_high"])),
            }
        )
    player_arena_records = (
        pd.DataFrame(rec_rows)
        .sort_values(["playerName", "arenaName", "gameType"])
        .reset_index(drop=True)
        if rec_rows
        else pd.DataFrame(
            columns=[
                "personId",
                "playerName",
                "arenaName",
                "gameType",
                "games",
                "wins",
                "losses",
                "win_pct",
                "total_points",
                "ppg",
                "career_high",
            ]
        )
    )

    return player_draw, player_arena_records, total_rows


def build_player_arena_draw(
    player_path: str, games_ctx: pd.DataFrame, chunksize: int
) -> pd.DataFrame:
    """Additive table: per (arenaName, personId) among visiting players
    (home==0, minutes>0) in qualifying games, the mean leave-one-out attendance
    delta and the game count.

    This reuses the exact games_ctx / loo_baseline logic that feeds player_draw;
    the only difference is the grouping — per arena here, rather than league-wide
    per player. It does not read or mutate any of the other accumulators, so the
    existing outputs are unchanged.
    """
    game_cols = ["gameId", "arenaName", "attendance", "loo_baseline", "loo_ok"]
    gctx = games_ctx[game_cols]

    # (arenaName, personId) -> {deltas sum, count}; plus a name lookup.
    deltas = defaultdict(list)
    name = {}

    usecols = ["personId", "firstName", "lastName", "gameId", "home", "numMinutes"]
    reader = pd.read_csv(
        player_path, usecols=usecols, chunksize=chunksize, low_memory=False
    )
    for chunk in reader:
        chunk = chunk.merge(gctx, on="gameId", how="inner")
        if chunk.empty:
            continue
        chunk["numMinutes"] = pd.to_numeric(chunk["numMinutes"], errors="coerce")
        chunk["home"] = pd.to_numeric(chunk["home"], errors="coerce")
        sub = chunk[(chunk["numMinutes"] > 0) & (chunk["home"] == 0) & chunk["loo_ok"]]
        if sub.empty:
            continue
        sub = sub.copy()
        sub["delta"] = sub["attendance"] - sub["loo_baseline"]
        sub["playerName"] = (
            sub["firstName"].fillna("").str.strip()
            + " "
            + sub["lastName"].fillna("").str.strip()
        ).str.strip()
        for arena, pid, pname, delta in zip(
            sub["arenaName"], sub["personId"], sub["playerName"], sub["delta"]
        ):
            deltas[(arena, pid)].append(float(delta))
            name[pid] = pname

    rows = []
    for (arena, pid), ds in deltas.items():
        s = pd.Series(ds)
        rows.append(
            {
                "arenaName": arena,
                "personId": pid,
                "playerName": name.get(pid, ""),
                "games": len(ds),
                "mean_delta": round(s.mean(), 1),
            }
        )
    cols = ["arenaName", "personId", "playerName", "games", "mean_delta"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return (
        pd.DataFrame(rows)
        .sort_values(["arenaName", "mean_delta"], ascending=[True, False])
        .reset_index(drop=True)
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data_dir", help="Folder containing Games.csv and PlayerStatistics.csv")
    ap.add_argument(
        "--player-filename",
        default="PlayerStatistics.csv",
        help="Player stats filename inside data_dir (default: PlayerStatistics.csv)",
    )
    ap.add_argument("--out-dir", default=".", help="Where to write output CSVs (default: cwd)")
    ap.add_argument("--chunksize", type=int, default=100_000, help="Rows per chunk")
    ap.add_argument(
        "--coverage-threshold",
        type=float,
        default=0.70,
        help="Minimum attendance coverage (fraction) for an arena-season-gametype to qualify",
    )
    args = ap.parse_args()

    games_path = os.path.join(args.data_dir, "Games.csv")
    player_path = os.path.join(args.data_dir, args.player_filename)
    for p in (games_path, player_path):
        if not os.path.exists(p):
            sys.exit(f"ERROR: file not found: {p}")
    os.makedirs(args.out_dir, exist_ok=True)

    print(f"Loading games from {games_path} ...")
    games_ctx, baselines = load_games(games_path, args.coverage_threshold)
    print(
        f"  qualifying arena-season-gametype baselines: {len(baselines)}"
        f" | games with usable LOO delta: {int(games_ctx['loo_ok'].sum())}"
    )

    team_draw = build_team_draw(games_ctx)
    print(f"  team_draw rows (visiting teams): {len(team_draw)}")

    print(f"Streaming players from {player_path} (chunksize={args.chunksize}) ...")
    player_draw, player_arena_records, total_rows = build_player_tables(
        player_path, games_ctx, args.chunksize
    )
    print(
        f"  read {total_rows:,} player rows | player_draw: {len(player_draw)}"
        f" | player_arena_records: {len(player_arena_records)}"
    )

    # Additive: per (arena, player) visiting-draw table. Independent pass over
    # the same games_ctx; does not touch the accumulators above.
    player_arena_draw = build_player_arena_draw(
        player_path, games_ctx, args.chunksize
    )
    print(f"  player_arena_draw rows (arena x visiting player): {len(player_arena_draw)}")

    outputs = {
        "arena_baselines.csv": baselines,
        "team_draw.csv": team_draw,
        "player_draw.csv": player_draw,
        "player_arena_records.csv": player_arena_records,
        "player_arena_draw.csv": player_arena_draw,
    }
    for name, df in outputs.items():
        path = os.path.join(args.out_dir, name)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"  wrote {path} ({len(df)} rows)")

    print("Done.")


if __name__ == "__main__":
    main()