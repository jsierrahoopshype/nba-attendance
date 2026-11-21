#!/usr/bin/env python3
"""Build docs/sitemap.xml for https://hoopsmatic.com/nba-attendance/.

Lists every indexable page: the hub, every arena page, every city page and
every real player page. Left out: the old query-string shells
(docs/arena.html, city.html, player.html, which only redirect), the
bare-personId player redirect stubs, and any page whose robots meta says
noindex.

<lastmod> is per URL and moves only when that page's content changes. A
page's fingerprint = its HTML (minus favicon links and social-preview tags,
which are site chrome) + the data JSON it renders. Fingerprints and dates
live in data/sitemap_lastmod.json (committed). A URL keeps its stored date
while its fingerprint is unchanged and gets today's date (UTC) when it
changes or is new. URLs no longer in the site drop out.
"""

import datetime
import hashlib
import json
import os
import re
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(HERE, "docs")
LASTMOD_FILE = os.path.join(HERE, "data", "sitemap_lastmod.json")
SITE = "https://hoopsmatic.com/nba-attendance/"

_CHROME = re.compile(
    r'<link rel="(?:icon|apple-touch-icon)"[^>]*>\n?'
    r'|<meta (?:property="og:|name="twitter:)[^>]*>\n?')
_NOINDEX = re.compile(r'<meta\s+name="robots"\s+content="[^"]*noindex', re.I)
_CANON = re.compile(r'<link rel="canonical" href="([^"]+)"')


def _pages(docs_dir):
    """(url, html path, data JSON path) for every indexable page."""
    yield SITE, os.path.join(docs_dir, "index.html"), os.path.join(docs_dir, "data", "frontpage.json")
    for folder, data_dir in (("arenas", "buildings"), ("cities", "cities"), ("players", "players")):
        d = os.path.join(docs_dir, folder)
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".html"):
                continue
            path = os.path.join(d, fn)
            url = SITE + folder + "/" + fn
            page = open(path, encoding="utf-8").read()
            m = _CANON.search(page)
            # Only self-canonical pages: this drops the player redirect stubs,
            # whose canonical points at the name-slugged page.
            if not m or m.group(1) != url:
                continue
            if _NOINDEX.search(page):
                continue
            # Data file the page renders: arenas/cities by slug, players by id
            # (the trailing number of "name-slug-personId").
            stem = fn[:-5]
            key = stem.rsplit("-", 1)[-1] if folder == "players" else stem
            yield url, path, os.path.join(docs_dir, "data", data_dir, key + ".json")


def generate_sitemap(docs_dir=DOCS_DIR, lastmod_file=LASTMOD_FILE):
    try:
        old = json.load(open(lastmod_file, encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    new = {}
    for url, path, data_path in _pages(docs_dir):
        page = open(path, encoding="utf-8").read()
        if _NOINDEX.search(page):
            continue
        h = hashlib.sha1(_CHROME.sub("", page).encode("utf-8"))
        if os.path.exists(data_path):
            with open(data_path, "rb") as f:
                h.update(f.read())
        fp = h.hexdigest()[:16]
        prev = old.get(url)
        new[url] = prev if prev and prev[0] == fp else [fp, today]
    os.makedirs(os.path.dirname(lastmod_file), exist_ok=True)
    with open(lastmod_file, "w", encoding="utf-8") as f:
        json.dump(new, f, indent=0, sort_keys=True)
        f.write("\n")
    with open(os.path.join(docs_dir, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        for url in sorted(new, key=lambda u: (u != SITE, u)):
            f.write("  <url><loc>" + escape(url) + "</loc><lastmod>" + new[url][1] + "</lastmod></url>\n")
        f.write("</urlset>\n")
    return len(new)


if __name__ == "__main__":
    print("sitemap.xml: " + str(generate_sitemap()) + " urls")
