#!/usr/bin/env python3
""""Last night at the gate": short notes for the front page, rebuilt every morning.

From the season in progress (data/live/<season>/) it writes docs/data/notes.json
with the latest game night's notes plus one-line summaries of the nights before:

  * the biggest road draw of the night (visiting team's crowd vs. that arena's
    own average this season, leave-one-out) and who led the visitors in scoring;
  * the biggest crowd of the night, flagged when it's the arena's or the
    league's biggest of the season;
  * the crowd furthest below its arena's average;
  * players who scored more in a building than ever before there (from the full
    history in output/ plus this season's earlier games);
  * sellouts, and arenas that have sold out every game this season;
  * the league's average crowd so far vs. last season.

Text carries link tokens the page turns into links, faces and logos:
  [[p:<personId>|<name>]]  [[a:<arena slug>|<arena>]]  [[t:<team name>]]
Every figure comes from the same games the rest of the site uses; nothing is
estimated. A night with no attendance figures yet produces no notes.
"""
import json
import math
import os
import re
import sys
from collections import defaultdict

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIVE_DIR = os.path.join(ROOT, "data", "live")
OUT = os.path.join(ROOT, "docs", "data", "notes.json")

TYPES = ("Regular Season", "Playoffs", "Play-in Tournament", "Emirates NBA Cup")
MIN_BASELINE_GAMES = 3      # other home games needed before an arena's average means anything
HIGH_MIN_POINTS = 25        # a building best has to be a real scoring night
HIGH_MIN_PRIOR_GAMES = 3    # ... and beat at least a few earlier games there
SELLOUT_WITHIN = 0.005      # same rule as generate_dashboard_data.py
NIGHTS_KEPT = 10


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def fmt(n):
    return f"{int(round(n)):,}"


def season_label(y):
    return f"{y - 1}-{str(y)[-2:]}"


def ordinal_day(d):
    t = pd.Timestamp(d)
    return f"{t.strftime('%A')}, {t.strftime('%b')} {t.day}"


def P(pid, name):
    return f"[[p:{int(pid)}|{name}]]"


def A(building):
    return f"[[a:{slugify(building)}|{building}]]"


def T(team):
    return f"[[t:{team}]]"


def load():
    seasons = sorted(int(d) for d in os.listdir(LIVE_DIR) if d.isdigit()) if os.path.isdir(LIVE_DIR) else []
    if not seasons:
        return None
    se = seasons[-1]
    g = pd.read_csv(os.path.join(LIVE_DIR, str(se), "games.csv"), low_memory=False)
    p = pd.read_csv(os.path.join(LIVE_DIR, str(se), "players.csv"), low_memory=False)
    g = g[g["gameType"].isin(TYPES)].copy()
    g["date"] = g["gameDate"].str[:10]
    g["attendance"] = pd.to_numeric(g["attendance"], errors="coerce")
    m = pd.read_csv(os.path.join(ROOT, "data", "arena_mapping.csv"))
    bmap = dict(zip(pd.to_numeric(m["arenaId"], errors="coerce"), m["building"]))
    g["building"] = pd.to_numeric(g["arenaId"], errors="coerce").map(bmap)
    g["home"] = (g["hometeamCity"].astype(str) + " " + g["hometeamName"].astype(str)).str.strip()
    g["away"] = (g["awayteamCity"].astype(str) + " " + g["awayteamName"].astype(str)).str.strip()
    return se, g, p


def capacities(se, g):
    """Effective capacity per building, as the sellout meter computes it, from
    last season's and this season's crowds."""
    hist = pd.read_csv(os.path.join(ROOT, "data", "Games.csv"), low_memory=False,
                       usecols=["arenaId", "attendance", "gameDate", "gameType"])
    hist = hist[hist["gameType"].isin(("Regular Season", "Playoffs"))]
    d = pd.to_datetime(hist["gameDate"], errors="coerce")
    hist = hist[(d.dt.year + (d.dt.month >= 8).astype(int)) == se - 1]
    m = pd.read_csv(os.path.join(ROOT, "data", "arena_mapping.csv"))
    bmap = dict(zip(pd.to_numeric(m["arenaId"], errors="coerce"), m["building"]))
    hist = hist.assign(building=pd.to_numeric(hist["arenaId"], errors="coerce").map(bmap))
    both = pd.concat([hist[["building", "attendance"]], g[["building", "attendance"]]])
    both = both[both["attendance"].fillna(0) > 0].dropna(subset=["building"])
    cap = {}
    for b, sub in both.groupby("building"):
        a = sub["attendance"].astype(float)
        c = float(a.quantile(0.95))
        mode = a.mode()
        if len(mode):
            c = max(c, float(mode.max()))
        cap[b] = c
    return cap


