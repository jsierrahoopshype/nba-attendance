#!/usr/bin/env python3
""""Last night at the gate": short notes for the front page, rebuilt every morning.

From the season in progress (data/live/<season>/) it writes docs/data/notes.json
with the latest game night's notes plus one-line summaries of the nights before:

  * the biggest road draw of the night (visiting team's crowd vs. that arena's
    other home games this season, named as such in the text) and who led the visitors in scoring;
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


# Several ways to say each kind of note, so the page doesn't read the same
# every morning. pick() rotates through them by date (each kind on its own
# offset), so consecutive nights use different wording and a rebuild of the
# same night always gives the same sentence.
def pick(variants, date, kind):
    day = pd.Timestamp(date).toordinal()
    off = sum(ord(c) for c in kind)
    return variants[(day + off) % len(variants)]


def P(pid, name):
    return f"[[p:{int(pid)}|{name}]]"


def A(building):
    return f"[[a:{slugify(building)}|{building}]]"


def T(team):
    return f"[[t:{team}]]"


def poss(team):
    """The apostrophe after a team name: the Kings' / the Heat's."""
    return "'" if str(team).endswith("s") else "'s"


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
            # The comparison is always named in full: the home team's OTHER home games this season
            # (leave-one-out), so it never reads as the season average sites like ESPN show, which
            # include tonight. No "packed": that word is for a building close to full.
            v = dict(t=T(r["away"]), a=A(r["building"]), d=fmt(r["delta"]), att=fmt(r["attendance"]),
                     b=fmt(r["base"]), h=T(r["home"]), hs=poss(r["home"]))
            text = pick([
                "The {t} drew {att} to {a}, {d} more than the {h}{hs} other home games this season have averaged ({b}).",
                "{a} had {att} in the seats for the {t}. The {h}{hs} other home games this season have averaged {b}, so that's {d} more.",
                "Nobody moved the turnstiles like the {t}: {att} at {a}, {d} above the {h}{hs} average for their other home games this season.",
                "{att} fans came out at {a} for the {t}, {d} more than the {h} have drawn on an average night there this season ({b} in their other home games).",
                "The {t} were the night's best road draw: {att} at {a}, against {b} for the {h}{hs} other home games this season.",
            ], date, "draw").format(**v)
            img = {"t": r["away"]}
            if ts:
                text += " " + pick([
                    "{p} led them with {n} points.",
                    "{p} gave them {n} points.",
                    "{p} put up {n} for the visitors.",
                    "{p} scored {n}.",
                    "{p} was their top scorer with {n}.",
                ], date, "draw-scorer").format(p=P(ts[0], ts[1]), n=ts[2])
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
    notes.append({"k": "crowd", "img": {"t": r["home"]}, "t": pick([
        "Biggest crowd of the night: {att} at {w} for the {aw} at the {h}{tail}.",
        "{w} had the night's biggest house, {att} for the {aw} at the {h}{tail}.",
        "The largest crowd in the league was at {w}: {att} for the {aw} at the {h}{tail}.",
        "No arena held more people than {w}, where {att} watched the {aw} at the {h}{tail}.",
    ], date, "crowd").format(att=fmt(r["attendance"]), w=where, aw=T(r["away"]), h=T(r["home"]), tail=tail)})

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
    for i, (pts, pid, name, b, games, best) in enumerate(sorted(highs, reverse=True)[:3]):
        n = {"k": "high", "img": {"p": pid}, "t": pick([
            "{p} scored {pts} at {a}, his most there in {g1} games (previous best: {best}).",
            "{p} had never scored more than {best} at {a} in {g} games. He put up {pts}.",
            "{pts} points for {p} at {a}, a new personal best in that building ({best} was his high over {g} earlier games).",
            "{ps} {pts} at {a} beat his old best there by {diff}.",
        ], (pd.Timestamp(date) + pd.Timedelta(days=i)).strftime("%Y-%m-%d"), "high").format(p=P(pid, name), ps=P(pid, name) + ("'" if name.endswith("s") else "'s"), pts=pts, a=A(b), g=games, g1=games + 1, best=best,
                                         diff=pts - best)}
        n["pts"] = pts
        notes.append(n)
        if featured is None:
            featured = n

    # 4. furthest below its average
    if len(dd) >= 2:
        r = dd.sort_values("delta").iloc[0]
        if r["delta"] < 0:
            notes.append({"k": "low", "img": {"t": r["home"]}, "t": pick([
                "Lightest crowd relative to normal: {att} at {a} for the {t}, {d} under what the {h}{hs} other home games this season have averaged ({b}).",
                "{a} drew {att} for the {t}, {d} below the {h}{hs} average for their other home games this season.",
                "The {t} didn't move the needle at {a}: {att}, {d} short of the {h}{hs} other home games this season ({b} on average).",
                "Quietest building compared with its norm: {a}, with {att} for the {t}. The {h} average {b} in their other home games this season.",
            ], date, "low").format(att=fmt(r["attendance"]), a=A(r["building"]), t=T(r["away"]),
                                   d=fmt(-r["delta"]), b=fmt(r["base"]), h=T(r["home"]), hs=poss(r["home"]))})

    # 5. sellouts and sellout runs
    sold = [r for _, r in df.iterrows() if isinstance(r["building"], str) and r["building"] in cap
            and r["attendance"] >= cap[r["building"]] * (1 - SELLOUT_WITHIN)]
    runs = []
    for b in {r["building"] for r in sold}:
        home = g[(g["building"] == b) & (g["date"] <= date) & (g["attendance"].fillna(0) > 0)]
        if len(home) >= 5 and (home["attendance"] >= cap[b] * (1 - SELLOUT_WITHIN)).all():
            runs.append((len(home), b))
    if sold:
        n_, N_ = len(sold), len(df)
        if n_ == N_:
            t = pick(["All {N} games were sellouts.", "Every one of the {N} games sold out.",
                      "{N} games, {N} sellouts."], date, "sold").format(N=N_)
        elif n_ == 1:
            t = pick(["1 of {N} games was a sellout.", "Only one of the {N} games sold out.",
                      "One sellout in {N} games."], date, "sold").format(N=N_)
        else:
            t = pick(["{n} of {N} games were sellouts.", "Sellouts: {n} of {N} games.",
                      "{n} of the {N} buildings were full.", "{n} sellouts across {N} games."],
                     date, "sold").format(n=n_, N=N_)
        if runs:
            n, b = max(runs)
            t += " " + pick(["{a} has sold out all {k} of its games this season.",
                             "{a} is {k} for {k} on sellouts this season.",
                             "Every game at {a} this season has been a sellout, {k} so far.",
                             "{a} hasn't had an empty seat to sell all season: {k} games, {k} sellouts."],
                            date, "run").format(a=A(b), k=n)
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
        cmp = ""
        if len(prev) >= 600:
            chg = 100 * (avg / prev["attendance"].mean() - 1)
            cmp = (f"{abs(chg):.1f}% {'above' if chg >= 0 else 'below'} {season_label(se - 1)}'s "
                   f"{fmt(prev['attendance'].mean())}")
        v = dict(avg=fmt(avg), n=len(season_att), c=cmp)
        t = pick([
            "League average so far: {avg} fans per game over {n} games" + (", {c}." if cmp else "."),
            "Through {n} games, NBA crowds average {avg}" + (", {c}." if cmp else "."),
            "The average NBA crowd this season is {avg} after {n} games" + (", {c}." if cmp else "."),
        ], date, "pace").format(**v)
        notes.append({"k": "pace", "t": t})

    # Lead with whatever stands out most, so the top of the page isn't the same
    # kind of note every morning: the league's biggest crowd of the season, then
    # a 35-point building best, then the night's biggest road draw.
    crowd_n = next((n for n in notes if n["k"] == "crowd"), None)
    high_n = next((n for n in notes if n["k"] == "high" and n.get("pts", 0) >= 35), None)
    if crowd_n and "biggest NBA crowd of the season" in crowd_n["t"]:
        featured = crowd_n
    elif high_n:
        featured = high_n
    for n in notes:
        n.pop("pts", None)

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
