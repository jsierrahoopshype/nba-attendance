#!/usr/bin/env python3
"""Generate static JSON for the GitHub Pages dashboard from the output/ CSVs.

Reads:
  output/player_arena_records_2007.csv   (2007-2026 player x building x gameType records)
  output/team_draw.csv                   (attendance draw deltas per team)
  output/player_draw.csv                 (attendance draw deltas per player)
  output/arena_baselines.csv             (arena attendance baselines)

Writes JSON into docs/data/:
  draw.json
  buildings/index.json
  buildings/{slug}.json
  players/index.json
  players/{personId}.json

All building metadata is derived from player_arena_records_2007.csv itself.
"""

import csv
import json
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, "output")
DATA_DIR = os.path.join(HERE, "docs", "data")

RECORDS_CSV = os.path.join(OUTPUT_DIR, "player_arena_records_2007.csv")
TEAM_DRAW_CSV = os.path.join(OUTPUT_DIR, "team_draw.csv")
PLAYER_DRAW_CSV = os.path.join(OUTPUT_DIR, "player_draw.csv")
ARENA_BASELINES_CSV = os.path.join(OUTPUT_DIR, "arena_baselines.csv")

PLAYER_DRAW_MIN_GAMES = 20
WIN_PCT_MIN_GAMES = 15
LEADERBOARD_SIZE = 25


def slugify(name):
    """Lowercase-hyphenated building name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def as_int(v):
    return int(float(v)) if v not in (None, "") else 0


def as_float(v):
    return float(v) if v not in (None, "") else 0.0


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)


def load_records():
    """Return list of typed record dicts plus a computed slug per record."""
    records = []
    for r in read_csv(RECORDS_CSV):
        rec = {
            "personId": r["personId"],
            "playerName": r["playerName"],
            "building": r["building"],
            "slug": slugify(r["building"]),
            "city": r["city"],
            "buildingType": r["buildingType"],
            "gameType": r["gameType"],
            "games": as_int(r["games"]),
            "wins": as_int(r["wins"]),
            "losses": as_int(r["losses"]),
            "win_pct": as_float(r["win_pct"]),
            "total_points": as_int(r["total_points"]),
            "ppg": as_float(r["ppg"]),
            "career_high": as_int(r["career_high"]),
            "first_season": as_int(r["first_season"]),
            "last_season": as_int(r["last_season"]),
        }
        records.append(rec)
    return records


def build_draw():
    teams = []
    for r in read_csv(TEAM_DRAW_CSV):
        teams.append({
            "teamId": r["teamId"],
            "teamCity": r["teamCity"],
            "teamName": r["teamName"],
            "games": as_int(r["games"]),
            "mean_delta": as_float(r["mean_delta"]),
            "median_delta": as_float(r["median_delta"]),
            "total_delta": as_float(r["total_delta"]),
        })

    players = []
    for r in read_csv(PLAYER_DRAW_CSV):
        games = as_int(r["games"])
        if games < PLAYER_DRAW_MIN_GAMES:
            continue
        players.append({
            "personId": r["personId"],
            "playerName": r["playerName"],
            "games": games,
            "mean_delta": as_float(r["mean_delta"]),
            "median_delta": as_float(r["median_delta"]),
            "total_delta": as_float(r["total_delta"]),
        })

    baselines = []
    for r in read_csv(ARENA_BASELINES_CSV):
        baselines.append({
            "arenaName": r["arenaName"],
            "season": as_int(r["season"]),
            "gameType": r["gameType"],
            "games": as_int(r["games"]),
            "games_with_attendance": as_int(r["games_with_attendance"]),
            "coverage_pct": as_float(r["coverage_pct"]),
            "mean_attendance": as_float(r["mean_attendance"]),
        })

    return {"teams": teams, "players": players, "arena_baselines": baselines}


def leader_entry(rec):
    return {
        "personId": rec["personId"],
        "playerName": rec["playerName"],
        "games": rec["games"],
        "wins": rec["wins"],
        "losses": rec["losses"],
        "win_pct": rec["win_pct"],
        "total_points": rec["total_points"],
        "ppg": rec["ppg"],
        "career_high": rec["career_high"],
    }


def leaderboards_for(recs):
    """Build the four leaderboards for a single gameType's records."""
    by_points = sorted(recs, key=lambda r: (-r["total_points"], r["playerName"]))
    by_wins = sorted(recs, key=lambda r: (-r["wins"], r["playerName"]))
    win_pct_pool = [r for r in recs if r["games"] >= WIN_PCT_MIN_GAMES]
    by_win_pct = sorted(win_pct_pool, key=lambda r: (-r["win_pct"], -r["games"], r["playerName"]))
    by_career_high = sorted(recs, key=lambda r: (-r["career_high"], r["playerName"]))
    return {
        "total_points": [leader_entry(r) for r in by_points[:LEADERBOARD_SIZE]],
        "wins": [leader_entry(r) for r in by_wins[:LEADERBOARD_SIZE]],
        "win_pct": [leader_entry(r) for r in by_win_pct[:LEADERBOARD_SIZE]],
        "career_high": [leader_entry(r) for r in by_career_high[:LEADERBOARD_SIZE]],
    }


