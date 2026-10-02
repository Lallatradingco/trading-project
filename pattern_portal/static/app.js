"use strict";

/* Pattern Scanner front end. Runs against the Flask API, or, when
   window.PORTAL_STATIC is set to a folder ("data/"), against exported JSON
   files with the same filtering done in the browser. */

const FACETS = {
  family: [["Reversal", "Reversal"], ["Continuation", "Continuation"], ["Range", "Range"], ["Curve & Cup", "Curve & Cup"]],
  direction: [["bull", "Bullish"], ["bear", "Bearish"], ["neutral", "Unresolved"]],
  tf: [["1D", "Daily"], ["1W", "Weekly"], ["1M", "Monthly"]],
  status: [["Forming", "Forming"], ["Confirmed", "Confirmed"], ["Failed", "Failed"], ["Marginal", "Marginal"]],
};
const SINGLES = {
  quality: [["any", "Any"], ["fair", "Fair+"], ["strong", "Strong+"], ["textbook", "Textbook"]],
  within: [["10", "10"], ["20", "20"], ["30", "30"], ["60", "60"], ["120", "120"], ["any", "Any"]],
};
const QUALITY_MIN = { any: 0, fair: 50, strong: 65, textbook: 80 };
const STATUS_BONUS = { Confirmed: 10, Forming: 6, Marginal: 3, Failed: -12 };
const TF_WORD = { "1D": "daily", "1W": "weekly", "1M": "monthly" };
const PER_PAGE = 48;
const DEFAULTS = () => ({
  universe: "all", q: "", family: [], direction: [], tf: [], status: [],
  quality: "any", within: "30", volume: false, book: false, sort: "composite", page: 1,
});

let state = DEFAULTS();
let view = "patterns";
let selected = null;
let lastScanAt = null;
let jobWasRunning = false;
let metaCache = null;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

