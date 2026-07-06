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
   Every column is sortable unless sortable:false. */
function sortableTable(mount, cols, rows, initial) {
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
    const head = cols.map(c => {
      const left = c.align === "left" ? " left" : "";
      if (c.sortable === false) return '<th class="' + left.trim() + '">' + esc(c.label) + "</th>";
      const sb = sort.key === c.key ? " sorted-by" : "";
      const arrow = sort.key === c.key ? '<span class="arr">' + (sort.dir === "desc" ? "▼" : "▲") + "</span>" : "";
      return '<th class="sortable' + left + sb + '" data-k="' + esc(c.key) + '">' + esc(c.label) + arrow + "</th>";
    }).join("");
    const body = arr.map(row => {
      const href = row._href ? ' data-href="' + esc(row._href) + '"' : "";
      const tds = cols.map(c => {
        const cls = [];
        if (c.align === "left") cls.push("name");
        if (c.tdcls) { const x = c.tdcls(row); if (x) cls.push(x); }
        if (sort.key === c.key) cls.push("sorted-by");
        return '<td' + (cls.length ? ' class="' + cls.join(" ") + '"' : "") + ">" + c.cell(row) + "</td>";
      }).join("");
      return "<tr" + href + ">" + tds + "</tr>";
    }).join("");
    mount.innerHTML =
      '<div class="table-wrap"><table class="lb"><thead><tr>' + head +
      "</tr></thead><tbody>" + (body || '<tr><td class="dash">No data</td></tr>') + "</tbody></table></div>";
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

/* whole-row navigation (ignore clicks that landed on a real link) */
function wireRowNav(root) {
  root.addEventListener("click", e => {
    if (e.target.closest("a")) return;
    const tr = e.target.closest("tr[data-href]");
    if (tr) window.location.href = tr.dataset.href;
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

/* ---------- All-Star flags + leaderboard default toggle ----------
   Every player leaderboard defaults to All-Star players only; a "show all
   players" toggle expands to the full field. allstarMode is shared across the
   page so a single toggle re-renders whatever is on screen. */
let ALLSTAR = null;          // Set of personId strings
let ALLSTAR_META = {};       // personId -> {times_selected, first_year, last_year}
let allstarMode = true;      // default: All-Stars only

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
/* keep only All-Stars when allstarMode is on; getPid(row)->personId */
function filterAllstar(rows, getPid) {
  if (!allstarMode) return rows;
  return rows.filter(r => isAllstar(getPid(r)));
}
/* Render an All-Stars / all-players segmented toggle into `mount`. onChange()
   fires after allstarMode flips so the caller can re-render its tables. */
function makeAllstarToggle(mount, onChange) {
  const wrap = document.createElement("div");
  wrap.className = "controls allstar-controls";
  wrap.innerHTML =
    '<div class="field"><span class="lbl">Leaderboards</span>' +
    '<div class="toggle allstar-toggle">' +
    '<button data-as="1" class="' + (allstarMode ? "active" : "") + '">★ All-Stars only</button>' +
    '<button data-as="0" class="' + (allstarMode ? "" : "active") + '">Show all players</button>' +
    "</div></div>";
  mount.appendChild(wrap);
  wrap.addEventListener("click", e => {
    const b = e.target.closest("button[data-as]");
    if (!b) return;
    allstarMode = b.dataset.as === "1";
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
