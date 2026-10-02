"use strict";

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
const TF_WORD = { "1D": "daily", "1W": "weekly", "1M": "monthly" };
const DEFAULTS = () => ({
  universe: "all", q: "", family: [], direction: [], tf: [], status: [],
  quality: "any", within: "30", volume: false, book: false, sort: "composite", page: 1,
});

let state = DEFAULTS();
let selected = null;        // {symbol, id}
let lastScanAt = null;
let jobWasRunning = false;
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

// ---------------------------------------------------------------- formatting
const nf = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const nf1 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });
const rs = v => (v == null ? "—" : "₹" + (Math.abs(v) >= 1000 ? nf1.format(v) : nf.format(v)));
const pct = v => (v == null ? "" : (v > 0 ? "+" : "") + v.toFixed(1) + "%");
const dirSym = { bull: "▲", bear: "▼", neutral: "◆" };
const fmtDate = d => (d ? new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "—");
function candlesAgo(n, tf) {
  const w = TF_WORD[tf] || "";
  if (n === 0) return `latest ${w} candle`;
  return `${n} ${w} candle${n === 1 ? "" : "s"} ago`;
}
function whenText(p) {
  const ago = candlesAgo(p.bars_ago, p.tf);
  if (p.breakout_idx != null) return (p.status === "Failed" ? "Broke out, then failed, " : "Broke out ") + ago;
  if (p.status === "Marginal") return "Edged past the level, " + ago;
  if (p.bars_ago === 0) return `Still taking shape (${TF_WORD[p.tf]})`;
  return "Shape completed " + ago;
}
function hue(sym) { let h = 0; for (const c of sym) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }
function avatar(el, sym) {
  el.textContent = sym.slice(0, 2);
  el.style.background = `hsl(${hue(sym)} 32% 64%)`;
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

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
  const vb = svg.viewBox.baseVal;
  const W = vb.width, H = vb.height;
  const pad = big ? { l: 6, r: 84, t: 14, b: 22 } : { l: 4, r: 4, t: 8, b: 8 };
  const ch = p.chart, N = ch.c.length;
  if (N < 2) return;
  const iEnd = ch.i0 + ch.step * (N - 1);
  const xOf = i => pad.l + Math.max(0, Math.min(1, (i - ch.i0) / Math.max(1, iEnd - ch.i0))) * (W - pad.l - pad.r);

  const entry = p.breakout ?? p.range_up;
  let vals = ch.c.slice();
  for (const s of p.segments) if (s.x2 >= ch.i0) vals.push(s.y1, s.y2);
  [entry, p.range_dn, p.stop].forEach(v => v != null && vals.push(v));
  let lo = Math.min(...vals), hi = Math.max(...vals);
  const R = hi - lo || hi * 0.05;
  let tgt = p.target, tgtOff = false;
  if (tgt != null) {
    if (tgt > hi + 0.7 * R) { tgt = hi + 0.7 * R; tgtOff = true; }
    if (tgt < lo - 0.7 * R) { tgt = lo - 0.7 * R; tgtOff = true; }
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
  // price
  let d = "";
  ch.c.forEach((v, k) => { d += (k ? "L" : "M") + xOf(ch.i0 + k * ch.step).toFixed(1) + " " + yOf(v).toFixed(1); });
  el("path", { d: d + `L${xOf(iEnd)} ${H - pad.b}L${xOf(ch.i0)} ${H - pad.b}Z`, class: "c-area" }, svg);
  el("path", { d, class: "c-price" }, svg);

  // pattern geometry
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
    if (["upper", "lower", "neck", "rim"].includes(s.kind)) {
      const cls = p.direction === "neutral" ? "c-bound" : "c-break";
      el("line", { x1: xOf(s.x1), y1: yOf(s.y1), x2: xOf(s.x2), y2: yOf(s.y2), class: cls }, svg);
    }
  }
  // target / stop from the event to the right edge
  const xe = xOf(p.breakout_idx ?? p.end);
  const xr = W - pad.r;
  if (p.stop != null) el("line", { x1: xe, x2: xr, y1: yOf(p.stop), y2: yOf(p.stop), class: "c-stop" }, svg);
  if (tgt != null) el("line", { x1: xe, x2: xr, y1: yOf(tgt), y2: yOf(tgt), class: "c-target" }, svg);
  if (p.breakout_idx != null && entry != null) el("circle", { cx: xOf(p.breakout_idx), cy: yOf(entry), r: big ? 3.5 : 2.6, class: "c-bo" }, svg);

  if (!big) return;
  for (const [i, v, lab] of p.points) {
    if (i < ch.i0) continue;
    el("circle", { cx: xOf(i), cy: yOf(v), r: 2.6, class: "c-pt" }, svg);
    if (lab) {
      const above = p.points.some(q => q[2] === lab) && (["Top", "Head", "LS", "RS", "Pole", "R", "L"].includes(lab) ? v >= (lo + hi) / 2 : v > (lo + hi) / 2);
      const t = el("text", { x: xOf(i), y: yOf(v) + (above ? -7 : 14), "text-anchor": "middle", class: "c-lab" }, svg);
      t.textContent = lab;
    }
  }
  const tags = [];
  if (entry != null) tags.push(["b", yOf(entry), (p.breakout_idx != null ? "Breakout " : "Level ") + rs(entry)]);
  if (p.range_dn != null) tags.push(["b", yOf(p.range_dn), "Level " + rs(p.range_dn)]);
  if (p.target != null) tags.push(["t", yOf(tgt), "Target " + rs(p.target) + (tgtOff ? (p.target > hi ? " ↑" : " ↓") : "")]);
  if (p.stop != null) tags.push(["s", yOf(p.stop), "Stop " + rs(p.stop)]);
  tags.sort((a, b) => a[1] - b[1]);
  for (let k = 1; k < tags.length; k++) if (tags[k][1] - tags[k - 1][1] < 12) tags[k][1] = tags[k - 1][1] + 12;
  for (const [c, y, txt] of tags) {
    const t = el("text", { x: W - pad.r + 6, y: Math.min(H - pad.b, y) + 3.5, class: "c-tag " + c }, svg);
    t.textContent = txt;
  }
  const ax = (x, txt, anchor) => { const t = el("text", { x, y: H - 6, "text-anchor": anchor, class: "c-axis" }, svg); t.textContent = txt; };
  ax(pad.l, fmtDate(ch.d0), "start");
  ax(W - pad.r, fmtDate(ch.d1), "end");
}

