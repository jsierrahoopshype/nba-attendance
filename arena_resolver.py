#!/usr/bin/env python3
"""Shared arena/building resolver for the historical (1980-2026) pipelines.

Two-tier resolution, additive to the existing arenaId mapping:
  1. If a game's arenaId is in data/arena_mapping.csv, use that building/city
     exactly as the current pipelines do (so 2007+ output is unchanged).
  2. Otherwise, for 1980-2006 Regular Season + Playoff games, resolve the
     building from the home team's city+name and a season that falls within a
     data/arena_mapping_pre2007.csv row's [first_season, last_season] range.

If data/arena_mapping_pre2007.csv is absent, tier 2 is inert and callers keep
their current 2007+ behavior — nothing breaks.

Also provides a coverage report: every distinct (hometeamCity, hometeamName,
season) in 1980-2006 that matches no mapping row, with its game count, so name
mismatches between the CSV and Games.csv's team strings are visible and fixable
rather than silently dropped. Callers stop building if unmatched games exceed a
threshold (default 2%).
"""

import os
from collections import defaultdict

import pandas as pd

PRE2007_SEASON_LO = 1980
PRE2007_SEASON_HI = 2006
UNMATCHED_GATE = 0.02   # stop building if >2% of 1980-2006 games are unresolved


def _norm(s):
    return str(s).strip() if s is not None else ""


class ArenaResolver:
    def __init__(self, id_building, id_city, id_type, pre2007_index, has_pre2007):
        self.id_building = id_building      # arenaId -> building
        self.id_city = id_city              # arenaId -> city
        self.id_type = id_type              # arenaId -> type (home/neutral/bubble)
        self.pre2007 = pre2007_index        # (city, name) -> [(first, last, building, city)]
        self.has_pre2007 = has_pre2007

    # ---- tier 2 lookup for one (city, name, season) ----
    def _pre2007_lookup(self, city, name, season):
        for first, last, bld, bcity in self.pre2007.get((_norm(city), _norm(name)), ()):
            if first <= season <= last:
                return bld, bcity
        return None, None

    def attach(self, g):
        """Return g with `building`, `city`, `buildingType` filled by tier 1 then,
        for 1980-2006 games, tier 2 (a pre-2007 home arena). Unresolved rows get
        NaN building (dropped downstream)."""
        g = g.copy()
        aid = pd.to_numeric(g["arenaId"], errors="coerce")
        g["building"] = aid.map(self.id_building)
        g["city"] = aid.map(self.id_city)
        g["buildingType"] = aid.map(self.id_type)

        if self.has_pre2007:
            need = g["building"].isna() & g["season"].between(PRE2007_SEASON_LO, PRE2007_SEASON_HI)
            if need.any():
                sub = g.loc[need, ["hometeamCity", "hometeamName", "season"]]
                resolved = {}
                for row in sub.drop_duplicates().itertuples(index=False):
                    resolved[(row.hometeamCity, row.hometeamName, int(row.season))] = \
                        self._pre2007_lookup(row.hometeamCity, row.hometeamName, int(row.season))
                keys = list(zip(sub["hometeamCity"], sub["hometeamName"], sub["season"].astype(int)))
                g.loc[need, "building"] = [resolved[k][0] for k in keys]
                g.loc[need, "city"] = [resolved[k][1] for k in keys]
                # pre-2007 rows resolved via a team's home arena are home games
                g.loc[need & g["building"].notna(), "buildingType"] = "home"
        return g

    def coverage_report(self, g, lo=PRE2007_SEASON_LO, hi=PRE2007_SEASON_HI, verbose=True):
        """Print/return the unmatched (city, name, season) combos in [lo, hi] RS+PO
        games. Returns (unmatched_games, total_games, fraction)."""
        window = g[(g["season"] >= lo) & (g["season"] <= hi)
                   & g["gameType"].isin(("Regular Season", "Playoffs"))]
        total = len(window)
        if total == 0:
            return 0, 0, 0.0
        attached = self.attach(window)
        miss = attached[attached["building"].isna()]
        combos = (miss.groupby(["hometeamCity", "hometeamName", "season"])
                      .size().reset_index(name="games")
                      .sort_values("games", ascending=False))
        unmatched = int(combos["games"].sum())
        frac = unmatched / total
        if verbose:
            print(f"\n--- pre-2007 building coverage ({lo}-{hi} Regular Season + Playoffs) ---")
            print(f"  games in window: {total:,} | unresolved: {unmatched:,} ({100*frac:.2f}%)")
            if len(combos):
                print(f"  {len(combos)} unmatched (hometeamCity, hometeamName, season) combos "
                      "— fix these in arena_mapping_pre2007.csv:")
                shown = 0
                for r in combos.itertuples(index=False):
                    print(f"    {r.hometeamCity!r:>18} {r.hometeamName!r:<22} {int(r.season)}  x{int(r.games)}")
                    shown += 1
                    if shown >= 60:
                        print(f"    … and {len(combos) - shown} more")
                        break
            else:
                print("  all 1980-2006 games resolved to a building.")
        return unmatched, total, frac


def load_arena_resolver(data_dir):
    mapping_path = os.path.join(data_dir, "arena_mapping.csv")
    pre2007_path = os.path.join(data_dir, "arena_mapping_pre2007.csv")

    id_building, id_city, id_type = {}, {}, {}
    if os.path.exists(mapping_path):
        m = pd.read_csv(mapping_path)
        m["arenaId"] = pd.to_numeric(m["arenaId"], errors="coerce")
        for r in m.itertuples(index=False):
            if pd.notna(r.arenaId):
                id_building[float(r.arenaId)] = r.building
                id_city[float(r.arenaId)] = r.city
                id_type[float(r.arenaId)] = getattr(r, "type", "home")

    pre2007_index = defaultdict(list)
    has_pre2007 = os.path.exists(pre2007_path)
    if has_pre2007:
        p = pd.read_csv(pre2007_path)
        for r in p.itertuples(index=False):
            pre2007_index[(_norm(r.teamCity), _norm(r.teamName))].append(
                (int(r.first_season), int(r.last_season), r.building, r.city))
    return ArenaResolver(id_building, id_city, id_type, dict(pre2007_index), has_pre2007)
