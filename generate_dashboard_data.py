#!/usr/bin/env python3
"""Generate static JSON for the GitHub Pages dashboard.

Reads (output/ CSVs, all utf-8-sig):
  player_arena_records_2007.csv   2007-2026 player x building x gameType records
  team_building_records.csv       2007-2026 team x building x gameType records
  arena_baselines.csv             arena attendance baselines (2026-only in practice)

Also recomputes the attendance-draw tables directly from the raw data so the
Attendance Draw tab can show the actual road-attendance and baseline components
(not just the delta). This reuses build_attendance_tables' leave-one-out logic;
it does not read or write any output/ CSV.

Writes JSON into docs/data/:
  draw.json                       team + player road-attendance draw (actual / baseline / diff)
  buildings/index.json            slim building list
  buildings/overview.json         Arena Records overview table (top scorer, winningest player/team, avg att.)
  buildings/{slug}.json           per-building leaderboards + all records
  players/index.json              personId, name, and light aggregates for the directory
  players/{personId}.json         one player's records at every building

Building metadata is derived from player_arena_records_2007.csv itself.
"""

import argparse
import csv
import json
import os
import re
import shutil
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(HERE, "output")
DATA_DIR = os.path.join(HERE, "docs", "data")

RECORDS_CSV = os.path.join(OUTPUT_DIR, "player_arena_records_2007.csv")
TEAM_BUILDING_CSV = os.path.join(OUTPUT_DIR, "team_building_records.csv")
ARENA_BASELINES_CSV = os.path.join(OUTPUT_DIR, "arena_baselines.csv")
CITY_RECORDS_CSV = os.path.join(OUTPUT_DIR, "city_records.csv")
ALLSTAR_CSV = os.path.join(OUTPUT_DIR, "allstar_players.csv")
PLAYER_ARENA_DRAW_CSV = os.path.join(OUTPUT_DIR, "player_arena_draw.csv")
TEAM_ARENA_DRAW_CSV = os.path.join(OUTPUT_DIR, "team_arena_draw.csv")
PLAYER_HOME_AWAY_CSV = os.path.join(OUTPUT_DIR, "player_arena_home_away.csv")
ARENA_MAPPING_CSV = os.path.join(HERE, "data", "arena_mapping.csv")

NEVER_HOME_MIN_AWAY = 3         # min visiting games for the "never played home" list
NEVER_HOME_TOP = 40

PLAYER_DRAW_MIN_GAMES = 20
WIN_PCT_MIN_GAMES = 15          # player leaderboards + overview winningest player
TEAM_WIN_PCT_MIN_GAMES = 20     # overview winningest team
LEADERBOARD_SIZE = 25
ARENA_DRAW_TOP = 40             # visiting-player draw rows kept per arena
BASELINE_SEASON_LABEL = "2026 season only"


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


# --------------------------------------------------------------------------- #
# records
# --------------------------------------------------------------------------- #
def load_records():
    records = []
    for r in read_csv(RECORDS_CSV):
        records.append({
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
        })
    return records


