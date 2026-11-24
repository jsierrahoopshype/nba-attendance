#!/usr/bin/env python3
"""Fetch the NBA season in progress from NBA.com's public CDN (no keys, no login).

Runs in the daily GitHub Action (cdn.nba.com is not reachable from every
sandbox). For every FINAL game of the current season that isn't stored yet it
reads the game's box score once and appends:

  data/live/<season_end>/games.csv    one row per game, Games.csv columns
  data/live/<season_end>/players.csv  one row per player who logged minutes,
                                      PlayerStatistics.csv columns

so the rest of the pipeline can treat the season exactly like the Kaggle files
it was built on. <season_end> is the season-ending year (2026-27 -> 2027), the
same key every build_* script derives from the game date.

Sources (both public JSON on cdn.nba.com, used by nba.com's own pages):
  https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json
  https://cdn.nba.com/static/json/liveData/boxscore/boxscore_<gameId>.json

Safe by construction: rows are only ever added for games the CDN marks final
(gameStatus 3) and whose box score carries players; a failed request leaves the
game for the next run. Files are rewritten only when their content changed.
"""

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIVE_DIR = os.path.join(ROOT, "data", "live")

SCHEDULE_URL = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
BOX_URL = "https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{gid}.json"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
}

# gameId prefix (digits 1-3 of the 10-digit id) -> Games.csv gameType label
GAME_TYPES = {"001": "Preseason", "002": "Regular Season", "003": "All-Star Game",
              "004": "Playoffs", "005": "Play-in Tournament", "006": "Emirates NBA Cup"}
KEEP_PREFIXES = ("002", "004", "005", "006")   # preseason and All-Star are never used

GAME_COLS = ["gameId", "gameDateTimeEst", "hometeamCity", "hometeamName", "hometeamId",
             "awayteamCity", "awayteamName", "awayteamId", "homeScore", "awayScore", "winner",
             "gameType", "gameSubtype", "gameLabel", "gameSubLabel", "seriesGameNumber",
             "attendance", "arenaId", "arenaName", "arenaCity", "arenaState", "officials",
             "gameDate"]
PLAYER_COLS = ["firstName", "lastName", "personId", "gameId", "gameDateTimeEst",
               "playerteamCity", "playerteamName", "opponentteamCity", "opponentteamName",
               "gameType", "gameLabel", "gameSubLabel", "seriesGameNumber", "win", "home",
               "numMinutes", "points", "assists", "blocks", "steals", "fieldGoalsAttempted",
               "fieldGoalsMade", "fieldGoalsPercentage", "threePointersAttempted",
               "threePointersMade", "threePointersPercentage", "freeThrowsAttempted",
               "freeThrowsMade", "freeThrowsPercentage", "reboundsDefensive",
               "reboundsOffensive", "reboundsTotal", "foulsPersonal", "turnovers",
               "plusMinusPoints", "playerteamId", "opponentteamId", "comment",
               "startingPosition", "gameDate"]
STAT_KEYS = ["points", "assists", "blocks", "steals", "fieldGoalsAttempted", "fieldGoalsMade",
             "fieldGoalsPercentage", "threePointersAttempted", "threePointersMade",
             "threePointersPercentage", "freeThrowsAttempted", "freeThrowsMade",
             "freeThrowsPercentage", "reboundsDefensive", "reboundsOffensive", "reboundsTotal",
             "foulsPersonal", "turnovers", "plusMinusPoints"]


def get_json(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):      # box score not published yet / not available
                raise
            last = e
        except Exception as e:            # timeouts, resets
            last = e
        time.sleep(2 * (i + 1))
    raise last


def season_end_of(date_str):
    """'2026-11-23...' -> 2027 (Aug..Dec belong to the next season-ending year)."""
    y, m = int(date_str[:4]), int(date_str[5:7])
    return y + 1 if m >= 8 else y


def et_stamp(s):
    """'2026-11-23T19:30:00Z' (the CDN's ET clock time, mislabeled Z) -> '2026-11-23 19:30:00'."""
    s = (s or "").replace("T", " ").replace("Z", "")
    return s[:19]


def minutes(iso):
    """'PT34M12.00S' -> 34.2 (minutes as a float, like numMinutes)."""
    m = re.match(r"PT(?:(\d+)M)?(?:([\d.]+)S)?", iso or "")
    if not m:
        return 0.0
    return int(m.group(1) or 0) + float(m.group(2) or 0) / 60.0


def read_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_rows(path, cols, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in cols})
    data = buf.getvalue()
    if os.path.exists(path) and open(path, encoding="utf-8").read() == data:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(data)
    return True


