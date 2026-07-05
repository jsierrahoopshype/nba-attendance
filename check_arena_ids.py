#!/usr/bin/env python3
"""Check whether 2007-2025 arenaIds match the 47 named 2026 arenaIds.
If they do, the 2026 id-to-name mapping can be propagated backward."""

import sys
import pandas as pd

data_dir = sys.argv[1] if len(sys.argv) > 1 else "data"
g = pd.read_csv(f"{data_dir}/Games.csv", low_memory=False)
g["gameDate"] = pd.to_datetime(g["gameDate"], errors="coerce")
g["season"] = g["gameDate"].dt.year + (g["gameDate"].dt.month >= 8).astype(int)

# The 2026 mapping: arenaId -> arenaName
m26 = g[(g["season"] == 2026) & g["arenaName"].notna()][["arenaId", "arenaName"]].drop_duplicates()
print(f"2026 named arenaIds: {m26['arenaId'].nunique()}")

# Historical games with a real arenaId (0 = unknown)
hist = g[(g["season"] >= 2007) & (g["season"] <= 2025) & (g["arenaId"] != 0)]
print(f"2007-2025 games with non-zero arenaId: {len(hist):,}")
print(f"2007-2025 distinct arenaIds: {hist['arenaId'].nunique()}")

known = set(m26["arenaId"])
matched = hist[hist["arenaId"].isin(known)]
print(f"\nGames that would inherit a 2026 name: {len(matched):,} ({100*len(matched)/len(hist):.1f}%)")
print(f"ArenaIds in 2007-2025 NOT in the 2026 mapping: {hist['arenaId'].nunique() - hist[hist['arenaId'].isin(known)]['arenaId'].nunique()}")

# Coverage by season, to see where inheritance breaks down
cov = hist.assign(named=hist["arenaId"].isin(known)).groupby("season")["named"].mean().mul(100).round(1)
print("\n% of games inheriting a name, by season:")
print(cov.to_string())