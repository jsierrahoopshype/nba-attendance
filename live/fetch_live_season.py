#!/usr/bin/env python3
"""Fetch the NBA season in progress from the same Kaggle dataset the whole
section is built on (eoinamoore/historical-nba-data-and-player-box-scores,
updated daily, public: downloadable without an account or a key).

Runs in the daily GitHub Action. It downloads the dataset's Games.csv and
PlayerStatistics.csv, keeps only the games newer than the committed history
(output/ through 2025-26) and writes:

  data/live/<season_end>/games.csv    Games.csv rows of that season
  data/live/<season_end>/players.csv  PlayerStatistics.csv rows of that season

<season_end> is the season-ending year (2026-27 -> 2027), the key every
build_* script derives from the game date. Same columns as the Kaggle files, so
the rest of the pipeline treats the season exactly like the history. Files are
rewritten only when their content changed; a failed download leaves them as
they are (the build then runs on yesterday's copy).

Tests and local runs: --source-dir <folder with Games.csv + PlayerStatistics.csv>.
"""

import argparse
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIVE_DIR = os.path.join(ROOT, "data", "live")
DATASET = "eoinamoore/historical-nba-data-and-player-box-scores"
GAME_TYPES = ("Regular Season", "Playoffs", "Play-in Tournament", "Emirates NBA Cup", "NBA Cup",
              "NBA Emirates Cup")


def season_end(dates):
    d = pd.to_datetime(dates, errors="coerce")
    return d.dt.year + (d.dt.month >= 8).astype("Int64")


def base_max_season():
    s = pd.read_csv(os.path.join(ROOT, "output", "player_arena_records_by_season.csv"),
                    usecols=["season"], encoding="utf-8-sig")["season"]
    return int(s.max())


def download(name):
    """One file of the public Kaggle dataset -> local path (kagglehub caches it)."""
    import kagglehub
    return kagglehub.dataset_download(DATASET, path=name)


def write_if_changed(df, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = df.to_csv(index=False, lineterminator="\n")
    if os.path.exists(path) and open(path, encoding="utf-8").read() == data:
        return False
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(data)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", help="read Games.csv / PlayerStatistics.csv from here instead of Kaggle")
    ap.add_argument("--live-dir", default=LIVE_DIR)
    args = ap.parse_args()

    if args.source_dir:
        games_path = os.path.join(args.source_dir, "Games.csv")
        players_path = os.path.join(args.source_dir, "PlayerStatistics.csv")
    else:
        games_path, players_path = download("Games.csv"), download("PlayerStatistics.csv")

    floor = base_max_season() + 1          # first season NOT in the committed history
    g = pd.read_csv(games_path, low_memory=False)
    g["_se"] = season_end(g["gameDate"])
    g = g[(g["_se"] >= floor) & g["gameType"].isin(GAME_TYPES)]
    if g.empty:
        print(f"no games from {floor - 1}-{str(floor)[2:]} on in the dataset yet")
        return 0
    ids = set(g["gameId"])
    cols = [c for c in pd.read_csv(players_path, nrows=0).columns]
    parts = []
    for chunk in pd.read_csv(players_path, chunksize=200_000, low_memory=False):
        sub = chunk[chunk["gameId"].isin(ids)]
        if len(sub):
            parts.append(sub)
    p = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)
    p = p.merge(g[["gameId", "_se"]], on="gameId", how="left")

    summary = []
    changed = False
    for se, gs in g.groupby("_se"):
        gs = gs.drop(columns="_se").sort_values(["gameDate", "gameId"])
        ps = p[p["_se"] == se].drop(columns="_se").sort_values(["gameDate", "gameId", "personId"])
        d = os.path.join(args.live_dir, str(int(se)))
        a = write_if_changed(gs, os.path.join(d, "games.csv"))
        b = write_if_changed(ps, os.path.join(d, "players.csv"))
        rs = gs[gs["gameType"].isin(("Regular Season", "Playoffs"))]
        no_att = int((pd.to_numeric(rs["attendance"], errors="coerce").fillna(0) <= 0).sum())
        line = (f"Season {int(se) - 1}-{str(int(se))[2:]}: {len(gs)} games through {str(gs['gameDate'].max())[:10]}, "
                f"{len(ps)} player rows, {no_att} games without attendance"
                + ("" if (a or b) else " (no change)"))
        print(line)
        summary.append(line)
        changed = changed or bool(a or b)
    # Scheduled runs check Kaggle every couple of hours; with nothing new they stop here.
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write("".join(f"- {s}\n" for s in summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
