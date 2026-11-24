#!/usr/bin/env python3
"""Layer the season in progress (data/live/<season_end>/) on top of the
committed all-history outputs, then rebuild the site data.

Why a layer: the six build_*.py scripts need the full 380 MB Kaggle
PlayerStatistics.csv, which lives only on Jorge's machine. Their committed
outputs in output/ (through 2025-26) are the BASE. Every run:

  1. runs the same build_*.py scripts on a temporary data folder holding ONLY
     the live season's games and players, so every output is that season's
     contribution alone (the DELTA);
  2. season-keyed files: drops the live season's rows from the base and appends
     the delta's (idempotent, re-running never double counts);
     all-time files: base + delta, summed per key (games, wins, points...),
     max of career highs, min/max of first/last season;
  3. writes the combined files to a separate folder (output_live/, never
     committed), so output/ stays exactly as Jorge's local build left it;
  4. builds a combined Games.csv (history + live season) in a temp data folder
     and runs generate_dashboard_data.py on it, which rewrites docs/data/ and the
     entity pages.

A live season is applied only when it is newer than the newest season in the
base, so a local rebuild that already contains it is never counted twice.
"""

import argparse
import csv
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIVE_DIR = os.path.join(ROOT, "data", "live")
BASE_DIR = os.path.join(ROOT, "output")
COMBINED_DIR = os.path.join(ROOT, "output_live")

SUPPORT_FILES = ["arena_mapping.csv", "arena_mapping_pre2007.csv", "historical_attendance.csv",
                 "franchise_names.csv", "TeamHistories.csv", "Players.csv"]

# season-keyed outputs: (file, script that writes it)
SEASON_FILES = ["player_arena_records_by_season.csv", "team_building_records_by_season.csv",
                "arena_baselines_all.csv", "team_draw_by_season.csv", "player_draw_by_season.csv",
                "team_arena_draw_by_season.csv", "player_arena_draw_by_season.csv"]
SCRIPTS = [
    ["build_attendance_tables_historical.py"],
    ["build_arena_records_by_season.py", "--player-filename", "PlayerStatistics.csv"],
    ["build_arena_records_historical.py"],
    ["build_player_home_away_split.py", "--player-filename", "PlayerStatistics.csv"],
    ["build_team_building_records.py"],
    ["build_team_building_records_by_season.py"],
]


def live_seasons(live_dir):
    out = []
    for d in sorted(glob.glob(os.path.join(live_dir, "[0-9][0-9][0-9][0-9]"))):
        if os.path.exists(os.path.join(d, "games.csv")) and os.path.exists(os.path.join(d, "players.csv")):
            out.append(int(os.path.basename(d)))
    return out


def base_max_season(base_dir):
    s = pd.read_csv(os.path.join(base_dir, "player_arena_records_by_season.csv"),
                    usecols=["season"], encoding="utf-8-sig")["season"]
    return int(s.max())


def read(path):
    return pd.read_csv(path, encoding="utf-8-sig", low_memory=False)


written = set()


def write(df, path):
    df.to_csv(path, index=False, encoding="utf-8-sig")
    written.add(os.path.basename(path))


def splice(base, delta, seasons):
    keep = base[~base["season"].isin(seasons)]
    out = pd.concat([keep, delta], ignore_index=True) if len(delta) else keep
    return out[base.columns]


def merge_sum(base, delta, keys, sums, mins=(), maxs=(), latest=(), recompute=None):
    """base + delta per key: sum `sums`, min `mins`, max `maxs`; `latest` text
    columns take the delta's value when the key appears there."""
    if not len(delta):
        return base
    both = pd.concat([base.assign(_d=0), delta[base.columns].assign(_d=1)], ignore_index=True)
    agg = {c: "sum" for c in sums}
    agg.update({c: "min" for c in mins})
    agg.update({c: "max" for c in maxs})
    other = [c for c in base.columns if c not in keys and c not in agg]
    for c in other:
        agg[c] = "last"          # rows are ordered base-then-delta, so "last" prefers the delta
    out = both.sort_values("_d", kind="stable").groupby(keys, dropna=False, sort=False).agg(agg).reset_index()
    if recompute:
        recompute(out)
    return out[base.columns]


def recompute_rates(df):
    g = df["games"].where(df["games"] > 0)
    if "wins" in df:
        df["win_pct"] = (df["wins"] / g).round(3).fillna(0.0)
    if "total_points" in df and "ppg" in df:
        df["ppg"] = (df["total_points"] / g).round(1).fillna(0.0)