# --------------------------------------------------------------------------- #
# draw (recomputed from raw so we can split actual attendance vs baseline)
# --------------------------------------------------------------------------- #
def build_draw(data_dir, player_filename, chunksize):
    """Team- and player-level road-attendance draw with the actual mean
    attendance, the mean arena-season baseline, and their difference."""
    import pandas as pd
    import build_attendance_tables as bat

    games_path = os.path.join(data_dir, "Games.csv")
    games_ctx, _ = bat.load_games(games_path, 0.70)

    d = games_ctx[games_ctx["loo_ok"]].copy()

    # ---- teams: every visiting (away) team's road games ----
    tg = d.groupby(["awayteamId", "awayteamCity", "awayteamName"]).agg(
        games=("attendance", "size"),
        actual=("attendance", "mean"),
        baseline=("loo_baseline", "mean"),
    ).reset_index()
    tg["diff"] = tg["actual"] - tg["baseline"]
    teams = []
    for _, r in tg.sort_values("diff", ascending=False).iterrows():
        teams.append({
            "teamId": str(int(r["awayteamId"])),
            "teamCity": r["awayteamCity"],
            "teamName": r["awayteamName"],
            "games": int(r["games"]),
            "actual": round(float(r["actual"]), 1),
            "baseline": round(float(r["baseline"]), 1),
            "diff": round(float(r["diff"]), 1),
        })

    # ---- players: visiting players (home==0, minutes>0) in qualifying games ----
    player_path = os.path.join(data_dir, player_filename)
    gcols = games_ctx[["gameId", "attendance", "loo_baseline", "loo_ok"]]
    acc = defaultdict(lambda: {"att": 0.0, "base": 0.0, "n": 0})
    names = {}
    usecols = ["personId", "firstName", "lastName", "gameId", "home", "numMinutes"]
    for chunk in pd.read_csv(player_path, usecols=usecols, chunksize=chunksize, low_memory=False):
        chunk = chunk.merge(gcols, on="gameId", how="inner")
        if chunk.empty:
            continue
        chunk["numMinutes"] = pd.to_numeric(chunk["numMinutes"], errors="coerce")
        chunk["home"] = pd.to_numeric(chunk["home"], errors="coerce")
        sub = chunk[(chunk["numMinutes"] > 0) & (chunk["home"] == 0) & chunk["loo_ok"]]
        if sub.empty:
            continue
        nm = (sub["firstName"].fillna("").str.strip() + " " +
              sub["lastName"].fillna("").str.strip()).str.strip()
        for pid, name, att, base in zip(sub["personId"], nm, sub["attendance"], sub["loo_baseline"]):
            a = acc[pid]
            a["att"] += float(att)
            a["base"] += float(base)
            a["n"] += 1
            names[pid] = name

    players = []
    for pid, a in acc.items():
        n = a["n"]
        if n < PLAYER_DRAW_MIN_GAMES:
            continue
        actual = a["att"] / n
        baseline = a["base"] / n
        players.append({
            "personId": str(pid),
            "playerName": names.get(pid, ""),
            "games": n,
            "actual": round(actual, 1),
            "baseline": round(baseline, 1),
            "diff": round(actual - baseline, 1),
        })
    players.sort(key=lambda p: -p["diff"])

    return {
        "teams": teams,
        "players": players,
        "baseline_label": BASELINE_SEASON_LABEL,
    }


# --------------------------------------------------------------------------- #
# game-level counts, all-star flags, per-arena draw  (all additive)
# --------------------------------------------------------------------------- #
def load_game_counts(data_dir):
    """Distinct-gameId counts per canonical building and per city, derived from
    the raw game-level data (Games.csv joined to arena_mapping.csv).

    This is the single source of truth for "total games". It counts each game
    once (via unique gameId), never by summing a per-player/per-team games column
    — summing those inflates the total by the number of players/teams per game.
    """
    import pandas as pd

    games_path = os.path.join(data_dir, "Games.csv")
    g = pd.read_csv(games_path, low_memory=False)
    g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
    g["season"] = g["gameDate"].dt.year + (g["gameDate"].dt.month >= 8).astype("int64")
    g["arenaId"] = pd.to_numeric(g["arenaId"], errors="coerce")

    m = pd.read_csv(ARENA_MAPPING_CSV)
    m["arenaId"] = pd.to_numeric(m["arenaId"], errors="coerce")

    g = g[
        (g["season"] >= 2007)
        & g["gameType"].isin(("Regular Season", "Playoffs"))
        & g["arenaId"].isin(set(m["arenaId"].dropna()))
    ].merge(m[["arenaId", "building", "city"]], on="arenaId", how="left")

    building_games = g.groupby("building")["gameId"].nunique().astype(int).to_dict()
    city_games = g.groupby("city")["gameId"].nunique().astype(int).to_dict()
    return building_games, city_games


def load_allstar():
    """(ordered personId list, {personId: meta}) from allstar_players.csv."""
    ids, meta = [], {}
    if not os.path.exists(ALLSTAR_CSV):
        return ids, meta
    for r in read_csv(ALLSTAR_CSV):
        pid = r["personId"]
        ids.append(pid)
        meta[pid] = {
            "playerName": r["playerName"],
            "times_selected": as_int(r["times_selected"]),
            "first_year": r["first_year"],
            "last_year": r["last_year"],
        }
    return ids, meta


