"use strict";
/* Shared helpers for the dashboard + detail pages.
   Relative paths throughout so the site works from any GitHub Pages subpath. */

const DATA = "./data";

/* ---------- fetch / format ---------- */
async function getJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(path + " → " + r.status);
  return r.json();
}
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtInt = n => (n == null || n === "") ? "" : Math.round(Number(n)).toLocaleString();
const fmt1 = n => (n == null) ? "" : Number(n).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const pct = n => (n == null) ? "" : (Number(n) * 100).toFixed(1) + "%";
const signed = n => (n > 0 ? "+" : "") + Math.round(Number(n)).toLocaleString();
const deltaCls = n => n > 0 ? "pos" : (n < 0 ? "neg" : "flat");
const seasons = (a, b) => a === b ? String(a) : a + "–" + b;

/* ---------- entity links (relative, work from index or detail pages) ---------- */
const playerHref = id => "player.html?id=" + encodeURIComponent(id);
const arenaHref = slug => "arena.html?slug=" + encodeURIComponent(slug);
const cityHref = slug => "city.html?slug=" + encodeURIComponent(slug);
const playerLink = (id, name) => '<a href="' + playerHref(id) + '">' + esc(name) + "</a>";
const arenaLink = (slug, name) => '<a href="' + arenaHref(slug) + '">' + esc(name) + "</a>";
const cityLink = (slug, name) => '<a href="' + cityHref(slug) + '">' + esc(name) + "</a>";
const slugify = name => String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

/* ---------- sortable table ----------
   cols: [{key, label, align:'left'|'right', sortable:bool, defaultDir:'asc'|'desc',
           get(row) -> sort value (number or string), cell(row) -> html, tdcls(row)}]
   Every column is sortable unless sortable:false.
   opts.ranked: prepend a rank column (table) / rank badge (card) reflecting
   the row's position in the current sort — for simple ranked lists (frontpage
   boxes, "Most Points"-style mini leaderboards), not detailed multi-column
   tables where sort order isn't "the" ranking. */
function sortableTable(mount, cols, rows, initial, opts) {
  opts = opts || {};
  let sort = initial || firstSortable(cols);

  function firstSortable(cs) {
    const c = cs.find(x => x.sortable !== false);
    return { key: c.key, dir: c.defaultDir || "desc" };
  }
  function draw() {
    const col = cols.find(c => c.key === sort.key);
    let arr = rows.slice();
    if (col && col.get) {
      arr.sort((a, b) => {
        let x = col.get(a), y = col.get(b);
        if (typeof x === "string" || typeof y === "string") {
          const r = String(x).localeCompare(String(y));
          return sort.dir === "desc" ? -r : r;
        }
        x = (x == null ? -Infinity : x); y = (y == null ? -Infinity : y);
        return sort.dir === "desc" ? (y - x) : (x - y);
      });
    }
    const rankHead = opts.ranked ? '<th class="rank"></th>' : "";
    const head = rankHead + cols.map(c => {
      const left = c.align === "left" ? " left" : "";
      if (c.sortable === false) return '<th class="' + left.trim() + '">' + esc(c.label) + "</th>";
      const sb = sort.key === c.key ? " sorted-by" : "";
      const arrow = sort.key === c.key ? '<span class="arr">' + (sort.dir === "desc" ? "▼" : "▲") + "</span>" : "";
      return '<th class="sortable' + left + sb + '" data-k="' + esc(c.key) + '">' + esc(c.label) + arrow + "</th>";
    }).join("");
    const body = arr.map((row, i) => {
      const href = row._href ? ' data-href="' + esc(row._href) + '"' : "";
      const rc = opts.rowClass ? opts.rowClass(row) : "";
      const rowCls = rc ? ' class="' + esc(rc) + '"' : "";
      const rankCell = opts.ranked ? '<td class="rank">' + (i + 1) + "</td>" : "";
      const tds = rankCell + cols.map(c => {
        const cls = [];
        if (c.align === "left") cls.push("name");
        if (c.tdcls) { const x = c.tdcls(row); if (x) cls.push(x); }
        if (sort.key === c.key) cls.push("sorted-by");
        return '<td' + (cls.length ? ' class="' + cls.join(" ") + '"' : "") + ">" + c.cell(row) + "</td>";
      }).join("");
      return "<tr" + rowCls + href + ">" + tds + "</tr>";
    }).join("");
    mount.innerHTML =
      '<div class="table-wrap"><table class="lb"><thead><tr>' + head +
      "</tr></thead><tbody>" + (body || '<tr><td class="dash">No data</td></tr>') + "</tbody></table></div>" +
      cardsHtml(cols, arr, opts);
  }
  draw();
  mount.addEventListener("click", e => {
    const th = e.target.closest("th.sortable");
    if (!th) return;
    const key = th.dataset.k;
    const col = cols.find(c => c.key === key);
    if (sort.key === key) sort = { key, dir: sort.dir === "desc" ? "asc" : "desc" };
    else sort = { key, dir: (col && col.defaultDir) || "desc" };
    draw();
  });
  return { setRows(r) { rows = r; draw(); } };
}

