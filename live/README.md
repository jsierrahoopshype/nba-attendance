# Season in progress: daily build

Live page: https://hoopsmatic.com/nba-attendance/

The daily GitHub Action (`.github/workflows/daily.yml`, 09:17 and 13:47 UTC) keeps the section current without anyone running anything:

1. `fetch_live_season.py` downloads `Games.csv` and `PlayerStatistics.csv` from the public Kaggle dataset the section was built on (`eoinamoore/historical-nba-data-and-player-box-scores`, no account or key needed) and keeps the games newer than the committed history: `data/live/<season-ending year>/games.csv` and `players.csv`.
2. `build_heads.py` lists the nba-headshots files (names only) and writes `data/heads.json` (personId to headshot file).
3. `build_live_season.py` runs the usual `build_*.py` scripts on the season in progress alone and layers the result on `output/` (the full history from the local build, which it never modifies; the combined copy goes to `output_live/`, not committed), then runs `generate_dashboard_data.py`, which writes `docs/data/` and the arena, city and player pages.
4. `build_notes.py` writes the front page's "Last night at the gate" notes (`docs/data/notes.json`).
5. The Action commits only `data/live/`, `data/heads.json` and `data/sitemap_lastmod.json`, then publishes `docs/` to GitHub Pages.

Publishing needs one setting: https://github.com/jsierrahoopshype/nba-attendance/settings/pages, Source: **GitHub Actions**. Until it is set, the run summary says so and the site keeps showing the committed `docs/`.

## Local rebuilds

The local six-script rebuild (with the full 380 MB `PlayerStatistics.csv`) still works as before and refreshes `output/`. A season that `output/` already contains is never layered on again, so a local rebuild that includes the season in progress freezes that season at the local copy until the next season starts. Rebuild locally with the season in progress filtered out (games before August of the current season), or leave the current season to the Action.
