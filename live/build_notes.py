#!/usr/bin/env python3
""""Last night at the gate": short notes for the front page, rebuilt every morning.

From the season in progress (data/live/<season>/) it writes docs/data/notes.json
with the latest game night's notes plus one-line summaries of the nights before:

  * the biggest road draw of the night (visiting team's crowd vs. that arena's
    other home games this season, named as such in the text);
  * the biggest crowd of the night, flagged when it's the arena's or the
    league's biggest of the season;
  * the crowd furthest below its arena's average;
  * players who scored more in a road building than ever before there (from the full
    history in output/ plus this season's earlier games);
  * sellouts, and arenas that have sold out every game this season;
  * the league's average crowd so far vs. last season.

Each kind of note has seven or eight phrasings that rotate by date, so the page
doesn't read the same every morning.

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
ROAD_MIN_GAMES = 4          # road games before a team can lead the road-draw note


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


def road_draw_deltas(g):
    """Every regular-season game with a crowd: the visitors, the date, and the crowd minus the
    home building's average in its other games this season (same comparison as the notes)."""
    rs = g[(g["gameType"] == "Regular Season") & (g["attendance"].fillna(0) > 0)].dropna(subset=["building"])
    tot = rs.groupby("building")["attendance"].agg(["sum", "count"])
    x = rs.join(tot, on="building")
    x = x[x["count"] - 1 >= MIN_BASELINE_GAMES]
    base = (x["sum"] - x["attendance"]) / (x["count"] - 1)
    return pd.DataFrame({"away": x["away"], "date": x["date"], "delta": x["attendance"] - base})


def building_bests():
    """(personId, building) -> (games, best) through last season, all game types."""
    r = pd.read_csv(os.path.join(ROOT, "output", "player_arena_records_2007.csv"), encoding="utf-8-sig",
                    usecols=["personId", "building", "games", "career_high"])
    agg = r.groupby(["personId", "building"]).agg(games=("games", "sum"), best=("career_high", "max"))
    return {k: (int(v.games), int(v.best)) for k, v in agg.iterrows()}