/* Mobile card fallback for a sortable table — same cols/cell renderers, no
   separate markup to maintain. Hidden on desktop, shown in place of
   .table-wrap under the 860px breakpoint (see styles.css).
   opts.alwaysVisible renders the ".cards.always" variant — a responsive
   multi-column grid shown at every width, not just under the mobile
   breakpoint (used for the "you might also like" related-entity footers,
   which are cards-only, with no companion desktop table).
   opts.ranked switches to the compact row-list format below, for simple
   ranked lists (frontpage boxes, "Most Points"-style mini leaderboards) —
   everything else (detailed multi-column tables, draw tables) keeps the
   block-card format, since those rows carry more than a name + 1-2 numbers. */
function cardsHtml(cols, rows, opts) {
  opts = opts || {};
  if (!rows.length) return "";
  const titleCol = cols.find(c => c.align === "left") || cols[0];
  const restCols = cols.filter(c => c !== titleCol);

  if (opts.ranked) return rankedRowsHtml(titleCol, restCols, rows, opts);

  const items = rows.map(row => {
    const href = row._href ? ' data-href="' + esc(row._href) + '"' : "";
    const rc = opts.rowClass ? opts.rowClass(row) : "";
    const rowCls = rc ? " " + esc(rc) : "";
    const stats = restCols.map(c =>
      '<div class="card-stat"><span class="card-lbl">' + esc(c.label) + '</span>' +
      '<span class="card-val">' + c.cell(row) + "</span></div>").join("");
    return '<div class="card' + rowCls + '"' + href + '>' +
      '<div class="card-title">' + titleCol.cell(row) + "</div>" +
      '<div class="card-grid">' + stats + "</div></div>";
  }).join("");
  const cls = "cards" + (opts.alwaysVisible ? " always" : "");
  return '<div class="' + cls + '">' + items + "</div>";
}

/* Compact ranked-list rows: rank, name (wraps, never truncates), values
   right-aligned — one line per row (two if a long name wraps), instead of a
   bordered block per row with the field label repeated every time. Field
   meaning is established ONCE via a slim uppercase header row, matching
   nba-polymarket's own micro-label sizing (.6rem) measured at 390px.

   Lists shaped like games/mean_delta/total_delta (all-time draw kings, and
   any other ranked list built from the same per-game+total metric pair) skip
   the header row entirely: at mobile width a long name already wraps the row
   to 2 lines, which puts the header's column positions out of sync with the
   values under it. Instead each row self-labels on its own second line —
   "+255,027 total extra fans (+498/g · 512 g)" — the same inline pattern the
   arena stat-card sub-line already uses ("62.5% · 16 g · Regular Season"), so
   no column-to-value correspondence needs to survive a wrap. */
