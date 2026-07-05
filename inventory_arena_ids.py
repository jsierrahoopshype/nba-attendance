#!/usr/bin/env python3
"""Inventory all arenaIds 2007-2026: seasons active, home teams, game counts,
plus the 2026 name where known. Output: arena_id_inventory.csv for manual/
assisted mapping to canonical buildings."""

import sys
import pandas as pd

data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
g = pd.read_csv(f"{data_dir}/Games.csv", low_memory=False)
g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
g["season"] = g["gameDate"].dt.year + (g["gameDate"].dt.month >= 8).astype(int)

g = g[(g["season"] >= 2007) & (g["arenaId"] != 0)].copy()
g["homeTeam"] = g["hometeamCity"].fillna("") + " " + g["hometeamName"].fillna("")

# 2026 name per arenaId where it exists
names = (
    g[g["arenaName"].notna()][["arenaId", "arenaName"]]
    .drop_duplicates()
    .set_index("arenaId")["arenaName"]
)

rows = []
for aid, sub in g.groupby("arenaId"):
    teams = sub["homeTeam"].value_counts()
    rows.append({
        "arenaId": aid,
        "name_2026": names.get(aid, ""),
        "first_season": int(sub["season"].min()),
        "last_season": int(sub["season"].max()),
        "games": len(sub),
        "main_home_team": teams.index[0],
        "main_team_games": int(teams.iloc[0]),
        "other_teams": "; ".join(f"{t} ({n})" for t, n in teams.iloc[1:4].items()),
    })

inv = pd.DataFrame(rows).sort_values(
    ["main_home_team", "first_season"]
).reset_index(drop=True)
inv.to_csv("arena_id_inventory.csv", index=False, encoding="utf-8-sig")
print(f"Wrote arena_id_inventory.csv ({len(inv)} arenaIds)")
print(inv.to_string(max_rows=250))