def building_bests():
    """(personId, building) -> (games, best) through last season, all game types."""
    r = pd.read_csv(os.path.join(ROOT, "output", "player_arena_records_2007.csv"), encoding="utf-8-sig",
                    usecols=["personId", "building", "games", "career_high"])
    agg = r.groupby(["personId", "building"]).agg(games=("games", "sum"), best=("career_high", "max"))
    return {k: (int(v.games), int(v.best)) for k, v in agg.iterrows()}


def night_notes(date, se, g, p, cap, bests):
    night = g[g["date"] == date]
    earlier = g[g["date"] < date]
    att = night[night["attendance"].fillna(0) > 0]
    notes = []
    if att.empty:
        return None
    rs_all = g[g["gameType"] == "Regular Season"]

    # leave-one-out season average per building (this season, regular season)
    def baseline(row):
        others = rs_all[(rs_all["building"] == row["building"]) & (rs_all["gameId"] != row["gameId"])
                        & (rs_all["attendance"].fillna(0) > 0)]
        return (float(others["attendance"].mean()), len(others)) if len(others) >= MIN_BASELINE_GAMES else (None, len(others))

    rows = []
    for _, r in att.iterrows():
        base, n = baseline(r) if isinstance(r["building"], str) else (None, 0)
        rows.append({**r.to_dict(), "base": base, "delta": (r["attendance"] - base) if base else None})
    df = pd.DataFrame(rows)

    def top_scorer(gid, team_id):
        sub = p[(p["gameId"] == gid) & (p["playerteamId"] == team_id)]
        if sub.empty:
            return None
        s = sub.sort_values("points", ascending=False).iloc[0]
        return int(s["personId"]), f"{s['firstName']} {s['lastName']}".strip(), int(s["points"])

    # 1. biggest road draw
    dd = df.dropna(subset=["delta"])
    featured = None
    if len(dd):
        r = dd.sort_values("delta", ascending=False).iloc[0]
        if r["delta"] > 0:
            ts = top_scorer(r["gameId"], r["awayteamId"])
            text = (f"The {T(r['away'])} drew {fmt(r['delta'])} more fans than usual at {A(r['building'])}: "
                    f"{fmt(r['attendance'])}, against a {season_label(se)} average of {fmt(r['base'])} there.")
            img = {"t": r["away"]}
            if ts:
                text += f" {P(ts[0], ts[1])} led them with {ts[2]} points."
                img = {"p": ts[0]}
            featured = {"k": "draw", "t": text, "img": img}
            notes.append(featured)

    # 2. biggest crowd of the night
    r = df.sort_values("attendance", ascending=False).iloc[0]
    prior_b = earlier[(earlier["building"] == r["building"]) & (earlier["attendance"].fillna(0) > 0)]
    prior_all = earlier[earlier["attendance"].fillna(0) > 0]
    tail = ""
    if len(prior_all) >= 20 and r["attendance"] > prior_all["attendance"].max():
        tail = ", the biggest NBA crowd of the season"
    elif len(prior_b) >= 2 and r["attendance"] > prior_b["attendance"].max():
        tail = ", its biggest crowd of the season"
    where = A(r["building"]) if isinstance(r["building"], str) else r["arenaName"]
    notes.append({"k": "crowd", "img": {"t": r["home"]},
                  "t": f"Biggest crowd of the night: {fmt(r['attendance'])} at {where} for the {T(r['away'])} "
                       f"at the {T(r['home'])}{tail}."})

    # 3. building bests
    highs = []
    seen_live = defaultdict(lambda: [0, 0])          # this season before tonight
    ep = p[p["gameId"].isin(earlier["gameId"])].merge(earlier[["gameId", "building"]], on="gameId")
    for (pid, b), sub in ep.groupby(["personId", "building"]):
        seen_live[(int(pid), b)] = [len(sub), int(sub["points"].max())]
    np_ = p[p["gameId"].isin(night["gameId"])].merge(night[["gameId", "building"]], on="gameId")
    for _, s in np_[np_["points"] >= HIGH_MIN_POINTS].iterrows():
        if not isinstance(s["building"], str):
            continue
        pid = int(s["personId"])
        g0, b0 = bests.get((pid, s["building"]), (0, 0))
        g1, b1 = seen_live[(pid, s["building"])]
        games, best = g0 + g1, max(b0, b1)
        if games >= HIGH_MIN_PRIOR_GAMES and s["points"] > best:
            highs.append((int(s["points"]), pid, f"{s['firstName']} {s['lastName']}".strip(), s["building"], games, best))
    for pts, pid, name, b, games, best in sorted(highs, reverse=True)[:3]:
        n = {"k": "high", "img": {"p": pid},
             "t": f"{P(pid, name)} scored {pts} at {A(b)}, his most there in {games + 1} games "
                  f"(previous best: {best})."}
        notes.append(n)
        if featured is None:
            featured = n

    # 4. furthest below its average
    if len(dd) >= 2:
        r = dd.sort_values("delta").iloc[0]
        if r["delta"] < 0:
            notes.append({"k": "low", "img": {"t": r["home"]},
                          "t": f"Lightest crowd relative to normal: {fmt(r['attendance'])} at {A(r['building'])} for the "
                               f"{T(r['away'])}, {fmt(-r['delta'])} under its {season_label(se)} average."})

    # 5. sellouts and sellout runs
    sold = [r for _, r in df.iterrows() if isinstance(r["building"], str) and r["building"] in cap
            and r["attendance"] >= cap[r["building"]] * (1 - SELLOUT_WITHIN)]
    runs = []
    for b in {r["building"] for r in sold}:
        home = g[(g["building"] == b) & (g["date"] <= date) & (g["attendance"].fillna(0) > 0)]
        if len(home) >= 5 and (home["attendance"] >= cap[b] * (1 - SELLOUT_WITHIN)).all():
            runs.append((len(home), b))
    if sold:
        t = (f"{len(sold)} of {len(df)} games " + ("was a sellout." if len(sold) == 1 else "were sellouts.")
             if len(sold) < len(df) else f"All {len(df)} games were sellouts.")
        if runs:
            n, b = max(runs)
            t += f" {A(b)} has sold out all {n} of its games this season."
        notes.append({"k": "sellouts", "t": t, "img": {"a": slugify(max(runs)[1])} if runs else None})

    # 6. league pace
    season_att = g[(g["date"] <= date) & (g["gameType"] == "Regular Season") & (g["attendance"].fillna(0) > 0)]
    if len(season_att) >= 30:
        hist = pd.read_csv(os.path.join(ROOT, "data", "Games.csv"), low_memory=False,
                           usecols=["attendance", "gameDate", "gameType"])
        hist = hist[hist["gameType"] == "Regular Season"]
        d = pd.to_datetime(hist["gameDate"], errors="coerce")
        prev = hist[((d.dt.year + (d.dt.month >= 8).astype(int)) == se - 1) & (hist["attendance"].fillna(0) > 0)]
        avg = season_att["attendance"].mean()
        t = f"League average so far: {fmt(avg)} fans per game over {len(season_att)} games"
        if len(prev) >= 600:
            chg = 100 * (avg / prev["attendance"].mean() - 1)
            word = "above" if chg >= 0 else "below"
            t += f", {abs(chg):.1f}% {word} {season_label(se - 1)}'s {fmt(prev['attendance'].mean())}"
        notes.append({"k": "pace", "t": t + "."})

    counts = {"games": int(len(night)), "fans": int(att["attendance"].sum()), "sellouts": len(sold)}
    return {"date": date, "label": ordinal_day(date), "counts": counts,
            "featured": featured or notes[0], "notes": [n for n in notes if n is not (featured or notes[0])]}


def main():
    got = load()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if not got:
        json.dump({"nights": []}, open(OUT, "w"))
        print("notes: no season in progress")
        return 0
    se, g, p = got
    cap = capacities(se, g)
    bests = building_bests()
    dates = sorted({d for d in g.loc[g["attendance"].fillna(0) > 0, "date"]}, reverse=True)
    nights = []
    for d in dates[:NIGHTS_KEPT]:
        n = night_notes(d, se, g, p, cap, bests)
        if n:
            nights.append(n)
    # only the latest night keeps every note; earlier nights keep their headline
    out = {"season": se, "season_label": season_label(se), "nights": nights[:1] + [
        {k: v for k, v in n.items() if k != "notes"} for n in nights[1:]]}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"notes: {len(nights)} nights, latest {nights[0]['date'] if nights else '-'} "
          f"({len(nights[0]['notes']) + 1 if nights else 0} notes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