function rankedRowsHtml(titleCol, restCols, rows, opts) {
  const gCol = restCols.find(c => c.key === "games");
  const meanCol = restCols.find(c => c.key === "mean_delta");
  const totalCol = restCols.find(c => c.key === "total_delta");
  if (meanCol && totalCol && restCols.length <= 3) return selfLabelRowsHtml(titleCol, gCol, meanCol, totalCol, rows, opts);

  const headVals = restCols.map(c => '<span class="rval">' + esc(c.label) + "</span>").join("");
  const head = '<div class="rrow rrow-head"><span class="rrank"></span><span class="rname"></span>' +
    '<span class="rvals">' + headVals + "</span></div>";
  const items = rows.map((row, i) => {
    const href = row._href ? ' data-href="' + esc(row._href) + '"' : "";
    const rc = opts.rowClass ? opts.rowClass(row) : "";
    const rowCls = rc ? " " + esc(rc) : "";
    const vals = restCols.map(c => '<span class="rval">' + c.cell(row) + "</span>").join("");
    return '<div class="rrow' + rowCls + '"' + href + '>' +
      '<span class="rrank">' + (i + 1) + '</span>' +
      '<span class="rname">' + titleCol.cell(row) + "</span>" +
      '<span class="rvals">' + vals + "</span></div>";
  }).join("");
  return '<div class="cards rlist">' + head + items + "</div>";
}

function selfLabelRowsHtml(titleCol, gCol, meanCol, totalCol, rows, opts) {
  const totalLabel = esc((totalCol.label || "total").toLowerCase());
  const items = rows.map((row, i) => {
    const href = row._href ? ' data-href="' + esc(row._href) + '"' : "";
    const rc = opts.rowClass ? opts.rowClass(row) : "";
    const rowCls = rc ? " " + esc(rc) : "";
    const secondary = [meanCol.cell(row) + "/g", gCol ? gCol.cell(row) + " g" : ""].filter(Boolean).join(" · ");
    return '<div class="rrow rrow-self' + rowCls + '"' + href + '>' +
      '<div class="rrow-top"><span class="rrank">' + (i + 1) + '</span>' +
      '<span class="rname">' + titleCol.cell(row) + "</span></div>" +
      '<div class="rrow-meta">' + totalCol.cell(row) + " " + totalLabel +
      ' <span class="rsecondary">(' + secondary + ")</span></div></div>";
  }).join("");
  return '<div class="cards rlist">' + items + "</div>";
}

/* ---------- "you might also like" related-entity footer ----------
   Always-visible cards (opts.alwaysVisible), reusing the exact same .card
   component the mobile table fallback uses, so names wrap instead of
   truncating here too. Each page supplies its own cols/rows — the ranking
   rule (same-city-first for arenas, games-hosted for cities, All-Star-first
   for players) lives on the page, this just renders the result. */
function renderRelated(mount, heading, cols, rows) {
  if (!rows || !rows.length) { mount.innerHTML = ""; return; }
  mount.innerHTML = "<div class='section-title'>" + esc(heading) + "</div>" +
    cardsHtml(cols, rows, { alwaysVisible: true });
  wireRowNav(mount);
}

/* whole-row navigation (ignore clicks that landed on a real link). Covers the
   desktop table row and both mobile card formats (.card block, .rrow compact
   ranked row). */
function wireRowNav(root) {
  root.addEventListener("click", e => {
    if (e.target.closest("a")) return;
    const el = e.target.closest("tr[data-href], .card[data-href], .rrow[data-href]");
    if (el) window.location.href = el.dataset.href;
  });
}

