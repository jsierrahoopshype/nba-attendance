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
RECORDS_BY_SEASON_CSV = os.path.join(OUTPUT_DIR, "player_arena_records_by_season.csv")
ALLSTAR_CSV = os.path.join(OUTPUT_DIR, "allstar_players.csv")
PLAYER_ARENA_DRAW_CSV = os.path.join(OUTPUT_DIR, "player_arena_draw.csv")
TEAM_ARENA_DRAW_CSV = os.path.join(OUTPUT_DIR, "team_arena_draw.csv")
# Season-keyed draws from build_attendance_tables_historical.py (2007-2026).
PLAYER_ARENA_DRAW_BY_SEASON_CSV = os.path.join(OUTPUT_DIR, "player_arena_draw_by_season.csv")
TEAM_ARENA_DRAW_BY_SEASON_CSV = os.path.join(OUTPUT_DIR, "team_arena_draw_by_season.csv")
PLAYER_DRAW_BY_SEASON_CSV = os.path.join(OUTPUT_DIR, "player_draw_by_season.csv")
TEAM_DRAW_BY_SEASON_CSV = os.path.join(OUTPUT_DIR, "team_draw_by_season.csv")
ARENA_BASELINES_ALL_CSV = os.path.join(OUTPUT_DIR, "arena_baselines_all.csv")
PLAYER_HOME_AWAY_CSV = os.path.join(OUTPUT_DIR, "player_arena_home_away.csv")
ARENA_MAPPING_CSV = os.path.join(HERE, "data", "arena_mapping.csv")

NEVER_HOME_MIN_AWAY = 3         # min visiting games for the "never played home" list
NEVER_HOME_TOP = 40
BIGGEST_DRAW_MIN_GAMES = 15     # min games for the per-arena biggest-draw card
DRAW_KING_MIN_GAMES = 100       # min road games for the all-time draw-kings box
SEASON_DRAW_MIN_GAMES = 15      # min road games for the biggest-draws-this-season box
SELLOUT_WITHIN = 0.005          # sellout = attendance >= 99.5% of effective capacity
SELLOUT_CAPACITY_PCTL = 0.95    # effective capacity = this percentile (robust vs. the
                                # max, which one SRO/miscount outlier poisons)
FRONTPAGE_ROWS = 10

PLAYER_DRAW_MIN_GAMES = 20
WIN_PCT_MIN_GAMES = 15          # player leaderboards + overview winningest player
TEAM_WIN_PCT_MIN_GAMES = 20     # overview winningest team
LEADERBOARD_SIZE = 25
BASELINE_SEASON_LABEL = "2026 season only"


def slugify(name):
    """Lowercase-hyphenated building name."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _season_label(y):
    """Season-ending year -> NBA label, e.g. 2026 -> '2025-26'."""
    return f"{y - 1}-{str(y)[-2:]}"


def draw_range_label(seasons):
    """Human label for the seasons carrying draw data, e.g. '2006-07 – 2025-26'."""
    if not seasons:
        return BASELINE_SEASON_LABEL
    lo, hi = min(seasons), max(seasons)
    return _season_label(lo) if lo == hi else f"{_season_label(lo)} – {_season_label(hi)}"


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
def _draw_aggregate(rows, id_field, extra_fields, min_games):
    """Games-weighted career aggregate of season draw rows, keyed by id_field.
    total_delta = sum of per-game deltas across the career; mean_delta = that
    total / games. Returns rows with games >= min_games, sorted by total desc."""
    agg = defaultdict(lambda: {"games": 0, "total": 0.0, "extra": {}})
    for r in rows:
        a = agg[r[id_field]]
        a["games"] += as_int(r["games"])
        a["total"] += as_float(r["total_delta"])
        a["extra"] = {k: r[k] for k in extra_fields}
    out = []
    for _id, a in agg.items():
        if a["games"] < min_games:
            continue
        out.append({id_field: _id, **a["extra"], "games": a["games"],
                    "mean_delta": round(a["total"] / a["games"], 1),
                    "total_delta": round(a["total"], 1)})
    out.sort(key=lambda x: -x["total_delta"])
    return out


def _draw_by_season(rows, id_field, extra_fields, min_games):
    """Group season draw rows into {str(season): [row, ...]}, min_games applied
    per season, each season's list sorted by per-game delta desc."""
    by_season = defaultdict(list)
    for r in rows:
        if as_int(r["games"]) < min_games:
            continue
        by_season[as_int(r["season"])].append({
            id_field: r[id_field], **{k: r[k] for k in extra_fields},
            "games": as_int(r["games"]),
            "mean_delta": round(as_float(r["mean_delta"]), 1),
            "total_delta": round(as_float(r["total_delta"]), 1)})
    for s in by_season:
        by_season[s].sort(key=lambda x: -x["mean_delta"])
    return {str(s): by_season[s] for s in sorted(by_season, reverse=True)}


