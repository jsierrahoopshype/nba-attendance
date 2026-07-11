#!/usr/bin/env python3
"""Shared arena/building resolver for the historical (1980-2026) pipelines.

Two-tier resolution, split strictly by season:
  1. Season >= 2007: use the game's arenaId in data/arena_mapping.csv, exactly as
     the current pipelines do (so 2007+ output is unchanged).
  2. Season <= 2006: IGNORE arenaId entirely. Games.csv backfills pre-2007 games
     with the franchise's modern arenaId (audit_tier_conflicts.py shows e.g.
     Chicago Stadium-era Bulls games carrying the United Center's id), so trusting
     it resolves the wrong, era-incorrect building. These games resolve only from
     the home team's city+name and a season inside a data/arena_mapping_pre2007.csv
     row's [first_season, last_season] range. A pre-2007 game that matches no row
     is unresolved (counted by the coverage report / 2% gate) — it never falls
     back to arenaId.

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
        """Return g with `building`, `city`, `buildingType`.

        Season >= 2007: tier 1 — arenaId in arena_mapping.csv, exactly as before.

        Season <= 2006: arenaId is IGNORED entirely (Games.csv backfills pre-2007
        games with the franchise's modern arenaId — e.g. Chicago Stadium-era Bulls
        games carry the United Center's id — so tier 1 would resolve the wrong,
        era-incorrect building). These games resolve exclusively via the pre-2007
        team-season lookup; any that miss it stay unresolved (NaN building) and are
        counted by the coverage report / 2% gate — never falling back to arenaId.
        """
        g = g.copy()
        aid = pd.to_numeric(g["arenaId"], errors="coerce")
        modern = g["season"] >= 2007

        # tier 1 — only for 2007+ games (byte-identical to the previous behavior
        # once the frame is restricted to 2007+, which is the no-pre2007 case).
        g["building"] = pd.NA
        g["city"] = pd.NA
        g["buildingType"] = pd.NA
        g.loc[modern, "building"] = aid[modern].map(self.id_building)
        g.loc[modern, "city"] = aid[modern].map(self.id_city)
        g.loc[modern, "buildingType"] = aid[modern].map(self.id_type)

        # tier 2 — pre-2007 team-season lookup, arenaId ignored.
        if self.has_pre2007:
            pre = g["season"].between(PRE2007_SEASON_LO, PRE2007_SEASON_HI)
            if pre.any():
                sub = g.loc[pre, ["hometeamCity", "hometeamName", "season"]]
                resolved = {}
                for row in sub.drop_duplicates().itertuples(index=False):
                    resolved[(row.hometeamCity, row.hometeamName, int(row.season))] = \
                        self._pre2007_lookup(row.hometeamCity, row.hometeamName, int(row.season))
                keys = list(zip(sub["hometeamCity"], sub["hometeamName"], sub["season"].astype(int)))
                g.loc[pre, "building"] = [resolved[k][0] for k in keys]
                g.loc[pre, "city"] = [resolved[k][1] for k in keys]
                # pre-2007 rows resolved via a team's home arena are home games
                g.loc[pre & g["building"].notna(), "buildingType"] = "home"
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


def _selftest():
    """In-memory verification of the season-split tier logic. No files needed."""
    # 2007+ mapping: modern arenas for the backfilled ids.
    id_building = {100.0: "United Center", 200.0: "TD Garden"}
    id_city = {100.0: "Chicago", 200.0: "Boston"}
    id_type = {100.0: "home", 200.0: "home"}
    # pre-2007 team-season -> era-correct arena.
    pre = {
        ("Chicago", "Bulls"): [(1980, 1994, "Chicago Stadium", "Chicago")],
        ("Boston", "Celtics"): [(1980, 1995, "Boston Garden", "Boston")],
    }
    R = ArenaResolver(id_building, id_city, id_type, pre, True)
    g = pd.DataFrame([
        # pre-2007 Bulls game whose arenaId is BACKFILLED to the United Center id:
        # must resolve to Chicago Stadium, NOT United Center.
        dict(gameId=1, arenaId=100, season=1990, gameType="Regular Season",
             hometeamCity="Chicago", hometeamName="Bulls"),
        # pre-2007 Celtics game, arenaId backfilled to TD Garden id -> Boston Garden.
        dict(gameId=2, arenaId=200, season=1990, gameType="Regular Season",
             hometeamCity="Boston", hometeamName="Celtics"),
        # pre-2007 team absent from the pre-2007 mapping -> unresolved (no fallback).
        dict(gameId=3, arenaId=100, season=1990, gameType="Regular Season",
             hometeamCity="Denver", hometeamName="Nuggets"),
        # 2007+ game -> tier 1 via arenaId, unchanged.
        dict(gameId=4, arenaId=100, season=2015, gameType="Regular Season",
             hometeamCity="Chicago", hometeamName="Bulls"),
    ])
    a = R.attach(g).set_index("gameId")
    assert a.loc[1, "building"] == "Chicago Stadium", a.loc[1, "building"]
    assert a.loc[2, "building"] == "Boston Garden", a.loc[2, "building"]
    assert pd.isna(a.loc[3, "building"]), "pre-2007 miss must not fall back to arenaId"
    assert a.loc[4, "building"] == "United Center", a.loc[4, "building"]
    un, total, frac = R.coverage_report(g, verbose=False)
    assert (total, un) == (3, 1), (total, un)

    # No pre-2007 file: pre-2007 games are unresolved, 2007+ still tier 1.
    R2 = ArenaResolver(id_building, id_city, id_type, {}, False)
    a2 = R2.attach(g).set_index("gameId")
    assert pd.isna(a2.loc[1, "building"]) and a2.loc[4, "building"] == "United Center"
    print("arena_resolver self-test: OK (pre-2007 ignores backfilled arenaId; "
          "2007+ unchanged; misses counted, never fall back)")


if __name__ == "__main__":
    _selftest()