def load_arena_draw():
    """Wire player_arena_draw.csv (2026-only visiting-player draw per arena) two
    ways, keyed by the same slug the building pages use:

      by_slug[slug]   -> visiting players sorted by mean_delta desc (who drew most)
      by_player[pid]  -> that player's draw at every arena they visited
    """
    by_slug = defaultdict(list)
    by_player = defaultdict(list)
    if not os.path.exists(PLAYER_ARENA_DRAW_CSV):
        return {}, {}
    for r in read_csv(PLAYER_ARENA_DRAW_CSV):
        arena = r["arenaName"]
        slug = slugify(arena)
        by_slug[slug].append({
            "personId": r["personId"],
            "playerName": r["playerName"],
            "games": as_int(r["games"]),
            "mean_delta": as_float(r["mean_delta"]),
        })
        by_player[r["personId"]].append({
            "arena": arena,
            "slug": slug,
            "games": as_int(r["games"]),
            "mean_delta": as_float(r["mean_delta"]),
        })
    for s in by_slug:
        by_slug[s].sort(key=lambda x: -x["mean_delta"])
    for p in by_player:
        by_player[p].sort(key=lambda x: -x["mean_delta"])
    return dict(by_slug), dict(by_player)


def load_team_arena_draw():
    """team_arena_draw.csv (2026-only visiting-TEAM draw per arena) -> by slug."""
    by_slug = defaultdict(list)
    if not os.path.exists(TEAM_ARENA_DRAW_CSV):
        return {}
    for r in read_csv(TEAM_ARENA_DRAW_CSV):
        slug = slugify(r["arenaName"])
        by_slug[slug].append({
            "teamId": r["teamId"],
            "team": f'{r["teamCity"]} {r["teamName"]}'.strip(),
            "games": as_int(r["games"]),
            "mean_delta": as_float(r["mean_delta"]),
        })
    for s in by_slug:
        by_slug[s].sort(key=lambda x: -x["mean_delta"])
    return dict(by_slug)


def load_never_home():
    """player_arena_home_away.csv -> per building slug, the players who never
    played there for the home team (home_games == 0) with enough visits."""
    by_slug = defaultdict(list)
    if not os.path.exists(PLAYER_HOME_AWAY_CSV):
        return {}
    for r in read_csv(PLAYER_HOME_AWAY_CSV):
        if as_int(r["home_games"]) != 0:
            continue
        if as_int(r["away_games"]) < NEVER_HOME_MIN_AWAY:
            continue
        slug = slugify(r["arenaName"])
        by_slug[slug].append({
            "personId": r["personId"],
            "playerName": r["playerName"],
            "away_games": as_int(r["away_games"]),
            "games": as_int(r["games"]),
            "total_points": as_int(r["total_points"]),
            "first_season": as_int(r["first_season"]),
            "last_season": as_int(r["last_season"]),
        })
    for s in by_slug:
        by_slug[s].sort(key=lambda x: (-x["total_points"], -x["games"], x["playerName"]))
        del by_slug[s][NEVER_HOME_TOP:]
    return dict(by_slug)


def load_city_records():
    """City-level player records (mirrors load_records but keyed by city)."""
    records = []
    if not os.path.exists(CITY_RECORDS_CSV):
        return records
    for r in read_csv(CITY_RECORDS_CSV):
        records.append({
            "personId": r["personId"],
            "playerName": r["playerName"],
            "city": r["city"],
            "slug": slugify(r["city"]),
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
        })
    return records


# --------------------------------------------------------------------------- #
# buildings
# --------------------------------------------------------------------------- #
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


