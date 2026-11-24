#!/usr/bin/env python3
"""personId -> headshot file stem, from a listing of the nba-headshots repo.

The daily Action lists (names only, no images) players/headshots/face-tight/,
face-fill/ and head-uniform-sharp/ in jsierrahoopshype/nba-headshots and passes
that listing here. Files are named <personId>-<slug>.webp, so the personId is the
part before the first hyphen. Only players who appear in this section are kept.

Writes data/heads.json: {"<personId>": "<stem>"}. The pages build each image
URL from the stem (face-fill first, face-tight as the fallback, the bust for
page headers) and show the player's initials when no file exists.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "heads.json")
STEM = re.compile(r"^(\d+)-[0-9a-z-]+$")


def known_ids():
    """personIds the site has a page for (base + season in progress)."""
    ids = set()
    import csv
    for path in (os.path.join(ROOT, "output", "player_arena_records_2007.csv"),):
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                ids.add(r["personId"])
    live = os.path.join(ROOT, "data", "live")
    for d in os.listdir(live) if os.path.isdir(live) else []:
        p = os.path.join(live, d, "players.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    ids.add(str(r["personId"]))
    return ids


def main():
    listing = open(sys.argv[1], encoding="utf-8").read().split()
    stems = {}
    for line in listing:
        name = os.path.basename(line)
        if not name.endswith(".webp"):
            continue
        stem = name[:-5]
        m = STEM.match(stem)
        if m:
            stems.setdefault(m.group(1), stem)
    if len(stems) < 1000:      # a partial listing must never wipe the faces
        raise SystemExit(f"only {len(stems)} headshots listed; keeping the committed file")
    ids = known_ids()
    out = {pid: stems[pid] for pid in sorted(stems, key=int) if pid in ids}
    data = json.dumps(out, separators=(",", ":"), sort_keys=False)
    if not os.path.exists(OUT) or open(OUT, encoding="utf-8").read() != data:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(data)
    print(f"heads.json: {len(out)} of {len(ids)} players have a headshot")


if __name__ == "__main__":
    main()