/* ---------- autocomplete search over a name list ---------- */
function autocomplete(input, box, items, opts) {
  // items: [{name, ...}], opts.href(item)->url, opts.sub(item)->string
  let matches = [], sel = -1;
  const href = opts.href, sub = opts.sub || (() => "");
  function close() { box.classList.remove("open"); sel = -1; }
  function render(q) {
    const s = q.toLowerCase();
    const starts = [], contains = [];
    for (const it of items) {
      const n = it.name.toLowerCase();
      if (n.startsWith(s)) starts.push(it);
      else if (n.includes(s)) contains.push(it);
      if (starts.length >= 30) break;
    }
    matches = starts.concat(contains).slice(0, 25);
    if (!matches.length) { close(); return; }
    box.innerHTML = matches.map((m, i) =>
      '<a class="gs-item" href="' + href(m) + '" data-i="' + i + '">' +
      '<span class="gs-main">' + esc(m.name) + '</span>' +
      '<span class="gs-sub">' + esc(sub(m)) + "</span></a>").join("");
    box.classList.add("open"); sel = -1;
  }
  function highlight() {
    [...box.children].forEach((c, i) => c.classList.toggle("sel", i === sel));
    if (sel >= 0 && box.children[sel]) box.children[sel].scrollIntoView({ block: "nearest" });
  }
  input.addEventListener("input", () => {
    const q = input.value.trim();
    if (!q) { close(); return; }
    render(q);
    if (opts.onInput) opts.onInput(q);
  });
  input.addEventListener("keydown", e => {
    if (!box.classList.contains("open")) return;
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, matches.length - 1); highlight(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); highlight(); }
    else if (e.key === "Enter") { e.preventDefault(); const m = matches[sel >= 0 ? sel : 0]; if (m) window.location.href = href(m); }
    else if (e.key === "Escape") close();
  });
  document.addEventListener("click", e => { if (!box.contains(e.target) && e.target !== input) close(); });
}

/* ---------- query param ---------- */
const qp = k => new URLSearchParams(window.location.search).get(k);

/* ---------- draw wording (shared across every draw table) ---------- */
const DRAW_DELTA_LABEL = "Extra fans per game";
const DRAW_TOTAL_LABEL = "Total extra fans";
const DRAW_EXPLAINER = "How many more (or fewer) spectators attended these road games, " +
  "compared to a typical game at the same arena in the same season.";
const drawExplainerHTML = () => '<div class="draw-explainer">' + esc(DRAW_EXPLAINER) + "</div>";

/* Career/aggregate total extra fans = sum of per-game deltas. Exact when the
   server supplied total_delta (draw tab, merged franchises); otherwise mean x
   games, which equals the summed delta because the mean is games-weighted. */
const drawTotal = r => (r.total_delta != null ? r.total_delta : (r.mean_delta || 0) * (r.games || 0));
/* Signed per-game delta cell. */
const drawMeanCell = r => '<span class="delta ' + deltaCls(r.mean_delta) + '">' + signed(r.mean_delta) + "</span>";
/* Signed total-delta cell (rounded to whole fans). */
const drawTotalCell = r => { const t = drawTotal(r);
  return '<span class="delta ' + deltaCls(t) + '">' + signed(Math.round(t)) + "</span>"; };
/* The per-game + total column pair shared by every draw table. */
const drawDeltaCols = () => [
  { key: "mean_delta", label: DRAW_DELTA_LABEL, defaultDir: "desc", get: r => r.mean_delta, cell: drawMeanCell },
  { key: "total_delta", label: DRAW_TOTAL_LABEL, defaultDir: "desc", get: drawTotal, cell: drawTotalCell },
];
/* All-time team rows carry `historical` (era names folded in) -> subnote. */
const teamNameCell = r => esc(r.team || ((r.teamCity || "") + " " + (r.teamName || "")).trim()) +
  (r.historical && r.historical.length
    ? '<span class="sub team-hist">incl. games as ' + esc(r.historical.join(", ")) + "</span>" : "");

/* ---------- season filter (detail pages) ----------
   Detail pages embed a records_by_season array alongside their all-time data.
   "All Seasons" (season === null) keeps the existing all-time render untouched;
   picking a season re-renders that page's leaderboard/table from the embedded
   array client-side (no new fetch). */
const WIN_PCT_MIN_GAMES = 15;   // must match generate_dashboard_data.py
const LEADERBOARD_SIZE = 25;
const seasonLabel = y => (Number(y) - 1) + "-" + String(Number(y)).slice(-2); // 2007 -> "2006-07"