def build_draw(data_dir, player_filename, chunksize):
    """Season-keyed road-attendance draw for the Attendance Draw tab.

    Emits a career "all" aggregate (default view) plus a per-season breakdown for
    both visiting players and visiting teams, from the season-keyed league draw
    CSVs (build_attendance_tables_historical). Players are capped to
    PLAYER_DRAW_MIN_GAMES road games in whichever scope is shown; teams show all.
    Falls back to the legacy 2026-only recompute only when the season CSVs are
    absent, so an un-refreshed output/ dir still renders."""
    prows = _read_by_season(PLAYER_DRAW_BY_SEASON_CSV,
                            ["season", "games", "mean_delta", "total_delta"])
    trows = _read_by_season(TEAM_DRAW_BY_SEASON_CSV,
                            ["season", "games", "mean_delta", "total_delta"])
    if prows or trows:
        p_extra, t_extra = ["playerName"], ["teamCity", "teamName"]
        seasons = sorted({as_int(r["season"]) for r in prows} |
                         {as_int(r["season"]) for r in trows}, reverse=True)
        return {
            "seasons": seasons,
            "players": {
                "all": _draw_aggregate(prows, "personId", p_extra, PLAYER_DRAW_MIN_GAMES),
                "by_season": _draw_by_season(prows, "personId", p_extra, PLAYER_DRAW_MIN_GAMES),
            },
            "teams": {
                "all": _draw_aggregate(trows, "teamId", t_extra, 0),
                "by_season": _draw_by_season(trows, "teamId", t_extra, 0),
            },
        }
    return _build_draw_2026(data_dir, player_filename, chunksize)