def record_public(rec):
    """Record shape used in building + player detail files (drops redundant keys per context)."""
    return {
        "personId": rec["personId"],
        "playerName": rec["playerName"],
        "building": rec["building"],
        "slug": rec["slug"],
        "city": rec["city"],
        "buildingType": rec["buildingType"],
        "gameType": rec["gameType"],
        "games": rec["games"],
        "wins": rec["wins"],
        "losses": rec["losses"],
        "win_pct": rec["win_pct"],
        "total_points": rec["total_points"],
        "ppg": rec["ppg"],
        "career_high": rec["career_high"],
        "first_season": rec["first_season"],
        "last_season": rec["last_season"],
    }


def build_buildings(records):
    by_building = {}
    for rec in records:
        by_building.setdefault(rec["building"], []).append(rec)

    index = []
    for name in sorted(by_building):
        recs = by_building[name]
        index.append({
            "name": name,
            "slug": slugify(name),
            "city": recs[0]["city"],
            "buildingType": recs[0]["buildingType"],
            "total_games": sum(r["games"] for r in recs),
            "first_season": min(r["first_season"] for r in recs),
            "last_season": max(r["last_season"] for r in recs),
        })

    write_json(os.path.join(DATA_DIR, "buildings", "index.json"), index)

    for name, recs in by_building.items():
        slug = slugify(name)
        regular = [r for r in recs if r["gameType"] == "Regular Season"]
        playoffs = [r for r in recs if r["gameType"] == "Playoffs"]
        detail = {
            "name": name,
            "slug": slug,
            "city": recs[0]["city"],
            "buildingType": recs[0]["buildingType"],
            "total_games": sum(r["games"] for r in recs),
            "first_season": min(r["first_season"] for r in recs),
            "last_season": max(r["last_season"] for r in recs),
            "leaderboards": {
                "regular": leaderboards_for(regular),
                "playoffs": leaderboards_for(playoffs),
            },
            "records": [record_public(r) for r in recs],
        }
        write_json(os.path.join(DATA_DIR, "buildings", slug + ".json"), detail)

    return len(index)


def build_players(records):
    by_player = {}
    names = {}
    for rec in records:
        by_player.setdefault(rec["personId"], []).append(rec)
        names[rec["personId"]] = rec["playerName"]

    index = [{"personId": pid, "name": names[pid]} for pid in sorted(names, key=lambda p: names[p])]
    write_json(os.path.join(DATA_DIR, "players", "index.json"), index)

    for pid, recs in by_player.items():
        recs_sorted = sorted(recs, key=lambda r: (r["building"], r["gameType"]))
        detail = {
            "personId": pid,
            "name": names[pid],
            "records": [record_public(r) for r in recs_sorted],
        }
        write_json(os.path.join(DATA_DIR, "players", pid + ".json"), detail)

    return len(index)


def main():
    if os.path.isdir(DATA_DIR):
        shutil.rmtree(DATA_DIR)
    os.makedirs(DATA_DIR, exist_ok=True)

    records = load_records()
    print(f"Loaded {len(records)} player-arena records")

    draw = build_draw()
    write_json(os.path.join(DATA_DIR, "draw.json"), draw)
    print(f"draw.json: {len(draw['teams'])} teams, "
          f"{len(draw['players'])} players (games>={PLAYER_DRAW_MIN_GAMES}), "
          f"{len(draw['arena_baselines'])} baselines")

    n_buildings = build_buildings(records)
    print(f"buildings: index + {n_buildings} detail files")

    n_players = build_players(records)
    print(f"players: index + {n_players} detail files")

    print("Done.")


if __name__ == "__main__":
    main()