function seasonsIn(rows) {
  return [...new Set((rows || []).map(r => r.season))].sort((a, b) => b - a);
}
/* Season-keyed draw helpers (used by the draw sections). "All Seasons"
   (season === null) maps to the "all" aggregate; a specific season maps to its
   own key. A season not in the draw data is genuinely absent -> caller shows a
   note instead of the table. */
const drawKey = season => (season == null ? "all" : String(season));
const seasonAbsentFromDraw = (seasons, season) =>
  season != null && !(seasons || []).map(Number).includes(Number(season));
/* Build the season <select> (mount is a container). onChange(seasonOrNull).
   `extraSeasons` merges in seasons that exist in other data on the page (e.g.
   draw seasons that predate the player-record sample) so they're selectable. */
function makeSeasonPicker(mount, rows, onChange, extraSeasons) {
  const yrs = [...new Set([...seasonsIn(rows), ...(extraSeasons || []).map(Number)])]
    .sort((a, b) => b - a);
  const wrap = document.createElement("div");
  wrap.className = "field season-field";
  wrap.innerHTML = '<span class="lbl">Season</span>' +
    '<select class="season-select"><option value="">All Seasons</option>' +
    yrs.map(y => '<option value="' + y + '">' + seasonLabel(y) + "</option>").join("") +
    "</select>";
  mount.appendChild(wrap);
  wrap.querySelector("select").addEventListener("change", e => {
    const v = e.target.value;
    onChange(v === "" ? null : Number(v));
  });
  return wrap;
}
const leaderEntry = r => ({
  personId: r.personId, playerName: r.playerName, games: r.games, wins: r.wins,
  losses: r.losses, win_pct: r.win_pct, total_points: r.total_points,
  ppg: r.ppg, career_high: r.career_high,
});
/* Client-side equivalent of generate_dashboard_data.leaderboards_for(). */
function computeLeaderboards(recs) {
  const cmpName = (a, b) => String(a.playerName).localeCompare(String(b.playerName));
  const byPts = [...recs].sort((a, b) => (b.total_points - a.total_points) || cmpName(a, b));
  const byWins = [...recs].sort((a, b) => (b.wins - a.wins) || cmpName(a, b));
  const wpPool = recs.filter(r => r.games >= WIN_PCT_MIN_GAMES)
    .sort((a, b) => (b.win_pct - a.win_pct) || (b.games - a.games) || cmpName(a, b));
  const byHi = [...recs].sort((a, b) => (b.career_high - a.career_high) || cmpName(a, b));
  const top = arr => arr.slice(0, LEADERBOARD_SIZE).map(leaderEntry);
  return { total_points: top(byPts), wins: top(byWins), win_pct: top(wpPool), career_high: top(byHi) };
}

/* ---------- All-Star flags + toggles ----------
   Two independent defaults:
     allstarMode      career leaderboards / record tables / player directory —
                      default OFF (show everyone); All-Star is an opt-in filter.
     drawAllstarMode  per-arena / per-city draw tables — default ON (All-Stars
                      only); toggleable to show everyone. Opposite default,
                      scoped to the draw tables only. */
let ALLSTAR = null;          // Set of personId strings
let ALLSTAR_META = {};       // personId -> {times_selected, first_year, last_year}
let allstarMode = false;     // records/leaderboards/directory: show all by default
let drawAllstarMode = true;  // arena/city draw tables: All-Stars only by default

async function loadAllstar() {
  if (ALLSTAR) return ALLSTAR;
  try {
    const d = await getJSON(DATA + "/allstar.json");
    ALLSTAR = new Set((d.personIds || []).map(String));
    ALLSTAR_META = d.players || {};
  } catch (e) { ALLSTAR = new Set(); }
  return ALLSTAR;
}
const isAllstar = pid => !!(ALLSTAR && ALLSTAR.has(String(pid)));
/* keep only All-Stars when `mode` is on (defaults to allstarMode). */
function filterAllstar(rows, getPid, mode) {
  const on = (mode === undefined) ? allstarMode : mode;
  if (!on) return rows;
  return rows.filter(r => isAllstar(getPid(r)));
}
/* Render an All-Stars / all-players segmented toggle into `mount`. opts:
     label   heading text (default "Leaderboards")
     get/set read/write the backing mode flag (default allstarMode)
   onChange() fires after the flag flips so the caller can re-render. */