def leaderboards_for(recs, allstar_ids=None):
    if allstar_ids is not None:
        recs = [r for r in recs if r["personId"] in allstar_ids]
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
    return {
        "personId": rec["personId"],
        "playerName": rec["playerName"],
        "building": rec["building"],
        "slug": rec["slug"],
        "city": rec["city"],
        "city_slug": slugify(rec["city"]),
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


def load_baseline_avgs():
    """Games-weighted mean attendance per arena across its baseline rows
    (all baseline rows are 2026 in the current data)."""
    sums = defaultdict(lambda: {"wsum": 0.0, "games": 0})
    for r in read_csv(ARENA_BASELINES_CSV):
        g = as_int(r["games_with_attendance"]) or as_int(r["games"])
        s = sums[r["arenaName"]]
        s["wsum"] += as_float(r["mean_attendance"]) * g
        s["games"] += g
    return {a: (v["wsum"] / v["games"]) for a, v in sums.items() if v["games"]}


def load_winningest_teams():
    """Per building: the team with the best combined win_pct (min games)."""
    agg = defaultdict(lambda: defaultdict(lambda: {"games": 0, "wins": 0}))
    label = {}
    for r in read_csv(TEAM_BUILDING_CSV):
        b = r["building"]
        tid = r["teamId"]
        a = agg[b][tid]
        a["games"] += as_int(r["games"])
        a["wins"] += as_int(r["wins"])
        label[tid] = f'{r["teamCity"]} {r["teamName"]}'.strip()
    best = {}
    for b, teams in agg.items():
        cand = None
        for tid, a in teams.items():
            if a["games"] < TEAM_WIN_PCT_MIN_GAMES:
                continue
            wp = a["wins"] / a["games"]
            key = (wp, a["games"])
            if cand is None or key > cand[0]:
                cand = (key, {"teamId": tid, "team": label[tid],
                              "win_pct": round(wp, 3), "games": a["games"]})
        if cand:
            best[b] = cand[1]
    return best


def build_buildings(records, building_games, allstar_ids, draw_by_slug,
                    team_draw_by_slug=None, never_home_by_slug=None):
    team_draw_by_slug = team_draw_by_slug or {}
    never_home_by_slug = never_home_by_slug or {}
    by_building = {}
    for rec in records:
        by_building.setdefault(rec["building"], []).append(rec)

    # slim index. total_games is the distinct-gameId count for the building, not a
    # sum of per-player rows.
    index = []
    for name in sorted(by_building):
        recs = by_building[name]
        index.append({
            "name": name,
            "slug": slugify(name),
            "city": recs[0]["city"],
            "buildingType": recs[0]["buildingType"],
            "total_games": building_games.get(name, 0),
            "player_game_rows": sum(r["games"] for r in recs),
            "first_season": min(r["first_season"] for r in recs),
            "last_season": max(r["last_season"] for r in recs),
        })
    write_json(os.path.join(DATA_DIR, "buildings", "index.json"), index)

    # per-building detail
    for name, recs in by_building.items():
        slug = slugify(name)
        regular = [r for r in recs if r["gameType"] == "Regular Season"]
        playoffs = [r for r in recs if r["gameType"] == "Playoffs"]
        detail = {
            "name": name,
            "slug": slug,
            "city": recs[0]["city"],
            "city_slug": slugify(recs[0]["city"]),
            "buildingType": recs[0]["buildingType"],
            "total_games": building_games.get(name, 0),
            "player_game_rows": sum(r["games"] for r in recs),
            "first_season": min(r["first_season"] for r in recs),
            "last_season": max(r["last_season"] for r in recs),
            "leaderboards": {
                "regular": leaderboards_for(regular),
                "playoffs": leaderboards_for(playoffs),
            },
            "leaderboards_allstar": {
                "regular": leaderboards_for(regular, allstar_ids),
                "playoffs": leaderboards_for(playoffs, allstar_ids),
            },
            "records": [record_public(r) for r in recs],
            "visiting_draw": draw_by_slug.get(slug, [])[:ARENA_DRAW_TOP],
            "team_draw": team_draw_by_slug.get(slug, [])[:ARENA_DRAW_TOP],
            "never_home": never_home_by_slug.get(slug, []),
            "draw_label": BASELINE_SEASON_LABEL,
        }
        write_json(os.path.join(DATA_DIR, "buildings", slug + ".json"), detail)

    # overview table (Arena Records default view)
    baseline_avgs = load_baseline_avgs()
    winning_teams = load_winningest_teams()
    overview = []
    for name in sorted(by_building):
        recs = by_building[name]
        # combine gameTypes per player at this building
        pts = defaultdict(int)
        wl = defaultdict(lambda: {"games": 0, "wins": 0})
        pname = {}
        for r in recs:
            pts[r["personId"]] += r["total_points"]
            w = wl[r["personId"]]
            w["games"] += r["games"]
            w["wins"] += r["wins"]
            pname[r["personId"]] = r["playerName"]

        top_scorer = None
        if pts:
            pid = max(pts, key=lambda p: (pts[p], -1))
            top_scorer = {"personId": pid, "name": pname[pid], "points": pts[pid]}

        winningest_player = None
        cand = None
        for pid, w in wl.items():
            if w["games"] < WIN_PCT_MIN_GAMES:
                continue
            wp = w["wins"] / w["games"]
            key = (wp, w["games"])
            if cand is None or key > cand[0]:
                cand = (key, {"personId": pid, "name": pname[pid],
                              "win_pct": round(wp, 3), "games": w["games"]})
        if cand:
            winningest_player = cand[1]

        avg_att = baseline_avgs.get(name)
        overview.append({
            "slug": slugify(name),
            "name": name,
            "city": recs[0]["city"],
            "city_slug": slugify(recs[0]["city"]),
            "buildingType": recs[0]["buildingType"],
            "total_games": building_games.get(name, 0),
            "first_season": min(r["first_season"] for r in recs),
            "last_season": max(r["last_season"] for r in recs),
            "top_scorer": top_scorer,
            "winningest_player": winningest_player,
            "winningest_team": winning_teams.get(name),
            "avg_attendance": round(avg_att, 1) if avg_att is not None else None,
            "avg_attendance_label": BASELINE_SEASON_LABEL,
        })
    write_json(os.path.join(DATA_DIR, "buildings", "overview.json"), overview)
    return len(index)


# --------------------------------------------------------------------------- #
# players
# --------------------------------------------------------------------------- #
def build_players(records, draw_by_player=None):
    draw_by_player = draw_by_player or {}
    by_player = {}
    names = {}
    for rec in records:
        by_player.setdefault(rec["personId"], []).append(rec)
        names[rec["personId"]] = rec["playerName"]

    # index with light aggregates for the directory table (combined game types)
    index = []
    for pid in sorted(names, key=lambda p: names[p]):
        recs = by_player[pid]
        buildings = {r["building"] for r in recs}
        index.append({
            "personId": pid,
            "name": names[pid],
            "buildings": len(buildings),
            "games": sum(r["games"] for r in recs),
            "points": sum(r["total_points"] for r in recs),
        })
    write_json(os.path.join(DATA_DIR, "players", "index.json"), index)

    for pid, recs in by_player.items():
        recs_sorted = sorted(recs, key=lambda r: (r["building"], r["gameType"]))
        detail = {
            "personId": pid,
            "name": names[pid],
            "records": [record_public(r) for r in recs_sorted],
            "arena_draw": draw_by_player.get(pid, []),
            "draw_label": BASELINE_SEASON_LABEL,
        }
        write_json(os.path.join(DATA_DIR, "players", pid + ".json"), detail)

    return len(index)


# --------------------------------------------------------------------------- #
# cities  (additive: same leaderboard pattern as buildings, aggregated by city)
# --------------------------------------------------------------------------- #
def city_record_public(rec):
    return {
        "personId": rec["personId"],
        "playerName": rec["playerName"],
        "city": rec["city"],
        "slug": rec["slug"],
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


def build_cities(city_records, city_games, allstar_ids, buildings_by_city):
    """Write docs/data/cities/{index,overview,slug}.json from city_records.csv.

    buildings_by_city[city] -> sorted list of {name, slug} buildings in the city,
    so a city page can link out to its arenas."""
    if not city_records:
        return 0
    by_city = {}
    for rec in city_records:
        by_city.setdefault(rec["city"], []).append(rec)

    index = []
    for name in sorted(by_city):
        recs = by_city[name]
        index.append({
            "name": name,
            "slug": slugify(name),
            "buildings": buildings_by_city.get(name, []),
            "total_games": city_games.get(name, 0),
            "first_season": min(r["first_season"] for r in recs),
            "last_season": max(r["last_season"] for r in recs),
        })
    write_json(os.path.join(DATA_DIR, "cities", "index.json"), index)

    overview = []
    for name in sorted(by_city):
        recs = by_city[name]
        pts = defaultdict(int)
        wl = defaultdict(lambda: {"games": 0, "wins": 0})
        pname = {}
        for r in recs:
            pts[r["personId"]] += r["total_points"]
            w = wl[r["personId"]]
            w["games"] += r["games"]
            w["wins"] += r["wins"]
            pname[r["personId"]] = r["playerName"]
        top_scorer = None
        if pts:
            pid = max(pts, key=lambda p: (pts[p], -1))
            top_scorer = {"personId": pid, "name": pname[pid], "points": pts[pid]}
        winningest_player = None
        cand = None
        for pid, w in wl.items():
            if w["games"] < WIN_PCT_MIN_GAMES:
                continue
            wp = w["wins"] / w["games"]
            key = (wp, w["games"])
            if cand is None or key > cand[0]:
                cand = (key, {"personId": pid, "name": pname[pid],
                              "win_pct": round(wp, 3), "games": w["games"]})
        if cand:
            winningest_player = cand[1]
        overview.append({
            "slug": slugify(name),
            "name": name,
            "buildings": buildings_by_city.get(name, []),
            "total_games": city_games.get(name, 0),
            "first_season": min(r["first_season"] for r in recs),
            "last_season": max(r["last_season"] for r in recs),
            "top_scorer": top_scorer,
            "winningest_player": winningest_player,
        })
    write_json(os.path.join(DATA_DIR, "cities", "overview.json"), overview)

    for name, recs in by_city.items():
        slug = slugify(name)
        regular = [r for r in recs if r["gameType"] == "Regular Season"]
        playoffs = [r for r in recs if r["gameType"] == "Playoffs"]
        detail = {
            "name": name,
            "slug": slug,
            "buildings": buildings_by_city.get(name, []),
            "total_games": city_games.get(name, 0),
            "first_season": min(r["first_season"] for r in recs),
            "last_season": max(r["last_season"] for r in recs),
            "leaderboards": {
                "regular": leaderboards_for(regular),
                "playoffs": leaderboards_for(playoffs),
            },
            "leaderboards_allstar": {
                "regular": leaderboards_for(regular, allstar_ids),
                "playoffs": leaderboards_for(playoffs, allstar_ids),
            },
            "records": [city_record_public(r) for r in recs],
        }
        write_json(os.path.join(DATA_DIR, "cities", slug + ".json"), detail)
    return len(index)


# --------------------------------------------------------------------------- #
# combined search index  (players, teams, arenas, cities)
# --------------------------------------------------------------------------- #
def build_search(records, city_records):
    items = []

    # players
    seen = set()
    for r in records:
        pid = r["personId"]
        if pid in seen:
            continue
        seen.add(pid)
        items.append({"name": r["playerName"], "type": "player", "id": pid})

    # arenas
    for name in sorted({r["building"] for r in records}):
        rec = next(r for r in records if r["building"] == name)
        items.append({"name": name, "type": "arena", "slug": slugify(name),
                      "sub": rec["city"]})

    # cities
    for name in sorted({r["city"] for r in city_records}):
        items.append({"name": name, "type": "city", "slug": slugify(name)})

    # teams (from team_building_records — current branding, one per teamId)
    if os.path.exists(TEAM_BUILDING_CSV):
        teams = {}
        for r in read_csv(TEAM_BUILDING_CSV):
            teams[r["teamId"]] = f'{r["teamCity"]} {r["teamName"]}'.strip()
        for label in sorted(set(teams.values())):
            items.append({"name": label, "type": "team"})

    write_json(os.path.join(DATA_DIR, "search.json"), items)
    return len(items)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=os.path.join(HERE, "data"))
    ap.add_argument("--player-filename", default="PlayerStatistics_sample.csv")
    ap.add_argument("--chunksize", type=int, default=100_000)
    args = ap.parse_args()

    if os.path.isdir(DATA_DIR):
        shutil.rmtree(DATA_DIR)
    os.makedirs(DATA_DIR, exist_ok=True)

    records = load_records()
    print(f"Loaded {len(records)} player-arena records")

    # additive supporting tables
    building_games, city_games = load_game_counts(args.data_dir)
    allstar_ids_list, allstar_meta = load_allstar()
    allstar_ids = set(allstar_ids_list)
    draw_by_slug, draw_by_player = load_arena_draw()
    team_draw_by_slug = load_team_arena_draw()
    never_home_by_slug = load_never_home()
    city_records = load_city_records()

    write_json(os.path.join(DATA_DIR, "allstar.json"),
               {"personIds": allstar_ids_list, "players": allstar_meta})
    print(f"allstar.json: {len(allstar_ids_list)} All-Star players")

    draw = build_draw(args.data_dir, args.player_filename, args.chunksize)
    write_json(os.path.join(DATA_DIR, "draw.json"), draw)
    print(f"draw.json: {len(draw['teams'])} teams, "
          f"{len(draw['players'])} players (games>={PLAYER_DRAW_MIN_GAMES})")

    n_buildings = build_buildings(records, building_games, allstar_ids, draw_by_slug,
                                  team_draw_by_slug, never_home_by_slug)
    print(f"buildings: index + overview + {n_buildings} detail files")

    # city -> its buildings (for cross-links on the city page)
    buildings_by_city = defaultdict(dict)
    for r in records:
        buildings_by_city[r["city"]][r["building"]] = slugify(r["building"])
    buildings_by_city = {
        c: [{"name": n, "slug": s} for n, s in sorted(b.items())]
        for c, b in buildings_by_city.items()
    }
    n_cities = build_cities(city_records, city_games, allstar_ids, buildings_by_city)
    print(f"cities: index + overview + {n_cities} detail files")

    n_players = build_players(records, draw_by_player)
    print(f"players: index + {n_players} detail files")

    n_search = build_search(records, city_records)
    print(f"search.json: {n_search} entities")

    print("Done.")


if __name__ == "__main__":
    main()