def _build_draw_2026(data_dir, player_filename, chunksize):
    """Legacy 2026-only draw (fallback): flat team/player lists wrapped into the
    season-keyed shape so the frontend has a single contract."""
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
        games = int(r["games"])
        diff = round(float(r["diff"]), 1)
        teams.append({
            "teamId": str(int(r["awayteamId"])),
            "teamCity": r["awayteamCity"],
            "teamName": r["awayteamName"],
            "games": games,
            "mean_delta": diff,
            "total_delta": round(diff * games, 1),
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
        diff = round(a["att"] / n - a["base"] / n, 1)
        players.append({
            "personId": str(pid),
            "playerName": names.get(pid, ""),
            "games": n,
            "mean_delta": diff,
            "total_delta": round(diff * n, 1),
        })
    players.sort(key=lambda p: -p["total_delta"])

    return {
        "seasons": [],
        "players": {"all": players, "by_season": {}},
        "teams": {"all": teams, "by_season": {}},
    }


# --------------------------------------------------------------------------- #
# game-level counts, all-star flags, per-arena draw  (all additive)
# --------------------------------------------------------------------------- #
def load_game_counts(data_dir):
    """Distinct-gameId counts per canonical building and per city, derived from
    the raw game-level data resolved through arena_resolver.

    This is the single source of truth for "total games". It counts each game
    once (via unique gameId), never by summing a per-player/per-team games column.
    Resolution goes through arena_resolver so pre-2007 buildings (e.g. Chicago
    Stadium) count correctly instead of showing 0 — the old arenaId-only join
    dropped every 1980-2006 game.
    """
    import pandas as pd
    from arena_resolver import load_arena_resolver, PRE2007_SEASON_LO

    games_path = os.path.join(data_dir, "Games.csv")
    g = pd.read_csv(games_path, low_memory=False)
    g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
    g["season"] = g["gameDate"].dt.year + (g["gameDate"].dt.month >= 8).astype("int64")

    resolver = load_arena_resolver(data_dir)
    floor = PRE2007_SEASON_LO if resolver.has_pre2007 else 2007
    g = g[(g["season"] >= floor) & g["gameType"].isin(("Regular Season", "Playoffs"))].copy()
    g = resolver.attach(g)
    g = g[g["building"].notna()]

    building_games = g.groupby("building")["gameId"].nunique().astype(int).to_dict()
    city_games = g.groupby("city")["gameId"].nunique().astype(int).to_dict()

    # Game-derived season ranges (1980-2026 via arena_resolver) — the actual span a
    # building/city hosted NBA games, independent of what seasons the player-record
    # sample happens to cover. City pages and arena pages label their range from
    # this so e.g. Atlanta reads 1980-2026 (The Omni's 1980-1997 games included),
    # not 2007-2026 (Fix F).
    def _ranges(col):
        gb = g.groupby(col)["season"].agg(["min", "max"])
        return {k: {"first_season": int(v["min"]), "last_season": int(v["max"])}
                for k, v in gb.iterrows()}
    building_range = _ranges("building")
    city_range = _ranges("city")
    return building_games, city_games, building_range, city_range


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


def load_arena_draw(slug_to_city=None):
    """Wire player_arena_draw.csv (2026-only visiting-player draw per arena) two
    ways, keyed by the same slug the building pages use:

      by_slug[slug]   -> visiting players sorted by mean_delta desc (who drew most)
      by_player[pid]  -> that player's draw at every arena they visited
    """
    slug_to_city = slug_to_city or {}
    by_slug = defaultdict(list)
    by_player = defaultdict(list)
    if not os.path.exists(PLAYER_ARENA_DRAW_CSV):
        return {}, {}
    for r in read_csv(PLAYER_ARENA_DRAW_CSV):
        arena = r["arenaName"]
        slug = slugify(arena)
        city = slug_to_city.get(slug, "")
        by_slug[slug].append({
            "personId": r["personId"],
            "playerName": r["playerName"],
            "games": as_int(r["games"]),
            "mean_delta": as_float(r["mean_delta"]),
        })
        by_player[r["personId"]].append({
            "arena": arena,
            "slug": slug,
            "city": city,
            "city_slug": slugify(city) if city else "",
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


def load_home_away():
    """player_arena_home_away.csv -> per building slug, a personId -> {home_games,
    away_games} map. Merged into each leaderboard/record row on the arena page so
    the home/away split (and the "never played home" cases, home_games == 0) show
    inline rather than as a standalone section."""
    by_slug = defaultdict(dict)
    if not os.path.exists(PLAYER_HOME_AWAY_CSV):
        return {}
    for r in read_csv(PLAYER_HOME_AWAY_CSV):
        slug = slugify(r["arenaName"])
        by_slug[slug][r["personId"]] = {
            "home_games": as_int(r["home_games"]),
            "away_games": as_int(r["away_games"]),
        }
    return {s: dict(m) for s, m in by_slug.items()}


def _weighted_draw(rows, key_field):
    """Collapse per-arena draw rows to one row per key, games-weighted mean_delta."""
    agg = {}
    for r in rows:
        k = r[key_field]
        a = agg.setdefault(k, {"row": r, "wsum": 0.0, "games": 0})
        a["wsum"] += r["mean_delta"] * r["games"]
        a["games"] += r["games"]
    out = []
    for k, a in agg.items():
        g = a["games"]
        merged = dict(a["row"])
        merged["games"] = g
        merged["mean_delta"] = round(a["wsum"] / g, 1) if g else 0.0
        out.append(merged)
    out.sort(key=lambda x: -x["mean_delta"])
    return out


def load_city_draw(slug_to_city):
    """Aggregate per-arena player/team draw up to the city level (games-weighted
    mean_delta across the city's buildings). Returns (player_by_city, team_by_city)."""
    player_rows = defaultdict(list)
    team_rows = defaultdict(list)
    if os.path.exists(PLAYER_ARENA_DRAW_CSV):
        for r in read_csv(PLAYER_ARENA_DRAW_CSV):
            city = slug_to_city.get(slugify(r["arenaName"]))
            if not city:
                continue
            player_rows[city].append({
                "personId": r["personId"], "playerName": r["playerName"],
                "games": as_int(r["games"]), "mean_delta": as_float(r["mean_delta"]),
            })
    if os.path.exists(TEAM_ARENA_DRAW_CSV):
        for r in read_csv(TEAM_ARENA_DRAW_CSV):
            city = slug_to_city.get(slugify(r["arenaName"]))
            if not city:
                continue
            team_rows[city].append({
                "teamId": r["teamId"], "team": f'{r["teamCity"]} {r["teamName"]}'.strip(),
                "games": as_int(r["games"]), "mean_delta": as_float(r["mean_delta"]),
            })
    player_by_city = {c: _weighted_draw(rows, "personId") for c, rows in player_rows.items()}
    team_by_city = {c: _weighted_draw(rows, "teamId") for c, rows in team_rows.items()}
    return player_by_city, team_by_city


# --------------------------------------------------------------------------- #
# season-keyed draws (build_attendance_tables_historical.py, 2007-2026)
# --------------------------------------------------------------------------- #
def _season_draw_map(rows_by_season, key_field):
    """{season_str -> [rows]} -> {"all": weighted-across-seasons, season_str: sorted}.
    Each per-season list is sorted by mean_delta desc; "all" is the games-weighted
    mean per key across every season."""
    out = {}
    all_rows = []
    for s, rows in rows_by_season.items():
        out[s] = sorted(rows, key=lambda x: -x["mean_delta"])
        all_rows.extend(rows)
    out["all"] = _weighted_draw(all_rows, key_field)
    return out


def load_arena_draw_by_season(slug_to_city):
    """Season-keyed per-arena and per-city draws. Returns a dict of:
        building[slug] -> {"players": season-map, "teams": season-map, "seasons": [..]}
        city[name]     -> {"players": season-map, "teams": season-map, "seasons": [..]}
    Empty ({}) when the by-season CSVs are absent, so the caller falls back to the
    single-season draw fields."""
    have = (os.path.exists(PLAYER_ARENA_DRAW_BY_SEASON_CSV)
            or os.path.exists(TEAM_ARENA_DRAW_BY_SEASON_CSV))
    if not have:
        return {}, {}

    bp = defaultdict(lambda: defaultdict(list))   # slug -> season -> [player rows]
    bt = defaultdict(lambda: defaultdict(list))   # slug -> season -> [team rows]
    cp = defaultdict(lambda: defaultdict(list))   # city -> season -> [player rows]
    ct = defaultdict(lambda: defaultdict(list))   # city -> season -> [team rows]

    if os.path.exists(PLAYER_ARENA_DRAW_BY_SEASON_CSV):
        for r in read_csv(PLAYER_ARENA_DRAW_BY_SEASON_CSV):
            slug = slugify(r["building"])
            s = str(as_int(r["season"]))
            row = {"personId": r["personId"], "playerName": r["playerName"],
                   "games": as_int(r["games"]), "mean_delta": as_float(r["mean_delta"])}
            bp[slug][s].append(row)
            city = slug_to_city.get(slug)
            if city:
                cp[city][s].append(dict(row))
    if os.path.exists(TEAM_ARENA_DRAW_BY_SEASON_CSV):
        for r in read_csv(TEAM_ARENA_DRAW_BY_SEASON_CSV):
            slug = slugify(r["building"])
            s = str(as_int(r["season"]))
            row = {"teamId": r["teamId"], "team": f'{r["teamCity"]} {r["teamName"]}'.strip(),
                   "games": as_int(r["games"]), "mean_delta": as_float(r["mean_delta"])}
            bt[slug][s].append(row)
            city = slug_to_city.get(slug)
            if city:
                ct[city][s].append(dict(row))

    def pack(pmap, tmap, keys):
        out = {}
        for k in keys:
            players = _season_draw_map(pmap.get(k, {}), "personId") if k in pmap else {"all": []}
            teams = _season_draw_map(tmap.get(k, {}), "teamId") if k in tmap else {"all": []}
            seasons = sorted({int(s) for s in list(pmap.get(k, {})) + list(tmap.get(k, {}))})
            out[k] = {"players": players, "teams": teams, "seasons": seasons}
        return out

    building = pack(bp, bt, set(bp) | set(bt))
    city = pack(cp, ct, set(cp) | set(ct))
    return building, city


def load_player_draw_by_season(slug_to_city):
    """Per player: {"seasons": [..], "by_season": {"all": [...], season: [...]}} of
    their draw-by-arena rows. Empty when the by-season CSV is absent."""
    if not os.path.exists(PLAYER_ARENA_DRAW_BY_SEASON_CSV):
        return {}
    pp = defaultdict(lambda: defaultdict(list))   # pid -> season -> [rows]
    for r in read_csv(PLAYER_ARENA_DRAW_BY_SEASON_CSV):
        bld = r["building"]
        slug = slugify(bld)
        city = slug_to_city.get(slug, "")
        pp[r["personId"]][str(as_int(r["season"]))].append({
            "arena": bld, "slug": slug, "city": city,
            "city_slug": slugify(city) if city else "",
            "games": as_int(r["games"]), "mean_delta": as_float(r["mean_delta"]),
        })
    out = {}
    for pid, by_season in pp.items():
        out[pid] = {
            "seasons": sorted(int(s) for s in by_season),
            "by_season": _season_draw_map(by_season, "slug"),
        }
    return out


def load_records_by_season():
    """Season-level player-arena records (additive). Returns three views keyed
    for embedding:
      by_player[pid]      -> per (building, gameType, season) rows (player page)
      by_building[bld]    -> per (personId, gameType, season) rows (arena page)
      by_city[(city,...)] -> per (personId, gameType, season) rows summed across
                             the city's buildings (city page)
    The all-time fields already on each JSON are unaffected; these are extra.
    """
    by_player = defaultdict(list)
    by_building = defaultdict(list)
    city_agg = defaultdict(lambda: {
        "playerName": "", "games": 0, "wins": 0, "losses": 0,
        "total_points": 0, "career_high": 0,
    })
    if not os.path.exists(RECORDS_BY_SEASON_CSV):
        return {}, {}, {}
    for r in read_csv(RECORDS_BY_SEASON_CSV):
        season = as_int(r["season"])
        games = as_int(r["games"])
        # player-page row: full building context
        by_player[r["personId"]].append({
            "building": r["building"],
            "slug": slugify(r["building"]),
            "city": r["city"],
            "city_slug": slugify(r["city"]),
            "buildingType": r["buildingType"],
            "gameType": r["gameType"],
            "season": season,
            "games": games,
            "wins": as_int(r["wins"]),
            "losses": as_int(r["losses"]),
            "win_pct": as_float(r["win_pct"]),
            "total_points": as_int(r["total_points"]),
            "ppg": as_float(r["ppg"]),
            "career_high": as_int(r["career_high"]),
        })
        # arena-page row: player identity only (building implied)
        by_building[r["building"]].append({
            "personId": r["personId"],
            "playerName": r["playerName"],
            "gameType": r["gameType"],
            "season": season,
            "games": games,
            "wins": as_int(r["wins"]),
            "losses": as_int(r["losses"]),
            "win_pct": as_float(r["win_pct"]),
            "total_points": as_int(r["total_points"]),
            "ppg": as_float(r["ppg"]),
            "career_high": as_int(r["career_high"]),
        })
        # city aggregation: sum a player's buildings within the city per season
        ck = (r["city"], r["personId"], r["gameType"], season)
        a = city_agg[ck]
        a["playerName"] = r["playerName"]
        a["games"] += games
        a["wins"] += as_int(r["wins"])
        a["losses"] += as_int(r["losses"])
        a["total_points"] += as_int(r["total_points"])
        a["career_high"] = max(a["career_high"], as_int(r["career_high"]))

    by_city = defaultdict(list)
    for (city, pid, gtype, season), a in city_agg.items():
        g = a["games"]
        by_city[city].append({
            "personId": pid,
            "playerName": a["playerName"],
            "gameType": gtype,
            "season": season,
            "games": g,
            "wins": a["wins"],
            "losses": a["losses"],
            "win_pct": round(a["wins"] / g, 3) if g else 0.0,
            "total_points": a["total_points"],
            "ppg": round(a["total_points"] / g, 1) if g else 0.0,
            "career_high": a["career_high"],
        })
    return dict(by_player), dict(by_building), dict(by_city)


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


def load_avg_attendance_all():
    """Games-weighted mean attendance per building across every season/gameType
    that carries attendance (arena_baselines_all.csv), with the covered season
    range. Empty when the all-seasons baselines file is absent."""
    if not os.path.exists(ARENA_BASELINES_ALL_CSV):
        return {}
    agg = defaultdict(lambda: {"wsum": 0.0, "games": 0, "lo": 9999, "hi": 0})
    for r in read_csv(ARENA_BASELINES_ALL_CSV):
        g = as_int(r["games_with_attendance"]) or as_int(r["games"])
        if not g:
            continue
        a = agg[r["building"]]
        s = as_int(r["season"])
        a["wsum"] += as_float(r["mean_attendance"]) * g
        a["games"] += g
        a["lo"] = min(a["lo"], s)
        a["hi"] = max(a["hi"], s)
    return {b: {"avg": round(a["wsum"] / a["games"], 1),
                "first_season": a["lo"], "last_season": a["hi"]}
            for b, a in agg.items() if a["games"]}


def load_biggest_draw():
    """Per building: the visiting team with the highest games-weighted mean LOO
    delta across all covered seasons (min games). LOO deltas sum to zero per
    group, so this is a max, never an average-of-all stat."""
    if not os.path.exists(TEAM_ARENA_DRAW_BY_SEASON_CSV):
        return {}
    agg = defaultdict(lambda: defaultdict(lambda: {"wsum": 0.0, "games": 0, "team": ""}))
    for r in read_csv(TEAM_ARENA_DRAW_BY_SEASON_CSV):
        a = agg[r["building"]][r["teamId"]]
        g = as_int(r["games"])
        a["wsum"] += as_float(r["mean_delta"]) * g
        a["games"] += g
        a["team"] = f'{r["teamCity"]} {r["teamName"]}'.strip()
    best = {}
    for b, teams in agg.items():
        cand = None
        for tid, a in teams.items():
            if a["games"] < BIGGEST_DRAW_MIN_GAMES:
                continue
            md = a["wsum"] / a["games"]
            if cand is None or md > cand[0]:
                cand = (md, {"team": a["team"], "mean_delta": round(md, 1), "games": a["games"]})
        if cand:
            best[b] = cand[1]
    return best


def load_sellout(data_dir):
    """Per (building, season): the share of attended games at/above SELLOUT_WITHIN
    of that arena-season's *effective capacity*. Returns {building: {season:
    {"pct", "games", "sellouts", "capacity"}}}. Empty if attendance can't be
    assembled (no historical builder inputs).

    Effective capacity is the SELLOUT_CAPACITY_PCTL-th percentile of the
    arena-season's attendances, NOT the season max. The max is poisoned by a
    single standing-room / miscounted outlier: Chicago Stadium 1993-94 had the
    Bulls' unbroken sellout streak reported at a constant 18,676, but one inflated
    figure pushes the max above every real game, so a max-based "within 0.5%"
    reads near 0%. The 95th percentile discards the top ~5% (a lone SRO sits above
    it), so it lands on the true, repeatedly-reported capacity for sellout-heavy
    arenas — while for a genuinely weak-drawing arena-season, whose attendances are
    spread out with no ceiling being hit, the 95th percentile is just the top of
    that spread and few games fall within 0.5% of it, keeping the sellout share
    low. (A pure mode fails when no attendance value repeats.)"""
    try:
        import pandas as pd
        import build_attendance_tables_historical as bath
    except Exception:
        return {}
    games_path = os.path.join(data_dir, "Games.csv")
    hist = os.path.join(data_dir, "historical_attendance.csv")
    mp = os.path.join(data_dir, "arena_mapping.csv")
    try:
        ctx, _ = bath.load_games(games_path, hist, mp, 0.70)
    except Exception:
        return {}
    d = ctx[ctx["attendance"].fillna(0) > 0].copy()
    out = defaultdict(dict)
    for (bld, season), sub in d.groupby(["building", "season"]):
        att = sub["attendance"].astype(float)
        # effective capacity = high percentile (robust to a lone SRO outlier),
        # never below the modal attendance for arenas that report a constant
        # sellout figure.
        cap = float(att.quantile(SELLOUT_CAPACITY_PCTL))
        mode = att.mode()
        if len(mode):
            cap = max(cap, float(mode.max()))
        if cap <= 0:
            continue
        sell = int((att >= cap * (1 - SELLOUT_WITHIN)).sum())
        n = int(len(att))
        out[bld][int(season)] = {"pct": round(sell / n, 4), "games": n,
                                 "sellouts": sell, "capacity": int(round(cap))}
    return dict(out)


def load_winningest_teams(regular_only=True):
    """Per building: the team with the best win_pct (min games). Scoped to
    Regular Season by default so the overview card matches the default
    leaderboard view."""
    agg = defaultdict(lambda: defaultdict(lambda: {"games": 0, "wins": 0}))
    label = {}
    for r in read_csv(TEAM_BUILDING_CSV):
        if regular_only and r["gameType"] != "Regular Season":
            continue
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
                    team_draw_by_slug=None, home_away_by_slug=None,
                    season_by_building=None, season_draw_buildings=None,
                    sellout_by_building=None, building_range=None):
    team_draw_by_slug = team_draw_by_slug or {}
    home_away_by_slug = home_away_by_slug or {}
    season_by_building = season_by_building or {}
    season_draw_buildings = season_draw_buildings or {}
    building_range = building_range or {}
    by_building = {}
    for rec in records:
        by_building.setdefault(rec["building"], []).append(rec)

    # Season range prefers the game-derived span (1980-2026 via arena_resolver),
    # falling back to the player-record span when a building has no resolved games.
    def span(name, recs):
        gr = building_range.get(name)
        if gr:
            return gr["first_season"], gr["last_season"]
        return min(r["first_season"] for r in recs), max(r["last_season"] for r in recs)

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
            "first_season": span(name, recs)[0],
            "last_season": span(name, recs)[1],
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
            "first_season": span(name, recs)[0],
            "last_season": span(name, recs)[1],
            "leaderboards": {
                "regular": leaderboards_for(regular),
                "playoffs": leaderboards_for(playoffs),
            },
            "leaderboards_allstar": {
                "regular": leaderboards_for(regular, allstar_ids),
                "playoffs": leaderboards_for(playoffs, allstar_ids),
            },
            "records": [record_public(r) for r in recs],
            "records_by_season": season_by_building.get(name, []),
            "home_away": home_away_by_slug.get(slug, {}),
            # Full sorted lists — no top-N truncation. Truncating here would run
            # BEFORE arena.html's client-side All-Star filter, and with ~195
            # visiting players tied at the same delta a top-N slice would drop
            # most qualifying All-Stars (56 at Crypto) from the All-Stars view.
            "visiting_draw": draw_by_slug.get(slug, []),
            "team_draw": team_draw_by_slug.get(slug, []),
            "draw_label": BASELINE_SEASON_LABEL,
        }
        # Season-keyed draw (2007-2026) when available — used by arena.html so the
        # draw section follows the season selector; the note then shows only for
        # seasons genuinely absent from the data.
        sd = season_draw_buildings.get(slug)
        if sd:
            detail["visiting_draw_by_season"] = sd["players"]
            detail["team_draw_by_season"] = sd["teams"]
            detail["draw_seasons"] = sd["seasons"]
            detail["draw_label"] = draw_range_label(sd["seasons"])
        write_json(os.path.join(DATA_DIR, "buildings", slug + ".json"), detail)

    # overview table (Arena Records default view)
    baseline_avgs = load_baseline_avgs()          # 2026-only fallback
    avg_att_all = load_avg_attendance_all()       # all-seasons, games-weighted
    winning_teams = load_winningest_teams()       # Regular Season only
    biggest_draw = load_biggest_draw()
    overview = []
    for name in sorted(by_building):
        recs = by_building[name]
        # Cards are scoped to Regular Season so they match the default leaderboard
        # view (the old code combined Regular Season + Playoffs, which disagreed
        # with the RS-only board below).
        regular = [r for r in recs if r["gameType"] == "Regular Season"]
        pts = defaultdict(int)
        wl = defaultdict(lambda: {"games": 0, "wins": 0})
        pname = {}
        for r in regular:
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

        # avg attendance: prefer the all-seasons games-weighted value + real range
        aa = avg_att_all.get(name)
        if aa:
            avg_attendance = aa["avg"]
            avg_label = (str(aa["first_season"]) if aa["first_season"] == aa["last_season"]
                         else f'{aa["first_season"]}–{aa["last_season"]}')
        else:
            av = baseline_avgs.get(name)
            avg_attendance = round(av, 1) if av is not None else None
            avg_label = BASELINE_SEASON_LABEL

        # sellout: latest season's share of near-capacity games
        sellout = None
        so = (sellout_by_building or {}).get(name)
        if so:
            latest = max(so)
            sellout = {"pct": so[latest]["pct"], "season": latest, "games": so[latest]["games"]}

        overview.append({
            "slug": slugify(name),
            "name": name,
            "city": recs[0]["city"],
            "city_slug": slugify(recs[0]["city"]),
            "buildingType": recs[0]["buildingType"],
            "total_games": building_games.get(name, 0),
            "first_season": span(name, recs)[0],
            "last_season": span(name, recs)[1],
            "card_scope": "Regular Season",
            "top_scorer": top_scorer,
            "winningest_player": winningest_player,
            "winningest_team": winning_teams.get(name),
            "avg_attendance": avg_attendance,
            "avg_attendance_label": avg_label,
            "biggest_draw": biggest_draw.get(name),
            "sellout": sellout,
        })
    write_json(os.path.join(DATA_DIR, "buildings", "overview.json"), overview)
    return len(index)


# --------------------------------------------------------------------------- #
# players
# --------------------------------------------------------------------------- #
def build_players(records, draw_by_player=None, season_by_player=None,
                  season_draw_players=None):
    draw_by_player = draw_by_player or {}
    season_by_player = season_by_player or {}
    season_draw_players = season_draw_players or {}
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
            "records_by_season": season_by_player.get(pid, []),
            "arena_draw": draw_by_player.get(pid, []),
            "draw_label": BASELINE_SEASON_LABEL,
        }
        sdp = season_draw_players.get(pid)
        if sdp:
            detail["arena_draw_by_season"] = sdp["by_season"]
            detail["draw_seasons"] = sdp["seasons"]
            detail["draw_label"] = draw_range_label(sdp["seasons"])
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


def build_cities(city_records, city_games, allstar_ids, buildings_by_city,
                 season_by_city=None, city_player_draw=None, city_team_draw=None,
                 season_draw_cities=None, city_range=None):
    """Write docs/data/cities/{index,overview,slug}.json from city_records.csv.

    buildings_by_city[city] -> sorted list of {name, slug} buildings in the city,
    so a city page can link out to its arenas."""
    season_by_city = season_by_city or {}
    city_player_draw = city_player_draw or {}
    city_team_draw = city_team_draw or {}
    season_draw_cities = season_draw_cities or {}
    city_range = city_range or {}
    if not city_records:
        return 0
    by_city = {}
    for rec in city_records:
        by_city.setdefault(rec["city"], []).append(rec)

    # Season range from the game-derived building-level span (1980-2026 via
    # arena_resolver), falling back to the player-record span (Fix F). This is why
    # Atlanta reads 1980-2026 rather than 2007-2026.
    def span(name, recs):
        gr = city_range.get(name)
        if gr:
            return gr["first_season"], gr["last_season"]
        return min(r["first_season"] for r in recs), max(r["last_season"] for r in recs)

    index = []
    for name in sorted(by_city):
        recs = by_city[name]
        index.append({
            "name": name,
            "slug": slugify(name),
            "buildings": buildings_by_city.get(name, []),
            "total_games": city_games.get(name, 0),
            "first_season": span(name, recs)[0],
            "last_season": span(name, recs)[1],
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
            "first_season": span(name, recs)[0],
            "last_season": span(name, recs)[1],
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
            "first_season": span(name, recs)[0],
            "last_season": span(name, recs)[1],
            "leaderboards": {
                "regular": leaderboards_for(regular),
                "playoffs": leaderboards_for(playoffs),
            },
            "leaderboards_allstar": {
                "regular": leaderboards_for(regular, allstar_ids),
                "playoffs": leaderboards_for(playoffs, allstar_ids),
            },
            "records": [city_record_public(r) for r in recs],
            "records_by_season": season_by_city.get(name, []),
            "visiting_draw": city_player_draw.get(name, []),
            "team_draw": city_team_draw.get(name, []),
            "draw_label": BASELINE_SEASON_LABEL,
        }
        sd = season_draw_cities.get(name)
        if sd:
            detail["visiting_draw_by_season"] = sd["players"]
            detail["team_draw_by_season"] = sd["teams"]
            detail["draw_seasons"] = sd["seasons"]
            detail["draw_label"] = draw_range_label(sd["seasons"])
        write_json(os.path.join(DATA_DIR, "cities", slug + ".json"), detail)
    return len(index)


# --------------------------------------------------------------------------- #
# front page  (six ranking boxes)
# --------------------------------------------------------------------------- #
def _read_by_season(path, num_fields):
    if not os.path.exists(path):
        return []
    rows = read_csv(path)
    for r in rows:
        for k in num_fields:
            r[k] = as_float(r[k]) if "." in str(r.get(k, "")) else as_int(r.get(k, 0))
    return rows


def build_frontpage(records, home_away_by_slug, sellout_by_building):
    """Emit docs/data/frontpage.json — six ranked boxes for the landing page."""
    boxes = {}

    # building -> city, so every arena in the boxes can carry a linked city (Fix D)
    bld_city = {r["building"]: r["city"] for r in records}
    def city_of(building):
        c = bld_city.get(building, "")
        return {"city": c, "city_slug": slugify(c) if c else ""}

    # ---- latest season across the draw / baseline data ----
    seasons = set()
    pdraw = _read_by_season(PLAYER_DRAW_BY_SEASON_CSV, ["season", "games", "mean_delta"])
    for r in pdraw:
        seasons.add(as_int(r["season"]))
    baselines = read_csv(ARENA_BASELINES_ALL_CSV) if os.path.exists(ARENA_BASELINES_ALL_CSV) else []
    for r in baselines:
        seasons.add(as_int(r["season"]))
    for b, sm in (sellout_by_building or {}).items():
        seasons.update(sm)
    latest = max(seasons) if seasons else None

    # ---- (1) arena attendance ranking, latest season ----
    att = defaultdict(lambda: {"wsum": 0.0, "games": 0})
    for r in baselines:
        if as_int(r["season"]) != latest:
            continue
        g = as_int(r["games_with_attendance"]) or as_int(r["games"])
        a = att[r["building"]]
        a["wsum"] += as_float(r["mean_attendance"]) * g
        a["games"] += g
    arena_attendance = sorted(
        ({"building": b, "slug": slugify(b), "avg": round(a["wsum"] / a["games"], 1),
          "games": a["games"], **city_of(b)} for b, a in att.items() if a["games"]),
        key=lambda x: -x["avg"])[:FRONTPAGE_ROWS]
    boxes["arena_attendance"] = arena_attendance

    # ---- (2) biggest draws this season (league-wide players, min road games) ----
    season_players = [r for r in pdraw if as_int(r["season"]) == latest
                      and as_int(r["games"]) >= SEASON_DRAW_MIN_GAMES]
    boxes["biggest_draws_season"] = [
        {"personId": r["personId"], "playerName": r["playerName"],
         "mean_delta": as_float(r["mean_delta"]), "games": as_int(r["games"])}
        for r in sorted(season_players, key=lambda r: -as_float(r["mean_delta"]))[:FRONTPAGE_ROWS]]

    # ---- (3) all-time draw kings — career total & mean delta, min 100 road games.
    # total_delta = sum of per-game deltas across the career (Fix C); default sort
    # is by that career total, with the per-game average shown alongside it.
    king = defaultdict(lambda: {"wsum": 0.0, "games": 0, "name": ""})
    for r in pdraw:
        a = king[r["personId"]]
        g = as_int(r["games"])
        a["wsum"] += as_float(r["mean_delta"]) * g
        a["games"] += g
        a["name"] = r["playerName"]
    boxes["draw_kings_alltime"] = [
        {"personId": pid, "playerName": a["name"],
         "mean_delta": round(a["wsum"] / a["games"], 1),
         "total_delta": round(a["wsum"], 1), "games": a["games"]}
        for pid, a in sorted(
            ((pid, a) for pid, a in king.items() if a["games"] >= DRAW_KING_MIN_GAMES),
            key=lambda kv: -kv[1]["wsum"])[:FRONTPAGE_ROWS]]

    # ---- (4) sellout meter, latest season ----
    sell = []
    for b, sm in (sellout_by_building or {}).items():
        if latest in sm:
            sell.append({"building": b, "slug": slugify(b),
                         "pct": sm[latest]["pct"], "games": sm[latest]["games"],
                         **city_of(b)})
    boxes["sellout"] = sorted(sell, key=lambda x: -x["pct"])[:FRONTPAGE_ROWS]

    # ---- per (player, building) career_high + points, for boxes 5 & 6 ----
    pb = defaultdict(lambda: {"ch": 0, "pts": 0, "name": "", "building": ""})
    for r in records:
        k = (r["personId"], r["slug"])
        a = pb[k]
        a["ch"] = max(a["ch"], r["career_high"])
        a["pts"] += r["total_points"]
        a["name"] = r["playerName"]
        a["building"] = r["building"]

    per_arena_legend = {}
    enemy = []
    for (pid, slug), a in pb.items():
        ha = home_away_by_slug.get(slug, {}).get(pid)
        if not ha or ha["home_games"] != 0 or ha["away_games"] <= 0:
            continue
        cur = per_arena_legend.get(slug)
        if cur is None or a["ch"] > cur["career_high"]:
            per_arena_legend[slug] = {"building": a["building"], "slug": slug,
                                      "personId": pid, "playerName": a["name"],
                                      "career_high": a["ch"], **city_of(a["building"])}
        enemy.append({"building": a["building"], "slug": slug, "personId": pid,
                      "playerName": a["name"], "total_points": a["pts"],
                      "away_games": ha["away_games"], **city_of(a["building"])})
    boxes["road_legends"] = sorted(per_arena_legend.values(),
                                   key=lambda x: -x["career_high"])[:FRONTPAGE_ROWS]
    boxes["enemy_territory"] = sorted(enemy, key=lambda x: -x["total_points"])[:FRONTPAGE_ROWS]

    write_json(os.path.join(DATA_DIR, "frontpage.json"),
               {"latest_season": latest,
                "latest_label": _season_label(latest) if latest else "",
                "boxes": boxes})
    return {k: len(v) for k, v in boxes.items()}


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

    # building slug -> city (for draw city-links and city-level draw aggregation)
    slug_to_city = {slugify(r["building"]): r["city"] for r in records}

    # additive supporting tables
    building_games, city_games, building_range, city_range = load_game_counts(args.data_dir)
    allstar_ids_list, allstar_meta = load_allstar()
    allstar_ids = set(allstar_ids_list)
    draw_by_slug, draw_by_player = load_arena_draw(slug_to_city)
    team_draw_by_slug = load_team_arena_draw()
    home_away_by_slug = load_home_away()
    city_player_draw, city_team_draw = load_city_draw(slug_to_city)
    season_draw_buildings, season_draw_cities = load_arena_draw_by_season(slug_to_city)
    season_draw_players = load_player_draw_by_season(slug_to_city)
    city_records = load_city_records()
    season_by_player, season_by_building, season_by_city = load_records_by_season()
    sellout_by_building = load_sellout(args.data_dir)
    if season_draw_buildings:
        print(f"season-keyed draw: {len(season_draw_buildings)} buildings, "
              f"{len(season_draw_cities)} cities, {len(season_draw_players)} players")

    write_json(os.path.join(DATA_DIR, "allstar.json"),
               {"personIds": allstar_ids_list, "players": allstar_meta})
    print(f"allstar.json: {len(allstar_ids_list)} All-Star players")

    draw = build_draw(args.data_dir, args.player_filename, args.chunksize)
    write_json(os.path.join(DATA_DIR, "draw.json"), draw)
    print(f"draw.json: {len(draw['teams']['all'])} teams, "
          f"{len(draw['players']['all'])} players (career, games>={PLAYER_DRAW_MIN_GAMES}) "
          f"across {len(draw['seasons'])} seasons")

    n_buildings = build_buildings(records, building_games, allstar_ids, draw_by_slug,
                                  team_draw_by_slug, home_away_by_slug,
                                  season_by_building, season_draw_buildings,
                                  sellout_by_building, building_range)
    print(f"buildings: index + overview + {n_buildings} detail files")

    # city -> its buildings (for cross-links on the city page)
    buildings_by_city = defaultdict(dict)
    for r in records:
        buildings_by_city[r["city"]][r["building"]] = slugify(r["building"])
    buildings_by_city = {
        c: [{"name": n, "slug": s} for n, s in sorted(b.items())]
        for c, b in buildings_by_city.items()
    }
    n_cities = build_cities(city_records, city_games, allstar_ids, buildings_by_city,
                            season_by_city, city_player_draw, city_team_draw,
                            season_draw_cities, city_range)
    print(f"cities: index + overview + {n_cities} detail files")

    n_players = build_players(records, draw_by_player, season_by_player,
                              season_draw_players)
    print(f"players: index + {n_players} detail files")

    n_search = build_search(records, city_records)
    print(f"search.json: {n_search} entities")

    fp = build_frontpage(records, home_away_by_slug, sellout_by_building)
    print(f"frontpage.json: {fp}")

    print("Done.")


if __name__ == "__main__":
    main()