def night_notes(date, se, g, p, cap, bests, road_deltas):
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

    # 1. biggest road draw
    dd = df.dropna(subset=["delta"])
    featured = None
    if len(dd):
        r = dd.sort_values("delta", ascending=False).iloc[0]
        if r["delta"] > 0:
            # The comparison is always named in full: the home team's OTHER home games this season
            # (leave-one-out), so it never reads as the season average sites like ESPN show, which
            # include tonight. No "packed": that word is for a building close to full.
            v = dict(t=T(r["away"]), a=A(r["building"]), d=fmt(r["delta"]), att=fmt(r["attendance"]),
                     b=fmt(r["base"]), h=T(r["home"]), hs=poss(r["home"]))
            text = pick([
                "The {t} drew {att} at {a}, {d} more than the {h} have averaged in their other home games this season ({b}).",
                "{att} at {a} for the {t}, {d} above the {h}{hs} average in their other home games this season ({b}).",
                "Best road draw of the night: the {t}, with {att} at {a}. The {h} have averaged {b} in their other home games.",
                "The {h} had {att} at {a} for the {t}, {d} more than their average in other home games this season.",
                "{a} drew {att} for the {t}. The {h}{hs} other home games this season have averaged {b}.",
                "Biggest jump over a home average: {att} at {a} for the {t}, up {d} on the {h}{hs} average in their other home games this season ({b}).",
                "{att} came out at {a} for the {t}, compared with the {b} the {h} have averaged in their other home games.",
                "The {t} pushed attendance at {a} to {att}, {d} above the {h}{hs} average in their other home games ({b}).",
            ], date, "draw").format(**v)
            img = {"t": r["away"]}
            featured = {"k": "draw", "t": text, "img": img, "b": r["building"]}
            notes.append(featured)

    # 2. biggest crowd of the night
    r = df.sort_values("attendance", ascending=False).iloc[0]
    prior_b = earlier[(earlier["building"] == r["building"]) & (earlier["attendance"].fillna(0) > 0)]
    prior_all = earlier[earlier["attendance"].fillna(0) > 0]
    tail = ""
    if len(prior_all) >= 20 and r["attendance"] > prior_all["attendance"].max():
        tail = " It was the biggest NBA crowd of the season."
    elif len(prior_b) >= 2 and r["attendance"] > prior_b["attendance"].max():
        tail = " It was the building's biggest crowd of the season."
    where = A(r["building"]) if isinstance(r["building"], str) else r["arenaName"]
    notes.append({"k": "crowd", "img": {"t": r["home"]}, "t": pick([
        "Biggest crowd of the night: {att} at {w} for the {aw} at the {h}.{tail}",
        "{w} had the night's biggest crowd, {att} for the {aw} at the {h}.{tail}",
        "The largest crowd in the league was at {w}: {att} for the {aw} at the {h}.{tail}",
        "Top attendance of the night: the {aw} at the {h}, {att} at {w}.{tail}",
        "The {h} hosted the {aw} in front of {att} at {w}, the night's largest crowd.{tail}",
        "Most fans in one building: {att} at {w}, for the {aw} at the {h}.{tail}",
        "{att} were at {w} for the {aw} against the {h}, more than at any other game.{tail}",
        "{w} led the night with {att} for the {aw} at the {h}.{tail}",
    ], date, "crowd").format(att=fmt(r["attendance"]), w=where, aw=T(r["away"]), h=T(r["home"]), tail=tail)})

    # 3. building bests
    highs = []
    seen_live = defaultdict(lambda: [0, 0])          # this season before tonight
    ep = p[p["gameId"].isin(earlier["gameId"])].merge(earlier[["gameId", "building"]], on="gameId")
    for (pid, b), sub in ep.groupby(["personId", "building"]):
        seen_live[(int(pid), b)] = [len(sub), int(sub["points"].max())]
    np_ = p[p["gameId"].isin(night["gameId"])].merge(night[["gameId", "building", "hometeamId"]], on="gameId")
    for _, s in np_[np_["points"] >= HIGH_MIN_POINTS].iterrows():
        if not isinstance(s["building"], str):
            continue
        # Road buildings only: a best in a player's own home arena is just his best home night.
        if s["playerteamId"] == s["hometeamId"]:
            continue
        pid = int(s["personId"])
        g0, b0 = bests.get((pid, s["building"]), (0, 0))
        g1, b1 = seen_live[(pid, s["building"])]
        games, best = g0 + g1, max(b0, b1)
        if games >= HIGH_MIN_PRIOR_GAMES and s["points"] > best:
            highs.append((int(s["points"]), pid, f"{s['firstName']} {s['lastName']}".strip(), s["building"], games, best))
    for i, (pts, pid, name, b, games, best) in enumerate(sorted(highs, reverse=True)[:1]):  # one at most: this is an attendance page
        n = {"k": "high", "img": {"p": pid}, "t": pick([
            "{p} scored {pts} at {a}, his most there in {g1} games (previous best: {best}).",
            "{p} had never scored more than {best} at {a} in {g} games. This time he had {pts}.",
            "{pts} points for {p} at {a}, a career high in that building. His previous best there was {best}, over {g} games.",
            "{ps} {pts} at {a} beat his old best there by {diff}.",
            "{p} set a building high at {a} with {pts} points. He had topped out at {best} in {g} earlier games there.",
            "New best for {p} at {a}: {pts} points, {diff} more than his previous high there.",
            "In {g} earlier games at {a}, {p} had never gone past {best}. He scored {pts}.",
            "{p} scored {pts} at {a}, his career high in that arena (old mark: {best} in {g} games).",
        ], (pd.Timestamp(date) + pd.Timedelta(days=i)).strftime("%Y-%m-%d"), "high").format(p=P(pid, name), ps=P(pid, name) + ("'" if name.endswith("s") else "'s"), pts=pts, a=A(b), g=games, g1=games + 1, best=best,
                                         diff=pts - best)}
        n["pts"] = pts
        notes.append(n)

    # 4. furthest below its average
    if len(dd) >= 2:
        r = dd.sort_values("delta").iloc[0]
        if r["delta"] < 0:
            notes.append({"k": "low", "img": {"t": r["home"]}, "t": pick([
                "Lightest crowd relative to normal: {att} at {a} for the {t}, {d} under the {h}{hs} average in their other home games this season ({b}).",
                "{a} drew {att} for the {t}, {d} below the {h}{hs} average in their other home games this season.",
                "The {t} drew {att} at {a}, {d} short of the {h}{hs} average in their other home games ({b}).",
                "Biggest drop from a home average: {att} at {a} for the {t}. The {h} average {b} in their other home games this season.",
                "The {h} had {att} at {a} for the {t}, {d} fewer than they've averaged in their other home games.",
                "{att} at {a} for the {t}, down {d} from the {b} the {h} have averaged in their other home games this season.",
                "Weakest draw against a home average: the {t}, with {att} at {a}. The {h}{hs} other home games have averaged {b}.",
                "{a} came in {d} below the {h}{hs} average for their other home games ({b}), with {att} for the {t}.",
            ], date, "low").format(att=fmt(r["attendance"]), a=A(r["building"]), t=T(r["away"]),
                                   d=fmt(-r["delta"]), b=fmt(r["base"]), h=T(r["home"]), hs=poss(r["home"]))})

    # 4b. smallest crowd of the night (a different building from the light-crowd note)
    low_r = dd.sort_values("delta").iloc[0] if len(dd) >= 2 else None
    low_b = low_r["building"] if low_r is not None and low_r["delta"] < 0 else None
    if len(df) >= 3:
        r = df.sort_values("attendance").iloc[0]
        draw_b = next((n["b"] for n in notes if n["k"] == "draw"), None)
        if r["building"] not in (low_b, draw_b):
            where = A(r["building"]) if isinstance(r["building"], str) else r["arenaName"]
            notes.append({"k": "quiet", "img": {"t": r["home"]}, "t": pick([
                "Smallest crowd of the night: {att} at {a} for the {t} at the {h}.",
                "The lightest house was at {a}, where {att} saw the {h} host the {t}.",
                "{a} had the night's smallest crowd, {att} for the {t} at the {h}.",
                "Fewest fans in one building: {att} at {a}, for the {t} at the {h}.",
                "The {h} drew {att} at {a} against the {t}, the smallest crowd of the night.",
                "At the other end of the list: {att} at {a} for the {t} at the {h}.",
                "Bottom of the night's attendance: the {t} at the {h}, {att} at {a}.",
                "The {t} at the {h} drew the night's smallest crowd, {att} at {a}.",
            ], date, "quiet").format(att=fmt(r["attendance"]), a=where, t=T(r["away"]), h=T(r["home"]))})

    # 4c. a building's biggest crowd of the season (not the night's biggest, which says so itself)
    top_b = df.sort_values("attendance", ascending=False).iloc[0]["building"]
    sh = []
    for _, r in df.iterrows():
        if not isinstance(r["building"], str) or r["building"] == top_b:
            continue
        prev = earlier[(earlier["building"] == r["building"]) & (earlier["attendance"].fillna(0) > 0)]
        if len(prev) >= MIN_BASELINE_GAMES and r["attendance"] > prev["attendance"].max():
            sh.append((r["attendance"] - prev["attendance"].max(), r, int(prev["attendance"].max()), len(prev)))
    if sh:
        _, r, prev, n = max(sh, key=lambda x: x[0])
        notes.append({"k": "seasonhigh", "img": {"t": r["home"]}, "t": pick([
            "{a} had its biggest crowd of the season, {att} for the {t}. The previous high there was {prev}.",
            "New season high at {a}: {att} for the {t} at the {h}, up from {prev}.",
            "The {h} drew {att} for the {t}, their best home crowd of the season (previous high: {prev}).",
            "{att} at {a} for the {t}, the most the {h} have drawn at home this season. The old mark was {prev}.",
            "Season-high crowd for the {h}: {att} at {a}, with the {t} in town (previous high: {prev}).",
            "The {t} brought out the {h}{hs} biggest home crowd so far, {att} at {a} (previous high: {prev}).",
            "{a} beat its season high with {att} for the {t}. The best before that was {prev}.",
            "Through {n} home games the {h} had topped out at {prev}. The {t} drew {att} at {a}.",
        ], date, "seasonhigh").format(att=fmt(r["attendance"]), a=A(r["building"]), t=T(r["away"]), h=T(r["home"]),
                                      hs=poss(r["home"]), prev=fmt(prev), n=n)})

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
            t = pick(["All {N} games were sellouts.",
                      "Every one of the {N} games sold out.",
                      "{N} games, {N} sellouts.",
                      "All {N} buildings were sold out.",
                      "Sellouts: {N} of {N}.",
                      "Each of the {N} games sold out.",
                      "Every game on the {N}-game slate was a sellout.",
                      "{N} for {N} on sellouts."], date, "sold").format(N=N_)
        elif n_ == 1:
            t = pick(["1 of {N} games was a sellout.",
                      "Only one of the {N} games sold out.",
                      "One sellout in {N} games.",
                      "Just one of the {N} buildings was sold out.",
                      "Of {N} games, one sold out.",
                      "Sellouts: 1 of {N}.",
                      "One full building out of {N}.",
                      "{N} games, one sellout."], date, "sold").format(N=N_, M=N_ - 1)
        else:
            t = pick(["{n} of {N} games were sellouts.",
                      "Sellouts: {n} of {N} games.",
                      "{n} of the {N} buildings were full.",
                      "{n} sellouts across {N} games.",
                      "Out of {N} games, {n} sold out.",
                      "{n} of the night's {N} games were sold out.",
                      "{N} games, {n} sellouts.",
                      "{n} sold-out buildings out of {N}."], date, "sold").format(n=n_, N=N_, m=N_ - n_)
        if runs:
            n, b = max(runs)
            t += " " + pick(["{a} has sold out all {k} of its games this season.",
                             "{a} is {k} for {k} on sellouts this season.",
                             "Every game at {a} this season has been a sellout, {k} so far.",
                             "{a} has sold out {k} straight to start the season.",
                             "{a}: {k} games this season, {k} sellouts.",
                             "{a} still hasn't had an unsold seat this season ({k} games).",
                             "That's {k} sellouts in {k} games at {a} this season.",
                             "{a} has been full for all {k} of its games this season."], date, "run").format(a=A(b), k=n)
        notes.append({"k": "sellouts", "t": t, "img": {"a": slugify(max(runs)[1])} if runs else None})

    # 5b. the season's best road draw so far, when the leader changes (and on Mondays)
    def road_leader(upto):
        x = road_deltas[road_deltas["date"] <= upto]
        agg = x.groupby("away")["delta"].agg(["mean", "count"])
        agg = agg[agg["count"] >= ROAD_MIN_GAMES]
        if agg.empty:
            return None
        t = agg["mean"].idxmax()
        return t, float(agg.loc[t, "mean"]), int(agg.loc[t, "count"])
    lead = road_leader(date)
    prev_dates = sorted(d for d in g.loc[g["attendance"].fillna(0) > 0, "date"].unique() if d < date)
    before = road_leader(prev_dates[-1]) if prev_dates else None
    if lead and lead[1] > 0 and (not before or before[0] != lead[0] or pd.Timestamp(date).weekday() == 0):
        notes.append({"k": "road", "img": {"t": lead[0]}, "t": pick([
            "Best road draw so far this season: the {t}, with {d} more fans per game than their hosts usually get ({n} road games).",
            "Through {n} road games, the {t} have drawn {d} more fans per game than their hosts average.",
            "The {t} lead the league as a road draw: {d} fans per game above their hosts' average, over {n} road games.",
            "The {t} are the top road draw of the season, adding {d} fans per game to their hosts' average ({n} games).",
            "Road draw leaders: the {t}, {d} fans per game above what their hosts average, after {n} road games.",
            "On the road, the {t} have lifted home crowds by {d} a game this season ({n} games).",
            "Home teams draw {d} more per game when the {t} visit, the most in the league ({n} road games).",
            "The {t} now lead the league in road draw: {d} extra fans per game across {n} road games.",
        ], date, "road").format(t=T(lead[0]), d=fmt(lead[1]), n=lead[2])})

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
            "{n} games in, the league is drawing {avg} a night" + (", {c}." if cmp else "."),
            "NBA crowds so far: {avg} per game across {n} games" + (", {c}." if cmp else "."),
            "On a typical night this season, {avg} people are in the building ({n} games so far" + (", {c})." if cmp else ")."),
            "Season to date, {n} games have averaged {avg} fans" + (", {c}." if cmp else "."),
            "The running average after {n} games: {avg} per night" + (", {c}." if cmp else "."),
        ], date, "pace").format(**v)
        notes.append({"k": "pace", "t": t})

    # Lead with whatever stands out most, so the top of the page isn't the same
    # kind of note every morning: the league's biggest crowd of the season, then
    # a 35-point building best, then the night's biggest road draw.
    # The headline is always about crowds: the league's biggest crowd of the season when there is
    # one, otherwise it rotates by date among the night's attendance notes so the top of the page
    # (and the "Earlier nights" list) doesn't lead with the same kind of note every day.
    crowd_n = next((n for n in notes if n["k"] == "crowd"), None)
    if crowd_n and "biggest NBA crowd of the season" in crowd_n["t"]:
        featured = crowd_n
    else:
        lead = [n for n in notes if n["k"] in ("draw", "crowd", "seasonhigh", "road")]
        if lead:
            featured = lead[pd.Timestamp(date).toordinal() % len(lead)]
    for n in notes:
        n.pop("pts", None)
        n.pop("b", None)
    order = ["draw", "crowd", "seasonhigh", "road", "low", "quiet", "sellouts", "high", "pace"]
    notes.sort(key=lambda n: order.index(n["k"]) if n["k"] in order else len(order))

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
    road_deltas = road_draw_deltas(g)
    dates = sorted({d for d in g.loc[g["attendance"].fillna(0) > 0, "date"]}, reverse=True)
    nights = []
    for d in dates[:NIGHTS_KEPT]:
        n = night_notes(d, se, g, p, cap, bests, road_deltas)
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
