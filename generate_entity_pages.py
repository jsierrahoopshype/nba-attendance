#!/usr/bin/env python3
"""Generate physical per-entity HTML pages for the GitHub Pages dashboard.

docs/arena.html, docs/city.html and docs/player.html are single reusable
shells: the same markup/CSS/JS renders whichever arena/city/player the
?slug=/?id= query string names, fetched client-side. That's great for the
app itself but means every entity shares one <title>, no real <meta
description>, and an empty <h1> until JS runs — bad for links shared before
JS executes (crawlers, previews, view-source).

This script turns each entity into a real static file — docs/arenas/{slug}.html,
docs/cities/{slug}.html, docs/players/{personId}.html — by taking the existing
shell verbatim and doing pure text surgery on it:
  - <title>, a new <meta name="description">, and a new <link rel="canonical">
    are baked in with the real entity name, using the exact title formats
    docs/{arena,city,player}.html already establish.
  - The empty `<div id="head"></div>` placeholder gets a real starter <h1>,
    so view-source (and anything that doesn't run JS) sees actual content.
  - Asset/nav paths (styles.css, app.js, the "back" link) are rewritten one
    level up, since these files live in a subdirectory of docs/.

No markup, CSS, or JS logic is duplicated or reimplemented: the shell is read
from disk and lightly patched, so the interactive page (tables, season
picker, draw data, All-Star toggle, etc.) is byte-for-byte the same code as
docs/{arena,city,player}.html — it still fetches the same JSON via the same
functions. app.js itself detects which depth it's running at (ROOT) and
adjusts DATA/entity hrefs accordingly; nothing here needs to know that.

The old docs/{arena,city,player}.html?slug=/?id= pages are untouched by this
script — they still exist and now just redirect to the canonical page (see
their boot()), so links already shared or tested against the query-string
form keep working.
"""

import json
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(HERE, "docs")


def _esc(s):
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _js_str(s):
    """Safe to drop into a double-quoted JS string literal inside a <script> tag."""
    return (str(s)
            .replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("</", "<\\/"))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _bake(template, *, title, description, canonical, name_html, entity_global):
    """Patch one shell template into a self-contained static page for one entity.

    entity_global is a raw `<script>window.ENTITY_X = "...";</script>` tag —
    the shared shell's boot() prefers this over the ?slug=/?id= query string
    (see docs/{arena,city,player}.html), so the exact same script renders a
    real entity here with no query string at all.
    """
    out = re.sub(
        r"<title>.*?</title>",
        "<title>" + title + "</title>\n"
        '<meta name="description" content="' + description + '">\n'
        '<link rel="canonical" href="' + canonical + '">',
        template, count=1, flags=re.S,
    )
    # These pages live one directory below docs/ (docs/arenas/, docs/cities/,
    # docs/players/), so every relative reference the shell has to itself or
    # its siblings needs one more "../".
    out = out.replace('href="styles.css"', 'href="../styles.css"')
    out = out.replace(
        '<script src="app.js"></script>',
        entity_global + '\n<script src="../app.js"></script>', 1,
    )
    out = out.replace('href="index.html"', 'href="../index.html"')
    out = out.replace(
        '<div id="head"></div>',
        '<div id="head"><div class="ehead"><h1>' + name_html + "</h1></div></div>", 1,
    )
    return out


def generate_arena_pages(docs_dir=DOCS_DIR):
    template = _read(os.path.join(docs_dir, "arena.html"))
    index = json.load(open(os.path.join(docs_dir, "data", "buildings", "index.json"), encoding="utf-8"))
    out_dir = os.path.join(docs_dir, "arenas")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    for b in index:
        slug, name, city = b["slug"], b["name"], b["city"]
        title = _esc(name + " Attendance & Records | NBA Attendance Tracker")
        description = _esc(
            "Attendance records, win/loss splits, and road-team draw history for "
            + name + " in " + city + "."
        )
        entity_global = '<script>window.ENTITY_SLUG = "' + _js_str(slug) + '";</script>'
        html = _bake(template, title=title, description=description,
                     canonical=slug + ".html", name_html=_esc(name),
                     entity_global=entity_global)
        _write(os.path.join(out_dir, slug + ".html"), html)
    return len(index)


def generate_city_pages(docs_dir=DOCS_DIR):
    template = _read(os.path.join(docs_dir, "city.html"))
    index = json.load(open(os.path.join(docs_dir, "data", "cities", "index.json"), encoding="utf-8"))
    out_dir = os.path.join(docs_dir, "cities")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    for c in index:
        slug, name = c["slug"], c["name"]
        title = _esc(name + " NBA Arena Records | NBA Attendance Tracker")
        description = _esc(
            "NBA arena attendance and records for every building in " + name + "."
        )
        entity_global = '<script>window.ENTITY_SLUG = "' + _js_str(slug) + '";</script>'
        html = _bake(template, title=title, description=description,
                     canonical=slug + ".html", name_html=_esc(name),
                     entity_global=entity_global)
        _write(os.path.join(out_dir, slug + ".html"), html)
    return len(index)


def generate_player_pages(docs_dir=DOCS_DIR):
    template = _read(os.path.join(docs_dir, "player.html"))
    index = json.load(open(os.path.join(docs_dir, "data", "players", "index.json"), encoding="utf-8"))
    out_dir = os.path.join(docs_dir, "players")
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    for p in index:
        pid, name = str(p["personId"]), p["name"]
        title = _esc(name + " Arena Records & Road Draw | NBA Attendance Tracker")
        description = _esc(
            "Career arena attendance draw and building-by-building records for " + name + "."
        )
        entity_global = '<script>window.ENTITY_ID = "' + _js_str(pid) + '";</script>'
        html = _bake(template, title=title, description=description,
                     canonical=pid + ".html", name_html=_esc(name),
                     entity_global=entity_global)
        _write(os.path.join(out_dir, pid + ".html"), html)
    return len(index)


def generate_entity_pages(docs_dir=DOCS_DIR, kinds=("arenas", "cities", "players")):
    counts = {}
    if "arenas" in kinds:
        counts["arenas"] = generate_arena_pages(docs_dir)
    if "cities" in kinds:
        counts["cities"] = generate_city_pages(docs_dir)
    if "players" in kinds:
        counts["players"] = generate_player_pages(docs_dir)
    return counts


if __name__ == "__main__":
    counts = generate_entity_pages()
    print("entity pages: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