def convert(box, sched):
    """One CDN box score (+ its schedule entry) -> (game row, [player rows]).
    Returns (None, []) when the box score isn't a usable final."""
    g = box.get("game") or {}
    gid = str(g.get("gameId") or sched.get("gameId") or "")
    home, away = g.get("homeTeam") or {}, g.get("awayTeam") or {}
    if not gid or not home.get("players") or not away.get("players"):
        return None, []
    when = et_stamp(g.get("gameEt") or sched.get("gameDateTimeEst") or "")
    if not when:
        return None, []
    hs, as_ = int(home.get("score") or 0), int(away.get("score") or 0)
    if hs == as_:
        return None, []
    gtype = GAME_TYPES.get(gid[:3], "")
    arena = g.get("arena") or {}
    att = g.get("attendance")
    try:
        att = int(att) if att not in (None, "", 0, "0") else ""
    except (TypeError, ValueError):
        att = ""
    officials = ", ".join(o.get("name", "") for o in (g.get("officials") or []) if o.get("name"))
    game = {
        "gameId": str(int(gid)),
        "gameDateTimeEst": when,
        "hometeamCity": home.get("teamCity", ""), "hometeamName": home.get("teamName", ""),
        "hometeamId": home.get("teamId", ""),
        "awayteamCity": away.get("teamCity", ""), "awayteamName": away.get("teamName", ""),
        "awayteamId": away.get("teamId", ""),
        "homeScore": hs, "awayScore": as_,
        "winner": home.get("teamId") if hs > as_ else away.get("teamId"),
        "gameType": gtype, "gameSubtype": "",
        "gameLabel": sched.get("gameLabel", ""), "gameSubLabel": sched.get("gameSubLabel", ""),
        "seriesGameNumber": sched.get("seriesGameNumber", ""),
        "attendance": att,
        "arenaId": arena.get("arenaId", ""), "arenaName": arena.get("arenaName", ""),
        "arenaCity": arena.get("arenaCity", ""), "arenaState": arena.get("arenaState", ""),
        "officials": officials,
        "gameDate": when,
    }
    players = []
    for team, opp, is_home in ((home, away, 1), (away, home, 0)):
        won = 1 if (hs > as_) == bool(is_home) else 0
        for p in team.get("players") or []:
            st = p.get("statistics") or {}
            mins = minutes(st.get("minutes") or st.get("minutesCalculated"))
            if str(p.get("played", "1")) != "1" or mins <= 0:
                continue
            row = {
                "firstName": p.get("firstName", ""), "lastName": p.get("familyName", ""),
                "personId": p.get("personId", ""), "gameId": game["gameId"],
                "gameDateTimeEst": when,
                "playerteamCity": team.get("teamCity", ""), "playerteamName": team.get("teamName", ""),
                "opponentteamCity": opp.get("teamCity", ""), "opponentteamName": opp.get("teamName", ""),
                "gameType": gtype, "gameLabel": game["gameLabel"], "gameSubLabel": game["gameSubLabel"],
                "seriesGameNumber": game["seriesGameNumber"],
                "win": won, "home": is_home, "numMinutes": round(mins, 4),
                "playerteamId": team.get("teamId", ""), "opponentteamId": opp.get("teamId", ""),
                "comment": "", "startingPosition": p.get("position", "") if p.get("starter") == "1" else "",
                "gameDate": when,
            }
            for k in STAT_KEYS:
                row[k] = st.get(k, "")
            players.append(row)
    return game, players


def schedule_finals(sched_json):
    """(season label, [schedule game dicts that are final and of a kept type])."""
    ls = sched_json.get("leagueSchedule") or {}
    out = []
    for d in ls.get("gameDates") or []:
        for g in d.get("games") or []:
            gid = str(g.get("gameId") or "")
            if g.get("gameStatus") == 3 and gid[:3] in KEEP_PREFIXES:
                out.append(g)
    return ls.get("seasonYear", ""), out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--max-games", type=int, default=600, help="box scores to read per run")
    ap.add_argument("--schedule-file", help="read the schedule from a local file (tests)")
    ap.add_argument("--box-dir", help="read box scores from <dir>/boxscore_<gid>.json (tests)")
    ap.add_argument("--live-dir", default=LIVE_DIR)
    args = ap.parse_args()

    if args.schedule_file:
        sched = json.load(open(args.schedule_file, encoding="utf-8"))
    else:
        sched = get_json(SCHEDULE_URL)
    season_label, finals = schedule_finals(sched)
    if not finals:
        print(f"schedule {season_label}: no final games yet")
        return 0

    by_season = {}
    for g in finals:
        se = season_end_of(g.get("gameDateTimeEst") or g.get("gameDateEst") or "")
        by_season.setdefault(se, []).append(g)

    summary = {}
    for se, games in sorted(by_season.items()):
        d = os.path.join(args.live_dir, str(se))
        grows = read_rows(os.path.join(d, "games.csv"))
        prows = read_rows(os.path.join(d, "players.csv"))
        have = {r["gameId"] for r in grows}
        todo = [g for g in games if str(int(g["gameId"])) not in have]
        added, missing = 0, []
        for g in todo[: args.max_games]:
            gid = g["gameId"]
            try:
                if args.box_dir:
                    box = json.load(open(os.path.join(args.box_dir, f"boxscore_{gid}.json"), encoding="utf-8"))
                else:
                    box = get_json(BOX_URL.format(gid=gid))
                    time.sleep(0.25)
            except Exception as e:
                missing.append(gid)
                print(f"  box score {gid} not available yet ({e})")
                continue
            game, players = convert(box, g)
            if not game:
                missing.append(gid)
                continue
            grows.append(game)
            prows.extend(players)
            added += 1
        grows.sort(key=lambda r: (r["gameDateTimeEst"], int(r["gameId"])))
        prows.sort(key=lambda r: (r["gameDateTimeEst"], int(r["gameId"]), -int(r["home"]),
                                  r["lastName"], r["firstName"]))
        write_rows(os.path.join(d, "games.csv"), GAME_COLS, grows)
        write_rows(os.path.join(d, "players.csv"), PLAYER_COLS, prows)
        no_att = sum(1 for r in grows if r["gameType"] in ("Regular Season", "Playoffs") and not r["attendance"])
        summary[se] = {"games": len(grows), "added": added, "waiting": len(missing),
                       "without_attendance": no_att,
                       "last_game": grows[-1]["gameDateTimeEst"][:10] if grows else ""}
        print(f"season {se}: {len(grows)} games stored (+{added} new, {len(missing)} waiting, "
              f"{no_att} without attendance), {len(prows)} player rows")
    gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            for se, s in summary.items():
                f.write(f"- Season {se - 1}-{str(se)[2:]}: {s['games']} games stored, +{s['added']} new, "
                        f"{s['waiting']} waiting, {s['without_attendance']} without attendance, "
                        f"last game {s['last_game']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