// ---------------------------------------------------------------- formatting
const nf = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const nf1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });
const int = v => (v ?? 0).toLocaleString("en-IN");
const rs = v => (v == null ? "—" : "₹" + (Math.abs(v) >= 1000 ? nf1.format(v) : nf.format(v)));
const pct = v => (v == null ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(1) + "%");
const dirSym = { bull: "▲", bear: "▼", neutral: "◆" };
const fmtDate = d => (d ? new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "—");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function candlesAgo(n, tf) {
  const w = TF_WORD[tf] || "";
  if (n === 0) return `latest ${w} candle`;
  return `${n} ${w} candle${n === 1 ? "" : "s"} ago`;
}
function whenText(p) {
  const ago = candlesAgo(p.bars_ago, p.tf);
  if (p.breakout_date) return (p.status === "Failed" ? "Broke out, then failed, " : "Broke out ") + ago;
  if (p.status === "Marginal") return "Edged past the level, " + ago;
  if (p.bars_ago === 0) return `Still taking shape (${TF_WORD[p.tf]})`;
  return "Shape completed " + ago;
}
function hue(sym) { let h = 0; for (const c of sym) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function avatar(el, sym) {
  el.textContent = sym.slice(0, 2);
  el.style.background = `hsl(${hue(sym)} 32% 64%)`;
}
function convClass(p) { return p == null ? "" : p >= 60 ? "up" : p <= 40 ? "down" : "mid"; }
function convText(p, label) {
  if (p == null) return "no reading";
  const side = p >= 50 ? `${p}% bullish` : `${100 - p}% bearish`;
  return label ? `${side}, ${label.toLowerCase()}` : side;
}
function relText(r) {
  if (!r || !r.cases) return "Not enough past cases on NSE to measure this pattern yet.";
  return `On NSE this pattern reached its target before its stop in ${Math.round(r.rate * 100)}% of ${int(r.cases)} past cases` +
    (r.exp_r != null ? `, an average of ${r.exp_r > 0 ? "+" : ""}${r.exp_r}R per trade.` : ".");
}

// ---------------------------------------------------------------- data sources
const ApiSource = {
  live: true,
  meta: () => fetch("api/meta").then(r => r.json()),
  patterns: f => fetch("api/patterns?" + queryString(f)).then(r => r.json()),
  stock: sym => fetch("api/stock/" + encodeURIComponent(sym)).then(r => (r.ok ? r.json() : null)),
  intraday: () => fetch("api/intraday").then(r => r.json()),
  stocks: () => fetch("api/stocks").then(r => r.json()),
  lists: () => fetch("api/lists").then(r => r.json()),
  scan: () => fetch("api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sync: true }) }),
};

const StaticSource = (() => {
  const base = window.PORTAL_STATIC;
  const get = path => fetch(base + path).then(r => { if (!r.ok) throw new Error(path + " " + r.status); return r.json(); });
  let ready = null, M, S = {}, LISTS = {}, RAWLISTS = {}, REL = {}, PATS = [], INTRA = [];
  const hist = {};
  function load() {
    if (ready) return ready;
    ready = (async () => {
      M = await get("meta.json");
      const parts = await Promise.all([
        get("stocks.json"), get("lists.json"), get("reliability.json"), get("intraday.json"),
        ...Array.from({ length: M.pattern_files }, (_, i) => get(`patterns_${i}.json`)),
      ]);
      for (const s of parts[0]) S[s.symbol] = s;
      RAWLISTS = parts[1];
      LISTS = Object.fromEntries(Object.entries(parts[1]).map(([k, v]) => [k, new Set(v)]));
      REL = parts[2]; INTRA = parts[3];
      PATS = parts.slice(4).flat();
      for (const p of PATS) {
        const st = S[p.symbol] || {};
        p.name = st.name || "";
        p.conviction = st.conviction?.bull_pct ?? null;
        p.conviction_label = st.conviction?.label ?? null;
        p.conviction_tf = Object.fromEntries(Object.entries(st.conviction_tf || {}).map(([k, v]) => [k, v.bull_pct]));
        const unbroken = p.status === "Forming" || p.status === "Marginal";
        p.rank = p.score + 30 * Math.exp(-p.bars_ago / 10) + (STATUS_BONUS[p.status] || 0)
          - (unbroken ? Math.min(25, Math.abs(p.vs_breakout || 0)) : 0)
          - ((st.turnover_cr || 0) < 1 ? 10 : 0);
      }
    })();
    return ready;
  }
  function masks(f) {
    const m = {};
    if (f.universe && f.universe !== "all") { const set = LISTS[f.universe] || new Set(); m.universe = p => set.has(p.symbol); }
    if (f.q) { const q = f.q.trim().toUpperCase(); m.q = p => p.symbol.includes(q) || (p.name || "").toUpperCase().includes(q); }
    if (f.within && f.within !== "any") { const w = +f.within; m.within = p => p.bars_ago <= w; }
    if (f.volume) m.volume = p => p.volume_confirmed === true;
    if (f.book) m.book = p => p.score >= 80;
    for (const k of ["family", "direction", "tf", "status"]) if (f[k]?.length) { const set = new Set(f[k]); m[k] = p => set.has(p[k]); }
    const qmin = QUALITY_MIN[f.quality] || 0;
    if (qmin) m.quality = p => p.score >= qmin;
    return m;
  }
  const apply = (m, skip) => { const fs = Object.entries(m).filter(([k]) => k !== skip).map(([, fn]) => fn); return PATS.filter(p => fs.every(fn => fn(p))); };
  return {
    live: false,
    async meta() {
      await load();
      return { scan: M, universes: M.universes, market: null, job: { running: false }, reliability_all: REL.__all__ };
    },
    async patterns(f) {
      await load();
      const m = masks(f);
      const v = apply(m);
      const facets = {};
      for (const fac of ["family", "direction", "tf", "status", "quality"]) {
        const sub = apply(m, fac);
        if (fac === "quality") facets.quality = { any: sub.length, fair: sub.filter(p => p.score >= 50).length, strong: sub.filter(p => p.score >= 65).length, textbook: sub.filter(p => p.score >= 80).length };
        else { const c = {}; for (const p of sub) c[p[fac]] = (c[p[fac]] || 0) + 1; facets[fac] = c; }
      }
      if (f.sort === "recent") v.sort((a, b) => a.bars_ago - b.bars_ago || b.score - a.score);
      else if (f.sort === "cleanest") v.sort((a, b) => b.score - a.score || a.bars_ago - b.bars_ago);
      else if (f.sort === "bullish" || f.sort === "bearish") {
        const tf = f.tf?.length === 1 ? f.tf[0] : null;
        const cv = p => (tf ? p.conviction_tf?.[tf] : p.conviction) ?? 50;
        const dir = f.sort === "bullish" ? -1 : 1;
        v.sort((a, b) => dir * (cv(a) - cv(b)) || b.rank - a.rank);
      }
      else v.sort((a, b) => b.rank - a.rank);
      const page = f.page || 1;
      return {
        total: M.patterns, in_view: v.length,
        confirmed: v.filter(p => p.status === "Confirmed").length,
        bull: v.filter(p => p.direction === "bull").length, bear: v.filter(p => p.direction === "bear").length,
        page, pages: Math.max(1, Math.ceil(v.length / PER_PAGE)), facets,
        items: v.slice((page - 1) * PER_PAGE, page * PER_PAGE),
      };
    },
    async stock(sym) {
      await load();
      const st = S[sym];
      if (!st) return null;
      const key = /[A-Z]/.test(sym[0]) ? sym[0] : "_";
      if (!hist[key]) hist[key] = await get(`history/${key}.json`);
      const light = hist[key][sym] || [];
      const full = new Map(PATS.filter(p => p.symbol === sym).map(p => [p.id, p]));
      const pats = light.map(p => ({ ...p, symbol: sym, ...(full.get(p.id) || {}) }));
      const rel = { __all__: REL.__all__ };
      for (const p of pats) { const k = `${p.pattern}|${p.tf}`; if (REL[k]) rel[k] = REL[k]; }
      const by = tf => pats.filter(p => p.tf === tf).length;
      return {
        info: st,
        conviction: st.conviction, conviction_tf: st.conviction_tf, reliability: rel,
        summary: {
          patterns: pats.length,
          timeframes: ["1D", "1W", "1M"].filter(tf => by(tf)),
          confirmed: pats.filter(p => p.status === "Confirmed").length,
          held: pats.filter(p => p.status === "Confirmed" && p.outcome !== "Stopped").length,
          forming: pats.filter(p => p.status === "Forming").length,
          target_hit: pats.filter(p => p.outcome === "Target hit").length,
          stopped: pats.filter(p => p.outcome === "Stopped").length,
        },
        patterns: pats,
      };
    },
    intraday: async () => { await load(); return INTRA; },
    stocks: async () => { await load(); return Object.values(S); },
    lists: async () => { await load(); return RAWLISTS; },
  };
})();

const SRC = window.PORTAL_STATIC ? StaticSource : ApiSource;

let listCache = null;
async function universeMembers(key) {
  if (!key || key === "all") return null;
  if (!listCache) listCache = await SRC.lists();
  return new Set(listCache[key] || []);
}

// ---------------------------------------------------------------- charts
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

function drawChart(svg, p, big) {
  svg.innerHTML = "";
  if (!p.chart) return;
  const vb = svg.viewBox.baseVal;
  const W = vb.width, H = vb.height;
  const pad = big ? { l: 6, r: 92, t: 14, b: 22 } : { l: 4, r: 4, t: 8, b: 8 };
  const ch = p.chart, N = ch.c.length;
  if (N < 2) return;
  const X = ch.x || ch.c.map((_, k) => ch.i0 + k * ch.step);   // bar index of each close
  const iEnd = X[N - 1];
  const xOf = i => pad.l + Math.max(0, Math.min(1, (i - ch.i0) / Math.max(1, iEnd - ch.i0))) * (W - pad.l - pad.r);

  const entry = p.breakout ?? p.range_up;
  const vals = ch.c.slice();
  for (const s of p.segments) if (s.x2 >= ch.i0) vals.push(s.y1, s.y2);
  [entry, p.range_dn, p.stop].forEach(v => v != null && vals.push(v));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const R = hi - lo || hi * 0.05;
  let tgt = p.target, tgtOff = "";
  if (tgt != null) {
    if (tgt > hi + 0.7 * R) { tgt = hi + 0.7 * R; tgtOff = "up"; }
    if (tgt < lo - 0.7 * R) { tgt = lo - 0.7 * R; tgtOff = "down"; }
    lo = Math.min(lo, tgt); hi = Math.max(hi, tgt);
  }
  const m = (hi - lo) * 0.06; lo -= m; hi += m;
  const yOf = v => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b);

  if (big) {
    for (let k = 0; k <= 3; k++) {
      const y = pad.t + k * (H - pad.t - pad.b) / 3;
      el("line", { x1: pad.l, x2: W - pad.r, y1: y, y2: y, class: "c-grid" }, svg);
    }
  }
  let d = "";
  ch.c.forEach((v, k) => { d += (k ? "L" : "M") + xOf(X[k]).toFixed(1) + " " + yOf(v).toFixed(1); });
  el("path", { d: d + `L${xOf(iEnd)} ${H - pad.b}L${xOf(ch.i0)} ${H - pad.b}Z`, class: "c-area" }, svg);
  el("path", { d, class: "c-price" }, svg);

  const shape = p.segments.filter(s => s.kind === "shape");
  if (shape.length) {
    let sd = "";
    shape.forEach((s, k) => { if (!k) sd += `M${xOf(s.x1)} ${yOf(s.y1)}`; sd += `L${xOf(s.x2)} ${yOf(s.y2)}`; });
    el("path", { d: sd, class: "c-shape" }, svg);
  }
  const curve = p.segments.filter(s => s.kind === "curve");
  if (curve.length) {
    let cd = `M${xOf(curve[0].x1)} ${yOf(curve[0].y1)}`;
    curve.forEach(s => { cd += `L${xOf(s.x2)} ${yOf(s.y2)}`; });
    el("path", { d: cd, class: "c-curve" }, svg);
  }
  for (const s of p.segments) {
    if (s.kind === "pole") el("line", { x1: xOf(s.x1), y1: yOf(s.y1), x2: xOf(s.x2), y2: yOf(s.y2), class: "c-pole" }, svg);
    if (["upper", "lower", "neck", "rim", "bound"].includes(s.kind)) {
      const cls = p.direction === "neutral" || s.kind === "bound" ? "c-bound" : "c-break";
      el("line", { x1: xOf(s.x1), y1: yOf(s.y1), x2: xOf(s.x2), y2: yOf(s.y2), class: cls }, svg);
    }
  }
  const xe = xOf(p.breakout_idx ?? p.end);
  const xr = W - pad.r;
  if (p.stop != null) el("line", { x1: xe, x2: xr, y1: yOf(p.stop), y2: yOf(p.stop), class: "c-stop" }, svg);
  if (tgt != null) el("line", { x1: xe, x2: xr, y1: yOf(tgt), y2: yOf(tgt), class: "c-target" }, svg);
  if (p.breakout_idx != null && entry != null) el("circle", { cx: xOf(p.breakout_idx), cy: yOf(entry), r: big ? 3.5 : 2.6, class: "c-bo" }, svg);

  if (!big) return;
  const mid = (lo + hi) / 2;
  for (const [i, v, lab] of p.points) {
    if (i < ch.i0) continue;
    el("circle", { cx: xOf(i), cy: yOf(v), r: 2.6, class: "c-pt" }, svg);
    if (lab) {
      const t = el("text", { x: xOf(i), y: yOf(v) + (v >= mid ? -7 : 14), "text-anchor": "middle", class: "c-lab" }, svg);
      t.textContent = lab;
    }
  }
  const tags = [];
  if (entry != null) tags.push(["b", yOf(entry), (p.breakout_idx != null ? "Breakout " : "Level ") + rs(entry)]);
  if (p.range_dn != null) tags.push(["b", yOf(p.range_dn), "Level " + rs(p.range_dn)]);
  if (p.target != null) tags.push(["t", yOf(tgt), "Target " + rs(p.target) + (tgtOff === "up" ? " ↑" : tgtOff === "down" ? " ↓" : "")]);
  if (p.stop != null) tags.push(["s", yOf(p.stop), "Stop " + rs(p.stop)]);
  tags.sort((a, b) => a[1] - b[1]);
  for (let k = 1; k < tags.length; k++) if (tags[k][1] - tags[k - 1][1] < 12) tags[k][1] = tags[k - 1][1] + 12;
  for (const [c, y, txt] of tags) {
    const t = el("text", { x: W - pad.r + 6, y: Math.max(pad.t, Math.min(H - pad.b, y)) + 3.5, class: "c-tag " + c }, svg);
    t.textContent = txt;
  }
  const ax = (x, txt, anchor) => { const t = el("text", { x, y: H - 6, "text-anchor": anchor, class: "c-axis" }, svg); t.textContent = txt; };
  ax(pad.l, fmtDate(ch.d0), "start");
  ax(W - pad.r, fmtDate(ch.d1), "end");
}

// ---------------------------------------------------------------- filters
function buildFilters() {
  for (const box of $$("[data-facet]")) {
    const f = box.dataset.facet;
    box.innerHTML = "";
    for (const [v, label] of FACETS[f]) {
      const b = document.createElement("button");
      b.className = "chip"; b.dataset.v = v;
      b.innerHTML = `${esc(label)}<b></b>`;
      b.onclick = () => { const a = state[f], i = a.indexOf(v); i >= 0 ? a.splice(i, 1) : a.push(v); refresh(); };
      box.appendChild(b);
    }
  }
  for (const box of $$("[data-single]")) {
    const f = box.dataset.single;
    box.innerHTML = "";
    for (const [v, label] of SINGLES[f]) {
      const b = document.createElement("button");
      b.textContent = label; b.dataset.v = v;
      b.onclick = () => { state[f] = v; refresh(); };
      box.appendChild(b);
    }
  }
}
function syncFilterUI(facets) {
  for (const box of $$("[data-facet]")) {
    const f = box.dataset.facet;
    for (const b of $$(".chip", box)) {
      b.setAttribute("aria-pressed", state[f].includes(b.dataset.v));
      $("b", b).textContent = int(facets?.[f]?.[b.dataset.v] ?? 0);
    }
  }
  for (const box of $$("[data-single]")) {
    const f = box.dataset.single;
    for (const b of $$("button", box)) b.setAttribute("aria-pressed", state[f] === b.dataset.v);
  }
  $("#volume").checked = state.volume;
  $("#book").checked = state.book;
  for (const t of $$(".tabs button")) t.setAttribute("aria-selected", t.dataset.sort === state.sort);
}
function queryString(f) {
  const qs = new URLSearchParams();
  for (const k of ["universe", "q", "within", "quality", "sort", "page"]) if (f[k] !== "" && f[k] != null) qs.set(k, f[k]);
  for (const k of ["family", "direction", "tf", "status"]) f[k].forEach(v => qs.append(k, v));
  if (f.volume) qs.set("volume", "1");
  if (f.book) qs.set("from_book", "1");
  return qs.toString();
}

// ---------------------------------------------------------------- patterns view
let reqId = 0;
async function refresh(append = false) {
  if (!append) state.page = 1;
  const id = ++reqId;
  const res = await SRC.patterns({ ...state, family: [...state.family], direction: [...state.direction], tf: [...state.tf], status: [...state.status] });
  if (id !== reqId) return;
  renderStats(res);
  syncFilterUI(res.facets);
  renderGrid(res, append);
}

function renderStats(res) {
  const s = metaCache?.scan || {};
  const all = metaCache?.reliability_all;
  const cells = [
    ["Scanned", int(s.scanned), `of ${int(s.eligible)} eligible NSE stocks`],
    ["Patterns", int(res.total), "all ages, all timeframes"],
    ["In view", int(res.in_view), "with the filters below"],
    ["Confirmed", int(res.confirmed), "held the break, in view"],
    ["Bull / bear", `<span class="up">${int(res.bull)}</span> / <span class="down">${int(res.bear)}</span>`, "resolved direction only"],
    ["Past hit rate", all ? Math.round(all.rate * 100) + "%" : "—", all ? `target before stop, ${int(all.cases)} trades` : "not measured yet"],
  ];
  $("#stats").innerHTML = cells.map(([k, v, sub]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`).join("");
  $("#count").innerHTML = `<b>${int(res.in_view)}</b> patterns of ${int(res.total)}. Found by shape rules on closing prices, not advice.`;
}

function renderGrid(res, append) {
  const grid = $("#grid");
  if (!append) grid.innerHTML = "";
  if (!res.items.length && !append) {
    grid.innerHTML = `<div class="empty">${res.total ? "No patterns match these filters." : "No scan results yet."}<br>` +
      (res.total ? `<button class="btn" id="clear">Clear filters</button>` : SRC.live ? `<button class="btn" id="scan2">Scan now</button>` : "") + `</div>`;
    $("#clear")?.addEventListener("click", resetFilters);
    $("#scan2")?.addEventListener("click", startScan);
  }
  const tpl = $("#card-tpl");
  for (const p of res.items) {
    const c = tpl.content.firstElementChild.cloneNode(true);
    c.dataset.id = p.id;
    avatar($(".avatar", c), p.symbol);
    $(".sym", c).textContent = p.symbol;
    $(".name", c).textContent = p.name || "";
    const pill = $(".pill", c); pill.textContent = p.status; pill.classList.add(p.status);
    const dir = $(".dir", c); dir.classList.add(p.direction); dir.textContent = dirSym[p.direction];
    $(".ptxt", c).textContent = p.pattern + (p.direction === "neutral" ? " (unresolved)" : "");
    $(".tf", c).textContent = p.tf;
    const svg = $(".mini", c);
    svg.setAttribute("aria-label", `${p.pattern} on ${p.symbol}`);
    drawChart(svg, p, false);
    const dts = $$(".levels dt", c);
    if (p.direction === "neutral") {
      dts[0].textContent = "Upper"; dts[1].textContent = "Lower"; dts[2].textContent = "Range";
      $(".b", c).textContent = rs(p.range_up);
      $(".t", c).textContent = rs(p.range_dn); $(".t", c).className = "t";
      $(".s", c).textContent = p.range_up && p.range_dn ? (100 * (p.range_up / p.range_dn - 1)).toFixed(1) + "%" : "—";
      $(".s", c).className = "s";
    } else {
      $(".b", c).textContent = rs(p.breakout);
      $(".t", c).textContent = rs(p.target);
      $(".s", c).textContent = rs(p.stop);
    }
    $(".when", c).textContent = whenText(p);
    $(".rr", c).textContent = p.rr ? `R:R 1 : ${p.rr}` : "";
    $(".ql", c).textContent = p.quality + (p.volume_confirmed ? ", volume ✓" : "");
    $(".vs", c).textContent = `Close ${rs(p.last_close)}` + (p.vs_breakout != null && p.direction !== "neutral" ? ` ${pct(p.vs_breakout)}` : "");
    const tfSel = state.tf.length === 1 ? state.tf[0] : null;
    const cv = tfSel ? (p.conviction_tf?.[tfSel] ?? null) : p.conviction;
    $(".cvbar i", c).style.width = (cv ?? 50) + "%";
    if (convClass(cv)) $(".cvrow", c).classList.add(convClass(cv));
    $(".cvtxt", c).textContent = `Stock conviction${tfSel ? " (" + TF_WORD[tfSel] + ")" : ""} ` + convText(cv);
    if (selected?.id === p.id) c.classList.add("sel");
    c.onclick = () => select(p.symbol, p.id);
    c.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(p.symbol, p.id); } };
    grid.appendChild(c);
  }
  const more = $("#more");
  more.hidden = res.page >= res.pages;
  more.textContent = `Show more patterns (${int(res.in_view - res.page * PER_PAGE)} left)`;
  if (!append && !selected && res.items.length && window.innerWidth > 1080) select(res.items[0].symbol, res.items[0].id, false);
}

// ---------------------------------------------------------------- intraday view
let intraCache = null;
async function renderIntraday() {
  if (!intraCache) intraCache = await SRC.intraday();
  const set = await universeMembers(state.universe);
  const q = state.q.trim().toUpperCase();
  const rows = intraCache.filter(p => (!set || set.has(p.symbol)) && (!q || p.symbol.includes(q)));
  for (const side of ["bull", "bear"]) {
    const box = $("#iv-" + side);
    const list = rows.filter(p => p.side === side).slice(0, 15);
    box.innerHTML = list.length ? "" : `<p class="empty-small">No ${side === "bull" ? "bullish" : "bearish"} setups qualify in this universe.</p>`;
    list.forEach((p, i) => {
      const r = p.reliability;
      const a = document.createElement("article");
      a.className = "pick " + side; a.tabIndex = 0;
      a.innerHTML = `
        <header><span class="rank">${i + 1}</span><span class="avatar"></span>
          <div class="who"><strong class="sym">${esc(p.symbol)}</strong><span class="name">${esc(p.pattern)}, ${esc(p.kind.toLowerCase())}</span></div>
          <span class="score" title="Pick score out of 100">${Math.round(p.score)}</span></header>
        <dl class="levels four">
          <div><dt>Trigger</dt><dd>${rs(p.entry)}</dd></div>
          <div><dt>Target</dt><dd class="up">${rs(p.target)}</dd></div>
          <div><dt>Stop</dt><dd class="down">${rs(p.stop)}</dd></div>
          <div><dt>R:R</dt><dd>${p.rr ? "1 : " + p.rr : "—"}</dd></div>
        </dl>
        <ul class="pick-facts">
          <li>Close ${rs(p.last_close)}, ${p.dist_atr > 0 ? p.dist_atr + " ATR from the trigger" : p.dist_atr < 0 ? Math.abs(p.dist_atr) + " ATR past the trigger" : "at the breakout"}</li>
          <li>Hit rate ${r && r.cases ? Math.round(r.rate * 100) + "% of " + int(r.cases) + " past cases" : "not measured"}</li>
          <li class="${convClass(p.conviction)}">Conviction ${convText(p.conviction)}</li>
          <li>₹${nf1.format(p.turnover_cr)} cr traded a day${p.vol_ratio ? ", volume " + p.vol_ratio + "× usual" : ""}</li>
        </ul>`;
      avatar($(".avatar", a), p.symbol);
      a.onclick = () => select(p.symbol, p.id);
      a.onkeydown = e => { if (e.key === "Enter") select(p.symbol, p.id); };
      box.appendChild(a);
    });
  }
}

// ---------------------------------------------------------------- conviction view
let stocksCache = null;
const cvState = { filter: "all", trend: false, liquid: true, tf: "all", sort: "conv", dir: -1, shown: 100 };
const convFor = s => (cvState.tf === "all" ? s.conviction : s.conviction_tf?.[cvState.tf]) || null;
async function renderConviction() {
  if (!stocksCache) stocksCache = await SRC.stocks();
  const set = await universeMembers(state.universe);
  const q = state.q.trim().toUpperCase();
  let rows = stocksCache.filter(s => convFor(s)?.bull_pct != null && (!set || set.has(s.symbol)) &&
    (!q || s.symbol.includes(q) || (s.name || "").toUpperCase().includes(q)));
  if (!cvState.trend) rows = rows.filter(s => (convFor(s).pattern_bull || 0) + (convFor(s).pattern_bear || 0) > 0);
  if (cvState.liquid) rows = rows.filter(s => (s.turnover_cr || 0) >= 1);
  if (cvState.filter === "bull") rows = rows.filter(s => convFor(s).bull_pct >= 60);
  if (cvState.filter === "bear") rows = rows.filter(s => convFor(s).bull_pct <= 40);
  if (cvState.filter === "strong") rows = rows.filter(s => /^Strong/.test(convFor(s).label || ""));
  const key = cvState.sort;
  const val = s => key === "conv" || key === "label" ? convFor(s).bull_pct
    : key === "evidence" ? convFor(s).evidence
    : key === "live" ? (cvState.tf === "all" ? s.live : convFor(s).live) : s[key];
  rows.sort((a, b) => {
    const x = val(a), y = val(b);
    if (typeof x === "string" || typeof y === "string") return cvState.dir * String(x).localeCompare(String(y));
    // ties: stronger evidence first
    return cvState.dir * ((x ?? -1e15) - (y ?? -1e15)) || (convFor(b).evidence || 0) - (convFor(a).evidence || 0);
  });
  for (const b of $$("#cv-order button")) b.setAttribute("aria-pressed", key === "conv" && +b.dataset.v === cvState.dir);
  for (const b of $$("#cv-tf button")) b.setAttribute("aria-pressed", b.dataset.v === cvState.tf);
  const body = $("#cv-body");
  body.innerHTML = rows.slice(0, cvState.shown).map(s => {
    const c = convFor(s), p = c.bull_pct;
    return `<tr tabindex="0" data-sym="${esc(s.symbol)}">
      <td><strong>${esc(s.symbol)}</strong>${s.name ? `<span class="sub">${esc(s.name)}</span>` : ""}</td>
      <td class="num"><span class="meter"><i style="width:${p}%"></i></span> ${p}%</td>
      <td class="${convClass(p)}">${esc(c.label)}</td>
      <td class="num">${esc(c.strength || "")}</td>
      <td class="num">${int(cvState.tf === "all" ? s.live : c.live)}</td>
      <td class="num">${rs(s.last_close)} <span class="sub ${s.chg_pct >= 0 ? "up" : "down"}">${pct(s.chg_pct)}</span></td>
      <td class="num ${s.ret20 >= 0 ? "up" : "down"}">${pct(s.ret20)}</td>
      <td class="num">₹${nf1.format(s.turnover_cr || 0)} cr</td></tr>`;
  }).join("") || `<tr><td colspan="8" class="empty-small">No stocks match. Turn on "Include stocks with no live pattern" or pick another universe.</td></tr>`;
  for (const tr of $$("tr[data-sym]", body)) {
    tr.onclick = () => select(tr.dataset.sym, null);
    tr.onkeydown = e => { if (e.key === "Enter") select(tr.dataset.sym, null); };
  }
  for (const th of $$(".cv-table th")) th.setAttribute("aria-sort", th.dataset.k === cvState.sort ? (cvState.dir < 0 ? "descending" : "ascending") : "none");
  const more = $("#cv-more");
  more.hidden = rows.length <= cvState.shown;
  more.textContent = `Show more stocks (${int(rows.length - cvState.shown)} left)`;
}

// ---------------------------------------------------------------- detail panel
const stockCache = new Map();
async function select(sym, id, open = true) {
  selected = { symbol: sym, id };
  for (const c of $$(".card")) c.classList.toggle("sel", c.dataset.id === id);
  let d = stockCache.get(sym);
  if (!d) {
    d = await SRC.stock(sym);
    if (!d) return;
    stockCache.set(sym, d);
    if (stockCache.size > 60) stockCache.delete(stockCache.keys().next().value);
  }
  renderDetail(d, id);
  if (open) $("#detail").classList.add("open");
}

function convictionBlock(c, byTf) {
  if (!c || c.bull_pct == null) return "";
  const tfRow = byTf ? `<div class="tf-conv">${["1D", "1W", "1M"].map(tf => {
    const x = byTf[tf];
    return `<div><span class="k">${TF_WORD[tf][0].toUpperCase() + TF_WORD[tf].slice(1)}</span><span class="${convClass(x?.bull_pct)}">${x?.bull_pct == null ? "—" : convText(x.bull_pct)}</span><span class="s">${x ? int(x.live) + " live" : ""}</span></div>`;
  }).join("")}</div>` : "";
  const p = c.bull_pct;
  const total = (c.bull || 0) + (c.bear || 0) || 1;
  const drivers = (c.drivers || []).map(dv => `<li><span class="dir ${dv.side}">${dirSym[dv.side]}</span>
      <span>${esc(dv.pattern)} <span class="when">${dv.tf}, ${esc(dv.status.toLowerCase())}</span></span>
      <span class="res">${Math.round(100 * dv.weight / total)}%</span></li>`).join("");
  const trend = (c.trend || []).map(t => `<li class="${t.pass ? "up" : "down"}">${t.pass ? "✓" : "✗"} ${esc(t.check)}</li>`).join("");
  const trendShare = Math.round(100 * ((c.trend_bull || 0) + (c.trend_bear || 0)) / total);
  return `<section class="conv">
      <div class="conv-head"><h3>Conviction</h3><span class="conv-read ${convClass(p)}">${esc(c.label)}</span></div>
      <div class="split" role="img" aria-label="${p}% bullish, ${100 - p}% bearish"><i class="b" style="width:${p}%"></i><i class="s" style="width:${100 - p}%"></i></div>
      <div class="split-lab"><span class="up">Bullish ${p}%</span><span>${esc(c.strength)} evidence</span><span class="down">Bearish ${100 - p}%</span></div>
      ${tfRow}
      ${drivers ? `<h4>Patterns behind it, share of the weight</h4><ol class="drivers">${drivers}</ol>` : `<p class="hint">No live pattern on this stock, so the reading comes from trend alone.</p>`}
      <h4>Trend checks, ${trendShare}% of the weight</h4><ul class="trend">${trend}</ul>
    </section>`;
}

function renderDetail(d, id, box = $("#detail"), inModal = false) {
  const pats = d.patterns;
  const p = pats.find(x => x.id === id) || pats.find(x => x.chart) || pats[0];
  const i = d.info, s = d.summary;
  const chg = i.chg_pct ?? (i.prev_close ? (i.last_close / i.prev_close - 1) * 100 : null);
  const rel = p ? d.reliability?.[`${p.pattern}|${p.tf}`] : null;
  box.innerHTML = `
    <div class="d-head">
      <span class="avatar"></span>
      <div><h2>${esc(i.symbol)}</h2><p>${esc(i.name || "NSE equity")}</p></div>
      <a href="https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(i.symbol.replace(/_SME$/, ""))}" target="_blank" rel="noopener">Open chart</a>
      <button class="btn ghost close-detail" aria-label="Close">✕</button>
    </div>
    <div class="d-stats">
      <div><div class="k">Last close</div><div class="v">${rs(i.last_close)}</div><div class="s ${chg >= 0 ? "up" : "down"}">${chg == null ? "" : pct(chg)} on ${fmtDate(i.last_date)}</div></div>
      <div><div class="k">Daily range (ATR)</div><div class="v">${rs(i.atr)}</div><div class="s">${i.atr_pct != null ? i.atr_pct + "% of price" : ""}</div></div>
      <div><div class="k">Patterns</div><div class="v">${s.patterns}</div><div class="s">${s.timeframes.join(", ")}, ${i.years} years</div></div>
      <div><div class="k">Confirmed</div><div class="v">${s.confirmed}</div><div class="s">${s.held} not stopped out</div></div>
      <div><div class="k">Reached target</div><div class="v">${s.target_hit}</div><div class="s">${s.stopped} hit the stop first</div></div>
      <div><div class="k">Forming now</div><div class="v">${s.forming}</div><div class="s">not yet broken out</div></div>
    </div>
    ${convictionBlock(d.conviction, d.conviction_tf)}
    ${p ? `
    <div class="d-title"><span class="dir ${p.direction}">${dirSym[p.direction]}</span><h3>${esc(p.pattern)}</h3></div>
    <div class="d-sub">
      <span class="tag ${p.direction}">${p.direction === "bull" ? "Bullish" : p.direction === "bear" ? "Bearish" : "Unresolved"}</span>
      <span class="tag">${TF_WORD[p.tf]}</span><span class="tag">${esc(p.status)}</span><span class="tag">${esc(p.family)}</span>
    </div>
    ${p.chart ? `<svg class="big" viewBox="0 0 380 260" role="img" aria-label="${esc(p.pattern)} chart"></svg>
    <ul class="legend">
      <li><i class="lg-shape"></i>Shape through the swing closes</li>
      <li><i class="lg-break"></i>Line a close must cross</li>
      <li><i class="lg-target"></i>Measured target</li>
      <li><i class="lg-stop"></i>Stop</li>
    </ul>` : `<p class="hint">Charts are kept for the last 120 candles of patterns; this older one shows its levels only.</p>`}
    <p class="reliab">${esc(relText(rel))}</p>
    <div class="facts">
      <div><div class="k">${p.breakout_date ? "Broke out" : "Shape completed"}</div><div class="v">${fmtDate(p.breakout_date || p.end_date)}</div><div class="s">${candlesAgo(p.bars_ago, p.tf)}</div></div>
      <div><div class="k">Spans</div><div class="v">${p.span} ${TF_WORD[p.tf]} candles</div><div class="s">${fmtDate(p.start_date)} to ${fmtDate(p.end_date)}</div></div>
      <div><div class="k">Shape quality</div><div class="v">${esc(p.quality)} (${p.score}/100)</div><div class="s">fit to the textbook shape</div></div>
      <div><div class="k">${p.outcome ? "Since the breakout" : "Last close vs level"}</div><div class="v">${esc(p.outcome || (p.vs_breakout != null ? pct(p.vs_breakout) : "—"))}</div><div class="s">${p.volume_confirmed ? "Breakout on 1.5× average volume" : p.volume_confirmed === false ? "Breakout volume was ordinary" : "Close " + rs(i.last_close)}</div></div>
    </div>
    <dl class="levels">
      ${p.direction === "neutral"
        ? `<div><dt>Upper</dt><dd>${rs(p.range_up)}</dd></div><div><dt>Lower</dt><dd>${rs(p.range_dn)}</dd></div><div><dt>R:R</dt><dd>—</dd></div>`
        : `<div><dt>Breakout</dt><dd>${rs(p.breakout)}</dd></div><div><dt>Target</dt><dd class="up">${rs(p.target)}</dd></div><div><dt>Stop</dt><dd class="down">${rs(p.stop)}</dd></div>`}
    </dl>
    <p class="hint">${p.rr ? `Reward to risk 1 : ${p.rr}, measured from the breakout level.` : ""}</p>` : `<p class="hint">No patterns recorded for this stock.</p>`}
    <div class="history"><h3>All ${s.patterns} patterns on ${esc(i.symbol)}, newest first</h3><ol></ol></div>`;
  avatar($(".avatar", box), i.symbol);
  if (p?.chart) drawChart($(".big", box), p, true);
  $(".close-detail", box).onclick = () => (inModal ? closeModal() : box.classList.remove("open"));
  const ol = $(".history ol", box);
  for (const q of pats.slice(0, 150)) {
    const li = document.createElement("li");
    li.tabIndex = 0;
    if (p && q.id === p.id) li.classList.add("sel");
    li.innerHTML = `<span class="dir ${q.direction}">${dirSym[q.direction]}</span>
      <span><span>${esc(q.pattern)}</span> <span class="when">${q.tf}, ${fmtDate(q.breakout_date || q.end_date)}</span></span>
      <span class="res">${esc(q.outcome || q.status)}</span>`;
    li.onclick = () => {
      if (!inModal) selected = { symbol: i.symbol, id: q.id };
      renderDetail(d, q.id, box, inModal);
    };
    li.onkeydown = e => { if (e.key === "Enter") li.onclick(); };
    ol.appendChild(li);
  }
}

// ---------------------------------------------------------------- stock search popup
let sugItems = [], sugIdx = -1;
async function suggest(text) {
  const q = text.trim().toUpperCase();
  const ul = $("#suggest");
  if (!q) { ul.hidden = true; $("#q").setAttribute("aria-expanded", "false"); return; }
  if (!stocksCache) stocksCache = await SRC.stocks();
  const starts = [], has = [];
  for (const s of stocksCache) {
    const name = (s.name || "").toUpperCase();
    if (s.symbol.startsWith(q)) starts.push(s);
    else if (s.symbol.includes(q) || name.includes(q)) has.push(s);
  }
  const byValue = (a, b) => (b.turnover_cr || 0) - (a.turnover_cr || 0);
  sugItems = [...starts.sort(byValue), ...has.sort(byValue)].slice(0, 8);
  sugIdx = sugItems.length ? 0 : -1;
  ul.innerHTML = sugItems.length ? sugItems.map((s, k) => {
    const c = s.conviction?.bull_pct;
    return `<li role="option" id="sug-${k}" data-sym="${esc(s.symbol)}" aria-selected="${k === 0}">
      <span class="avatar" style="background:hsl(${hue(s.symbol)} 32% 64%)">${esc(s.symbol.slice(0, 2))}</span>
      <span class="who"><strong>${esc(s.symbol)}</strong><span class="name">${esc(s.name || "")} ${rs(s.last_close)}</span></span>
      <span class="${convClass(c)}">${c == null ? "" : convText(c)}</span>
      <span class="n">${int(s.live)} live</span></li>`;
  }).join("") : `<li class="none">No NSE stock matches "${esc(text.trim())}" in this scan.</li>`;
  ul.hidden = false;
  $("#q").setAttribute("aria-expanded", "true");
  for (const li of $$("li[data-sym]", ul)) li.onmousedown = e => { e.preventDefault(); openStock(li.dataset.sym); };
}
function moveSuggest(step) {
  if (!sugItems.length) return;
  sugIdx = (sugIdx + step + sugItems.length) % sugItems.length;
  $$("#suggest li[data-sym]").forEach((li, k) => li.setAttribute("aria-selected", k === sugIdx));
  $("#q").setAttribute("aria-activedescendant", "sug-" + sugIdx);
}
function hideSuggest() { $("#suggest").hidden = true; $("#q").setAttribute("aria-expanded", "false"); }
let lastFocus = null;
async function openStock(sym) {
  hideSuggest();
  const d = stockCache.get(sym) || await SRC.stock(sym);
  if (!d) return;
  stockCache.set(sym, d);
  // lead with the most relevant pattern: a live one with a chart, else the newest with a chart
  const live = d.patterns.filter(p => p.chart && ["Forming", "Marginal"].includes(p.status) || (p.chart && p.status === "Confirmed" && p.outcome === "Open"));
  const lead = live[0] || d.patterns.find(p => p.chart) || d.patterns[0];
  lastFocus = document.activeElement;
  renderDetail(d, lead?.id, $("#modal-body"), true);
  $("#modal").hidden = false;
  document.body.classList.add("modal-open");
  $("#modal .close-detail")?.focus();
}
function closeModal() {
  $("#modal").hidden = true;
  document.body.classList.remove("modal-open");
  lastFocus?.focus?.();
}

// ---------------------------------------------------------------- meta + views
async function loadMeta() {
  const m = await SRC.meta();
  metaCache = m;
  const mk = $("#mkt");
  if (m.market) {
    mk.className = "mkt" + (m.market.open ? " open" : "");
    mk.innerHTML = `<span class="dot"></span><b>${m.market.open ? "Market open" : "Market closed"}</b> ${esc(m.market.time)}, ${esc(m.market.note)}`;
  } else {
    mk.className = "mkt";
    mk.innerHTML = `<span class="dot"></span><b>Snapshot</b> prices through ${fmtDate(m.scan.data_through)}`;
  }
  const ub = $("#universes");
  if (!ub.childElementCount) {
    for (const u of m.universes.filter(u => u.available)) {
      const b = document.createElement("button");
      b.className = "chip"; b.dataset.v = u.key; b.textContent = u.label;
      b.onclick = () => {
        state.universe = u.key; syncUniverse(); refresh();
        if (view === "intraday") renderIntraday();
        if (view === "conviction") renderConviction();
      };
      ub.appendChild(b);
    }
  }
  syncUniverse();
  const s = m.scan;
  $("#scanline").textContent = s.scanned
    ? `Scanned ${int(s.scanned)} of ${int(s.eligible)} eligible stocks. ${int(s.with_patterns)} have patterns. ${int(s.skipped)} of ${int(s.listed)} listed skipped (under ${Math.round(s.min_history_bars / 250)} years of history, or stopped trading). Prices through ${fmtDate(s.data_through)}.`
    : "No scan yet. Use Scan again to download prices and find patterns.";
  const j = m.job || { running: false }, jb = $("#job");
  $("#rescan").hidden = !SRC.live;
  jb.hidden = !j.running;
  $("#rescan").disabled = !!j.running;
  $("#rescan").textContent = j.running ? "Scanning…" : "Scan again";
  if (j.running) {
    const pc = j.total ? Math.round(100 * j.done / j.total) : 0;
    jb.innerHTML = `${esc(j.stage[0].toUpperCase() + j.stage.slice(1))} ${j.total ? `${j.done}/${j.total}` : ""}<span class="bar"><i style="width:${pc}%"></i></span>`;
  }
  if (j.error) { jb.hidden = false; jb.textContent = "Last scan failed: " + j.error; }
  if (SRC.live) {
    if (jobWasRunning && !j.running) { intraCache = stocksCache = listCache = null; stockCache.clear(); refresh(); }
    jobWasRunning = !!j.running;
    if (lastScanAt && s.finished_at && s.finished_at !== lastScanAt) refresh();
    lastScanAt = s.finished_at || lastScanAt;
    setTimeout(loadMeta, j.running ? 1500 : 30000);
  }
}
function syncUniverse() {
  for (const b of $$("#universes .chip")) b.setAttribute("aria-pressed", b.dataset.v === state.universe);
}
function setView(v) {
  view = v;
  for (const b of $$(".views button")) b.setAttribute("aria-pressed", b.dataset.view === v);
  $("#view-patterns").hidden = v !== "patterns";
  $("#view-intraday").hidden = v !== "intraday";
  $("#view-conviction").hidden = v !== "conviction";
  $("#filters").hidden = v !== "patterns";
  $("#layout").classList.toggle("wide", v !== "patterns");
  if (v === "intraday") renderIntraday();
  if (v === "conviction") renderConviction();
  try { history.replaceState(null, "", "#" + v); } catch (e) { /* sandboxed frames may refuse */ }
}

function resetFilters() {
  const u = state.universe, sort = state.sort;
  state = { ...DEFAULTS(), universe: u, sort };
  $("#q").value = "";
  refresh();
}
async function startScan() {
  const r = await SRC.scan();
  if (r.ok) { jobWasRunning = true; loadMeta(); }
}

function wire() {
  buildFilters();
  let t;
  $("#q").addEventListener("keydown", e => {
    if ($("#suggest").hidden) return;
    if (e.key === "ArrowDown") { e.preventDefault(); moveSuggest(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveSuggest(-1); }
    else if (e.key === "Enter" && sugIdx >= 0) { e.preventDefault(); openStock(sugItems[sugIdx].symbol); }
    else if (e.key === "Escape") hideSuggest();
  });
  $("#q").addEventListener("focus", e => { if (e.target.value.trim()) suggest(e.target.value); });
  $("#q").addEventListener("blur", () => setTimeout(hideSuggest, 120));
  $("#modal").addEventListener("click", e => { if (e.target.id === "modal") closeModal(); });
  $("#q").addEventListener("input", e => {
    suggest(e.target.value);
    clearTimeout(t);
    t = setTimeout(() => {
      state.q = e.target.value;
      refresh();
      if (view === "intraday") renderIntraday();
      if (view === "conviction") renderConviction();
    }, 250);
  });
  $("#volume").onchange = e => { state.volume = e.target.checked; refresh(); };
  $("#book").onchange = e => { state.book = e.target.checked; refresh(); };
  $("#reset").onclick = resetFilters;
  $("#rescan").onclick = startScan;
  $("#more").onclick = () => { state.page += 1; refresh(true); };
  $("#filters-toggle").onclick = () => $("#filters").classList.toggle("open");
  for (const tb of $$(".tabs button")) tb.onclick = () => { state.sort = tb.dataset.sort; refresh(); };
  for (const b of $$(".views button")) b.onclick = () => setView(b.dataset.view);
  for (const b of $$("#cv-filter .chip")) b.onclick = () => {
    cvState.filter = b.dataset.v; cvState.shown = 100;
    for (const x of $$("#cv-filter .chip")) x.setAttribute("aria-pressed", x === b);
    renderConviction();
  };
  $("#cv-trend").onchange = e => { cvState.trend = e.target.checked; renderConviction(); };
  for (const b of $$("#cv-tf button")) b.onclick = () => { cvState.tf = b.dataset.v; cvState.shown = 100; renderConviction(); };
  for (const b of $$("#cv-order button")) b.onclick = () => { cvState.sort = "conv"; cvState.dir = +b.dataset.v; renderConviction(); };
  $("#cv-liquid").onchange = e => { cvState.liquid = e.target.checked; renderConviction(); };
  $("#cv-more").onclick = () => { cvState.shown += 100; renderConviction(); };
  for (const th of $$(".cv-table th")) {
    th.tabIndex = 0;
    const go = () => {
      const k = th.dataset.k;
      cvState.dir = cvState.sort === k ? -cvState.dir : (k === "symbol" ? 1 : -1);
      cvState.sort = k; renderConviction();
    };
    th.onclick = go;
    th.onkeydown = e => { if (e.key === "Enter") go(); };
  }
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    if (!$("#modal").hidden) closeModal();
    else $("#detail").classList.remove("open");
  });
}

wire();
loadMeta()
  .then(() => refresh())
  .then(() => {
    const h = (location.hash || "").slice(1);
    if (["intraday", "conviction", "patterns"].includes(h)) setView(h);
  })
  .catch(err => {
    $("#grid").innerHTML = `<div class="empty">Could not load the scan data (${esc(err.message)}). Reload the page to try again.</div>`;
  });