function makeAllstarToggle(mount, onChange, opts) {
  opts = opts || {};
  const label = opts.label || "Leaderboards";
  const get = opts.get || (() => allstarMode);
  const set = opts.set || (v => { allstarMode = v; });
  const wrap = document.createElement("div");
  wrap.className = "field allstar-field";
  const on = get();
  wrap.innerHTML =
    '<span class="lbl">' + esc(label) + "</span>" +
    '<div class="toggle allstar-toggle">' +
    '<button data-as="1" class="' + (on ? "active" : "") + '">★ All-Stars only</button>' +
    '<button data-as="0" class="' + (on ? "" : "active") + '">Show all players</button>' +
    "</div>";
  mount.appendChild(wrap);
  wrap.addEventListener("click", e => {
    const b = e.target.closest("button[data-as]");
    if (!b) return;
    set(b.dataset.as === "1");
    wrap.querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
    onChange();
  });
  return wrap;
}

/* ---------- persistent site search (players, teams, arenas, cities) ----------
   Injected at the top and bottom of every page from search.json. */
const searchHref = it =>
  it.type === "player" ? playerHref(it.id) :
  it.type === "arena" ? arenaHref(it.slug) :
  it.type === "city" ? cityHref(it.slug) :
  "index.html?tab=draw";               // teams live on the Attendance Draw tab

function siteSearchBar(pos, items) {
  const bar = document.createElement("div");
  bar.className = "site-search " + pos;
  bar.innerHTML =
    '<div class="ss-inner">' +
    '<input type="text" autocomplete="off" placeholder="Search players, teams, arenas, cities…">' +
    '<div class="gsearch-results"></div></div>';
  const input = bar.querySelector("input");
  const box = bar.querySelector(".gsearch-results");
  let matches = [], sel = -1;
  const close = () => { box.classList.remove("open"); sel = -1; };
  function render(q) {
    const s = q.toLowerCase();
    const starts = [], contains = [];
    for (const it of items) {
      const n = it.name.toLowerCase();
      if (n.startsWith(s)) starts.push(it);
      else if (n.includes(s)) contains.push(it);
      if (starts.length >= 40) break;
    }
    matches = starts.concat(contains).slice(0, 25);
    if (!matches.length) { close(); return; }
    box.innerHTML = matches.map((m, i) =>
      '<a class="gs-item" href="' + searchHref(m) + '" data-i="' + i + '">' +
      '<span class="gs-main">' + esc(m.name) + "</span>" +
      (m.sub ? '<span class="gs-sub">' + esc(m.sub) + "</span>" : "") +
      '<span class="gs-type">' + esc(m.type) + "</span></a>").join("");
    box.classList.add("open"); sel = -1;
  }
  const highlight = () => [...box.children].forEach((c, i) => c.classList.toggle("sel", i === sel));
  input.addEventListener("input", () => {
    const q = input.value.trim();
    if (!q) { close(); return; }
    render(q);
  });
  input.addEventListener("keydown", e => {
    if (!box.classList.contains("open")) return;
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(sel + 1, matches.length - 1); highlight(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(sel - 1, 0); highlight(); }
    else if (e.key === "Enter") { e.preventDefault(); const m = matches[sel >= 0 ? sel : 0]; if (m) window.location.href = searchHref(m); }
    else if (e.key === "Escape") close();
  });
  document.addEventListener("click", e => { if (!bar.contains(e.target)) close(); });
  return bar;
}

async function initSiteSearch() {
  let items;
  try { items = await getJSON(DATA + "/search.json"); }
  catch (e) { return; }               // no search index → skip silently
  document.body.classList.add("has-site-search");
  document.body.insertBefore(siteSearchBar("top", items), document.body.firstChild);
  document.body.appendChild(siteSearchBar("bottom", items));
}
document.addEventListener("DOMContentLoaded", initSiteSearch);