def build_delta(seasons, work):
    """Run the build scripts on the live season(s) only. Returns the delta dir."""
    data = os.path.join(work, "data")
    out = os.path.join(work, "delta")
    os.makedirs(data)
    os.makedirs(out)
    for f in SUPPORT_FILES:
        src = os.path.join(ROOT, "data", f)
        if os.path.exists(src):
            os.symlink(src, os.path.join(data, f))
    games = pd.concat([read(os.path.join(LIVE_DIR, str(s), "games.csv")) for s in seasons], ignore_index=True)
    players = pd.concat([read(os.path.join(LIVE_DIR, str(s), "players.csv")) for s in seasons], ignore_index=True)
    games.to_csv(os.path.join(data, "Games.csv"), index=False)
    players.to_csv(os.path.join(data, "PlayerStatistics.csv"), index=False)
    for cmd in SCRIPTS:
        r = subprocess.run([sys.executable, os.path.join(ROOT, cmd[0]), data, *cmd[1:], "--out-dir", out],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit(f"{cmd[0]} failed on the live season")
        print(f"  {cmd[0]}: ok")
    return out, games


def unknown_arenas(games):
    m = read(os.path.join(ROOT, "data", "arena_mapping.csv"))
    known = set(pd.to_numeric(m["arenaId"], errors="coerce").dropna().astype(int))
    rs = games[games["gameType"].isin(["Regular Season", "Playoffs"])]
    aid = pd.to_numeric(rs["arenaId"], errors="coerce")
    miss = rs[~aid.isin(known)]
    return (miss.groupby(["arenaId", "arenaName", "arenaCity"]).size().reset_index(name="games")
            .to_dict("records"))


def combined_data_dir(work, live_games):
    """data/ with Games.csv = history (minus any live gameIds) + live games."""
    d = os.path.join(work, "combined")
    os.makedirs(d)
    for f in os.listdir(os.path.join(ROOT, "data")):
        if f != "Games.csv" and f != "live":
            os.symlink(os.path.join(ROOT, "data", f), os.path.join(d, f))
    hist = read(os.path.join(ROOT, "data", "Games.csv"))
    live = live_games.copy()
    ids = set(pd.to_numeric(live["gameId"]).astype(int))
    hist = hist[~pd.to_numeric(hist["gameId"], errors="coerce").isin(ids)]
    for c in hist.columns:
        if c not in live:
            live[c] = ""
    pd.concat([hist, live[hist.columns]], ignore_index=True).to_csv(os.path.join(d, "Games.csv"), index=False)
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=COMBINED_DIR, help="where the combined outputs go")
    ap.add_argument("--skip-dashboard", action="store_true")
    args = ap.parse_args()

    seasons = live_seasons(LIVE_DIR)
    top = base_max_season(BASE_DIR)
    apply = [s for s in seasons if s > top]
    skipped = [s for s in seasons if s <= top]
    if skipped:
        print(f"WARNING: live season(s) {skipped} are already in the base outputs (newest base "
              f"season {top}); not layering them again.")
    work = tempfile.mkdtemp(prefix="live-season-")
    try:
        os.makedirs(args.out_dir, exist_ok=True)
        if apply:
            print(f"Live season(s) {apply} on top of base through {top}")
            delta_dir, live_games = build_delta(apply, work)
            for f in SEASON_FILES:
                base = read(os.path.join(BASE_DIR, f))
                delta = read(os.path.join(delta_dir, f))
                write(splice(base, delta, apply), os.path.join(args.out_dir, f))
            # all-time files
            f = "player_arena_records_2007.csv"
            write(merge_sum(read(os.path.join(BASE_DIR, f)), read(os.path.join(delta_dir, f)),
                            ["personId", "building", "gameType"],
                            ["games", "wins", "losses", "total_points"], ["first_season"],
                            ["career_high", "last_season"], recompute=recompute_rates),
                  os.path.join(args.out_dir, f))
            f = "player_arena_home_away.csv"
            write(merge_sum(read(os.path.join(BASE_DIR, f)), read(os.path.join(delta_dir, f)),
                            ["personId", "arenaName"],
                            ["home_games", "away_games", "games", "total_points"], ["first_season"],
                            ["last_season"]),
                  os.path.join(args.out_dir, f))
            f = "team_building_records.csv"
            tb = merge_sum(read(os.path.join(BASE_DIR, f)), read(os.path.join(delta_dir, f)),
                           ["teamId", "building", "gameType"], ["games", "wins", "losses"],
                           ["first_season"], ["last_season"], recompute=recompute_rates)
            # one current name per franchise id, as the builder does
            latest = read(os.path.join(delta_dir, f)).drop_duplicates("teamId", keep="last") \
                .set_index("teamId")[["teamCity", "teamName"]]
            for c in ("teamCity", "teamName"):
                tb[c] = tb["teamId"].map(latest[c]).fillna(tb[c])
            write(tb, os.path.join(args.out_dir, f))
            unk = unknown_arenas(live_games)
            if unk:
                msg = "Games at arenas missing from data/arena_mapping.csv (left out until mapped): " + \
                      "; ".join(f"{u['arenaName']} ({u['arenaCity']}, arenaId {u['arenaId']}, {u['games']} games)" for u in unk)
                print("WARNING: " + msg)
                gh = os.environ.get("GITHUB_STEP_SUMMARY")
                if gh:
                    open(gh, "a", encoding="utf-8").write(f"- {msg}\n")
        else:
            live_games = pd.DataFrame(columns=["gameId"])
        # every base file the live layer didn't rewrite goes along unchanged
        for f in glob.glob(os.path.join(BASE_DIR, "*.csv")):
            if os.path.basename(f) not in written:
                shutil.copy(f, os.path.join(args.out_dir, os.path.basename(f)))

        if args.skip_dashboard:
            return 0
        data = combined_data_dir(work, live_games) if len(live_games) else os.path.join(ROOT, "data")
        env = dict(os.environ, ATTENDANCE_OUTPUT_DIR=os.path.abspath(args.out_dir))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "generate_dashboard_data.py"), "--data-dir", data],
                           cwd=ROOT, env=env)
        if r.returncode != 0:
            raise SystemExit("generate_dashboard_data.py failed")
        # personId -> headshot stem (live/build_heads.py), for the pages' faces
        heads = os.path.join(ROOT, "data", "heads.json")
        if os.path.exists(heads):
            shutil.copy(heads, os.path.join(ROOT, "docs", "data", "heads.json"))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