// ---------------------------------------------------------------- filters UI
function buildFilters() {
  for (const box of $$("[data-facet]")) {
    const f = box.dataset.facet;
    box.innerHTML = "";
    for (const [v, label] of FACETS[f]) {
      const b = document.createElement("button");
      b.className = "chip"; b.dataset.v = v;
      b.innerHTML = `${esc(label)}<b></b>`;
      b.onclick = () => {
        const arr = state[f];
        const i = arr.indexOf(v);
        i >= 0 ? arr.splice(i, 1) : arr.push(v);
        refresh();
      };
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
      const n = facets?.[f]?.[b.dataset.v] ?? 0;
      $("b", b).textContent = n.toLocaleString("en-IN");
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

// ---------------------------------------------------------------- data
function queryString() {
  const qs = new URLSearchParams();
  for (const k of ["universe", "q", "within", "quality", "sort", "page"]) if (state[k] !== "" && state[k] != null) qs.set(k, state[k]);
  for (const k of ["family", "direction", "tf", "status"]) state[k].forEach(v => qs.append(k, v));
  if (state.volume) qs.set("volume", "1");
  if (state.book) qs.set("from_book", "1");
  return qs.toString();
}

let reqId = 0;
async function refresh(append = false) {
  if (!append) state.page = 1;
  const id = ++reqId;
  const res = await fetch("/api/patterns?" + queryString()).then(r => r.json());
  if (id !== reqId) return;
  renderStats(res);
  syncFilterUI(res.facets);
  renderGrid(res, append);
}

let metaCache = null;
async function loadMeta() {
  const m = await fetch("/api/meta").then(r => r.json());
  metaCache = m;
  const mk = $("#mkt");
  mk.className = "mkt" + (m.market.open ? " open" : "");
  mk.innerHTML = `<span class="dot"></span><b>${m.market.open ? "Market open" : "Market closed"}</b> ${esc(m.market.time)}, ${esc(m.market.note)}`;

  const ub = $("#universes");
  if (!ub.childElementCount) {
    for (const u of m.universes) {
      const b = document.createElement("button");
      b.className = "chip"; b.dataset.v = u.key; b.textContent = u.label;
      b.disabled = !u.available;
      if (!u.available) b.title = "List not downloaded yet. Run a scan to fetch index lists.";
      b.onclick = () => { state.universe = u.key; refresh(); syncUniverse(); };
      ub.appendChild(b);
    }
  }
  syncUniverse();
  const s = m.scan;
  $("#scanline").textContent = s.scanned
    ? `Scanned ${s.scanned.toLocaleString("en-IN")} of ${s.eligible.toLocaleString("en-IN")} eligible stocks in ${s.seconds}s. ` +
      `${s.with_patterns.toLocaleString("en-IN")} have patterns. ${s.skipped.toLocaleString("en-IN")} of ${s.listed.toLocaleString("en-IN")} listed skipped (need ${Math.round(s.min_history_bars / 250)}+ years of history). ` +
      `Prices through ${fmtDate(s.data_through)}.`
    : "No scan yet. Use Scan again to download prices and find patterns.";

  const j = m.job, jb = $("#job");
  jb.hidden = !j.running;
  $("#rescan").disabled = j.running;
  $("#rescan").textContent = j.running ? "Scanning…" : "Scan again";
  if (j.running) {
    const p = j.total ? Math.round(100 * j.done / j.total) : 0;
    jb.innerHTML = `${esc(j.stage[0].toUpperCase() + j.stage.slice(1))} ${j.total ? `${j.done}/${j.total}` : ""}<span class="bar"><i style="width:${p}%"></i></span>`;
  }
  if (j.error) { jb.hidden = false; jb.textContent = "Last scan failed: " + j.error; }
  if (jobWasRunning && !j.running) refresh();
  jobWasRunning = j.running;
  if (lastScanAt && s.finished_at && s.finished_at !== lastScanAt) refresh();
  lastScanAt = s.finished_at || lastScanAt;
  setTimeout(loadMeta, j.running ? 1500 : 30000);
}
function syncUniverse() {
  for (const b of $$("#universes .chip")) b.setAttribute("aria-pressed", b.dataset.v === state.universe);
}

function renderStats(res) {
  const s = metaCache?.scan || {};
  const cells = [
    ["Scanned", (s.scanned ?? 0).toLocaleString("en-IN"), `of ${(s.eligible ?? 0).toLocaleString("en-IN")} eligible stocks`],
    ["Patterns", res.total.toLocaleString("en-IN"), "all ages, all timeframes"],
    ["In view", res.in_view.toLocaleString("en-IN"), "with the filters below"],
    ["Confirmed", res.confirmed.toLocaleString("en-IN"), "held the break, in view"],
    ["Bull / bear", `<span class="up">${res.bull.toLocaleString("en-IN")}</span> / <span class="down">${res.bear.toLocaleString("en-IN")}</span>`, "resolved direction only"],
    ["Prices through", s.data_through ? fmtDate(s.data_through) : "—", "latest daily candle"],
  ];
  $("#stats").innerHTML = cells.map(([k, v, sub]) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${sub}</div></div>`).join("");
  $("#count").innerHTML = `<b>${res.in_view.toLocaleString("en-IN")}</b> patterns of ${res.total.toLocaleString("en-IN")}. Found by shape rules on closing prices, not advice.`;
}

function renderGrid(res, append) {
  const grid = $("#grid");
  if (!append) grid.innerHTML = "";
  if (!res.items.length && !append) {
    grid.innerHTML = `<div class="empty">${res.total ? "No patterns match these filters." : "No scan results yet."}<br>` +
      (res.total ? `<button class="btn" id="clear">Clear filters</button>` : `<button class="btn" id="scan2">Scan now</button>`) + `</div>`;
    $("#clear")?.addEventListener("click", resetFilters);
    $("#scan2")?.addEventListener("click", startScan);
  }
  const tpl = $("#card-tpl");
  for (const p of res.items) {
    const c = tpl.content.firstElementChild.cloneNode(true);
    c.dataset.id = p.id; c.dataset.sym = p.symbol;
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
    if (p.direction === "neutral") {
      $(".b", c).textContent = rs(p.range_up);
      $$(".levels dt", c)[0].textContent = "Upper";
      $$(".levels dt", c)[1].textContent = "Lower";
      $(".t", c).textContent = rs(p.range_dn); $(".t", c).className = "t";
      $$(".levels dt", c)[2].textContent = "Size";
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
    if (selected?.id === p.id) c.classList.add("sel");
    c.onclick = () => select(p.symbol, p.id);
    c.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(p.symbol, p.id); } };
    grid.appendChild(c);
  }
  const more = $("#more");
  more.hidden = res.page >= res.pages;
  more.textContent = `Show more patterns (${(res.in_view - res.page * 48).toLocaleString("en-IN")} left)`;
  if (!append && !selected && res.items.length && window.innerWidth > 1080) select(res.items[0].symbol, res.items[0].id);
}

// ---------------------------------------------------------------- detail
const stockCache = new Map();
async function select(sym, id) {
  selected = { symbol: sym, id };
  for (const c of $$(".card")) c.classList.toggle("sel", c.dataset.id === id);
  let d = stockCache.get(sym);
  if (!d) {
    d = await fetch("/api/stock/" + encodeURIComponent(sym)).then(r => r.json());
    stockCache.set(sym, d);
    if (stockCache.size > 50) stockCache.delete(stockCache.keys().next().value);
  }
  renderDetail(d, id);
  $("#detail").classList.add("open");
}

function renderDetail(d, id) {
  const p = d.patterns.find(x => x.id === id) || d.patterns[0];
  const i = d.info, s = d.summary;
  const chg = i.prev_close ? (i.last_close / i.prev_close - 1) * 100 : null;
  const box = $("#detail");
  box.innerHTML = `
    <div class="d-head">
      <span class="avatar"></span>
      <div><h2>${esc(i.symbol)}</h2><p>${esc(i.name || "NSE equity")}</p></div>
      <a href="https://www.tradingview.com/chart/?symbol=NSE:${encodeURIComponent(i.symbol.replace(/_SME$/, ""))}" target="_blank" rel="noopener">Open chart</a>
      <button class="btn ghost close-detail" aria-label="Close">✕</button>
    </div>
    <div class="d-stats">
      <div><div class="k">Last close</div><div class="v">${rs(i.last_close)}</div><div class="s ${chg >= 0 ? "up" : "down"}">${chg == null ? "" : pct(chg)} on ${fmtDate(i.last_date)}</div></div>
      <div><div class="k">History</div><div class="v">${i.years} years</div><div class="s">${i.bars.toLocaleString("en-IN")} daily candles</div></div>
      <div><div class="k">Patterns</div><div class="v">${s.patterns}</div><div class="s">${s.timeframes.join(", ")}</div></div>
      <div><div class="k">Confirmed</div><div class="v">${s.confirmed}</div><div class="s">${s.held} not stopped out</div></div>
      <div><div class="k">Reached target</div><div class="v">${s.target_hit}</div><div class="s">${s.stopped} hit the stop first</div></div>
      <div><div class="k">Forming now</div><div class="v">${s.forming}</div><div class="s">not yet broken out</div></div>
    </div>
    <div class="d-title"><span class="dir ${p.direction}">${dirSym[p.direction]}</span><h3>${esc(p.pattern)}</h3></div>
    <div class="d-sub">
      <span class="tag ${p.direction}">${p.direction === "bull" ? "Bullish" : p.direction === "bear" ? "Bearish" : "Unresolved"}</span>
      <span class="tag">${TF_WORD[p.tf]}</span><span class="tag">${esc(p.status)}</span><span class="tag">${esc(p.family)}</span>
    </div>
    <svg class="big" viewBox="0 0 380 260" role="img" aria-label="${esc(p.pattern)} chart"></svg>
    <ul class="legend">
      <li><i style="border-color:#e4e7ec"></i>Shape through the swing closes</li>
      <li><i style="border-color:var(--brass)"></i>Line a close must cross</li>
      <li><i style="border-color:var(--bull);border-top-style:dashed"></i>Measured target</li>
      <li><i style="border-color:var(--bear);border-top-style:dotted"></i>Stop</li>
    </ul>
    <div class="facts">
      <div><div class="k">${p.breakout_idx != null ? "Broke out" : "Shape completed"}</div><div class="v">${fmtDate(p.breakout_date || p.end_date)}</div><div class="s">${candlesAgo(p.bars_ago, p.tf)}</div></div>
      <div><div class="k">Spans</div><div class="v">${p.span} ${TF_WORD[p.tf]} candles</div><div class="s">${fmtDate(p.start_date)} to ${fmtDate(p.end_date)}</div></div>
      <div><div class="k">Shape quality</div><div class="v">${esc(p.quality)} (${p.score}/100)</div><div class="s">fit to the textbook shape</div></div>
      <div><div class="k">${p.outcome ? "Since the breakout" : "Last close vs level"}</div><div class="v">${esc(p.outcome || (p.vs_breakout != null ? pct(p.vs_breakout) : "—"))}</div><div class="s">${p.volume_confirmed ? "Breakout on 1.5× average volume" : p.volume_confirmed === false ? "Breakout volume was ordinary" : "Close " + rs(p.last_close)}</div></div>
    </div>
    <dl class="levels">
      ${p.direction === "neutral"
        ? `<div><dt>Upper</dt><dd>${rs(p.range_up)}</dd></div><div><dt>Lower</dt><dd>${rs(p.range_dn)}</dd></div><div><dt>R:R</dt><dd>—</dd></div>`
        : `<div><dt>Breakout</dt><dd>${rs(p.breakout)}</dd></div><div><dt>Target</dt><dd class="up">${rs(p.target)}</dd></div><div><dt>Stop</dt><dd class="down">${rs(p.stop)}</dd></div>`}
    </dl>
    <p class="hint">${p.rr ? `Reward to risk 1 : ${p.rr}, measured from the breakout level.` : ""}</p>
    <div class="history"><h3>All ${s.patterns} patterns on ${esc(i.symbol)}, newest first</h3><ol></ol></div>`;
  avatar($(".avatar", box), i.symbol);
  drawChart($(".big", box), p, true);
  $(".close-detail", box).onclick = () => box.classList.remove("open");
  const ol = $(".history ol", box);
  for (const q of d.patterns.slice(0, 120)) {
    const li = document.createElement("li");
    li.tabIndex = 0;
    if (q.id === p.id) li.classList.add("sel");
    li.innerHTML = `<span class="dir ${q.direction}">${dirSym[q.direction]}</span>
      <span><span>${esc(q.pattern)}</span> <span class="when">${q.tf}, ${fmtDate(q.breakout_date || q.end_date)}</span></span>
      <span class="res">${esc(q.outcome || q.status)}</span>`;
    li.onclick = () => { selected = { symbol: i.symbol, id: q.id }; renderDetail(d, q.id); };
    li.onkeydown = e => { if (e.key === "Enter") li.onclick(); };
    ol.appendChild(li);
  }
}

// ---------------------------------------------------------------- actions
function resetFilters() {
  const u = state.universe, sort = state.sort;
  state = { ...DEFAULTS(), universe: u, sort };
  $("#q").value = "";
  refresh();
}
async function startScan() {
  const r = await fetch("/api/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sync: true }) });
  if (r.ok) { jobWasRunning = true; loadMeta(); }
}

function wire() {
  buildFilters();
  let t;
  $("#q").addEventListener("input", e => { clearTimeout(t); t = setTimeout(() => { state.q = e.target.value; refresh(); }, 250); });
  $("#volume").onchange = e => { state.volume = e.target.checked; refresh(); };
  $("#book").onchange = e => { state.book = e.target.checked; refresh(); };
  $("#reset").onclick = resetFilters;
  $("#rescan").onclick = startScan;
  $("#more").onclick = () => { state.page += 1; refresh(true); };
  $("#filters-toggle").onclick = () => $("#filters").classList.toggle("open");
  for (const tb of $$(".tabs button")) tb.onclick = () => { state.sort = tb.dataset.sort; refresh(); };
  document.addEventListener("keydown", e => { if (e.key === "Escape") $("#detail").classList.remove("open"); });
}

wire();
loadMeta().then(() => refresh());
