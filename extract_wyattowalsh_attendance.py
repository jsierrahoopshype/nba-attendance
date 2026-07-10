#!/usr/bin/env python3
"""Extract wyattowalsh attendance for games that exist in eoinamoore Games.csv
(2007-2023), producing a small committable CSV: gameId, attendance."""
import sqlite3
import sys

import pandas as pd

data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
db_path = sys.argv[2] if len(sys.argv) > 2 else "nba.sqlite"

g = pd.read_csv(f"{data_dir}/Games.csv", low_memory=False, usecols=["gameId", "gameDate", "gameType"])
g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
g["season"] = g["gameDate"].dt.year + (g["gameDate"].dt.month >= 8).astype(int)
g = g[(g["season"] >= 2007) & (g["season"] <= 2023)
      & g["gameType"].isin(["Regular Season", "Playoffs"])].copy()
g["gid"] = g["gameId"].astype(str).str.replace(".0", "", regex=False).str.zfill(10)

conn = sqlite3.connect(db_path)
gi = pd.read_sql("SELECT game_id, attendance FROM game_info WHERE attendance > 0", conn)
conn.close()
gi["gid"] = gi["game_id"].astype(str).str.zfill(10)
gi = gi.drop_duplicates(subset="gid")

out = g.merge(gi[["gid", "attendance"]], on="gid", how="inner")
out = out[["gameId", "attendance"]]
out["attendance"] = out["attendance"].astype(int)
out.to_csv(f"{data_dir}/historical_attendance.csv", index=False, encoding="utf-8-sig")
print(f"Wrote {data_dir}/historical_attendance.csv ({len(out):,} rows)")
print(out.groupby(out["gameId"].astype(str).str.zfill(10).str[3:5])["attendance"].count().to_string())