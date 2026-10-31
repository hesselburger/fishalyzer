// Fishalyzer frontend - plain JS, no build step.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
const SPECIES = ["Perch", "Pike", "Zander", "Sea trout", "Roach", "Garfish", "Cod", "Ide"];

let user = null; // logged-in {id, name, admin}
const store = {
  get me() { return user ? user.name : ""; },
  get spot() { try { return +localStorage.getItem("spot") || 0; } catch { return 0; } },
  set spot(v) { try { localStorage.setItem("spot", v); } catch {} },
};
let spots = [];
let lastForecast = null;
let calMonth = new Date().toISOString().slice(0, 7);
let selDay = null;

// ---------- helpers ----------
async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401 && !path.startsWith("/auth/login")) { showLogin(); throw new Error("Please log in"); }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const localNow = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const today = () => localNow().slice(0, 10);
const hm = (ts) => ts ? ts.slice(11, 16) : "…";
const dayName = (d) => new Date(d + "T12:00").toLocaleDateString("en-GB", { weekday: "short" });
const longDate = (d) => new Date(d + "T12:00").toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
const DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
const dir = (deg) => deg == null ? "" : DIRS[Math.floor(((deg + 22.5) % 360) / 45)];

function scoreColor(s) {
  // red (0) -> yellow (50) -> green (100)
  const lerp = (a, b, t) => Math.round(a + (b - a) * t);
  const [r1, g1, b1, r2, g2, b2, t] = s < 50 ? [224, 98, 90, 232, 197, 71, s / 50] : [232, 197, 71, 76, 195, 138, (s - 50) / 50];
  return `rgb(${lerp(r1, r2, t)},${lerp(g1, g2, t)},${lerp(b1, b2, t)})`;
}
const scoreBadge = (s, cls = "sm") => `<span class="score ${cls}" style="background:${scoreColor(s)}">${s}</span>`;
// Multiplier as "+18%" / "-23%" compared with an average hour.
const pct = (m) => { const p = Math.round((m - 1) * 100); return p === 0 ? "±0%" : (p > 0 ? "+" : "−") + Math.abs(p) + "%"; };
const reasonChips = (rs) => (rs || []).map((r) =>
  `<button type="button" class="reason ${r.m >= 1 ? "up" : "down"}" data-k="${r.key}" data-b="${r.b}" data-m="${r.m}" data-w="${r.data_weight}">${esc(r.feature)}: ${esc(r.bin)} ${pct(r.m)}</button>`).join("");
const GLOSS_LINK = `<a href="#" class="small gloss">What do the tags mean?</a>`;

// ---------- explanations ----------
let glossary = [];
const gloss = (k) => glossary.find((g) => g.key === k);
function conditionChip(k, b) {
  const g = gloss(k), bin = g?.bins.find((x) => x.key === b);
  return `<button type="button" class="reason" data-k="${k}" data-b="${b}">${esc(g?.short || k)}: ${esc(bin?.label || b)}</button>`;
}
function explainTag(k, b, m, w) {
  const g = gloss(k), bin = g?.bins.find((x) => x.key === b);
  if (!g) return;
  const effect = m == null ? "" : `<div class="card"><b>${pct(m)}</b> compared with an average hour for you.
    <div class="muted small" style="margin-top:4px">${w > 0.5 ? "Mostly based on your own logged trips." : w > 0 ? "Partly your logs, partly built-in angler folklore." : "Built-in angler folklore so far - no trips logged in these conditions yet."}</div></div>`;
  openModal(`<h1>${esc(g.short)}: ${esc(bin?.label || b)}</h1>
    ${bin?.help ? `<p><b>${esc(bin.help)}</b></p>` : ""}<p class="muted">${esc(g.desc)}</p>${effect}
    <a href="#" class="gloss">All tags explained</a>`);
}
function glossaryModal() {
  openModal(`<h1>What the tags mean</h1>
    <p class="muted">Each tag is a condition during that hour. The % says how much better (green) or worse (red) that condition
      has been than an average hour - first from built-in angler folklore, then more and more from your own trips.
      The score (0-100) combines all conditions; 50 is an average hour for you.</p>
    ${glossary.map((g) => `<div class="card"><h3>${esc(g.short)}</h3><div class="muted small">${esc(g.desc)}</div>
      ${g.bins.some((b) => b.help) ? `<div style="margin-top:6px">${g.bins.filter((b) => b.help).map((b) => `<div class="small" style="margin-top:3px"><b>${esc(b.label)}</b> - ${esc(b.help)}</div>`).join("")}</div>` : ""}</div>`).join("")}`);
}
document.addEventListener("click", (e) => {
  const chip = e.target.closest(".reason[data-k]");
  if (chip) { const d = chip.dataset; explainTag(d.k, d.b, d.m ? +d.m : null, +d.w || 0); return; }
  if (e.target.closest(".gloss")) { e.preventDefault(); glossaryModal(); }
});
const topFishText = (f) => `${esc(f.species)}${f.length_cm ? ` ${f.length_cm} cm` : ""}${f.weight_g ? ` · ${f.weight_g} g` : ""}`;

function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg; t.className = err ? "err" : ""; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
}
function openModal(html) {
  $("#modal .body").innerHTML = html; $("#modal").hidden = false;
  return $("#modal .body");
}
let modalResolve = null; // pending answer of askConfirm/askText; closing the sheet counts as "cancel"
function closeModal() {
  $("#modal").hidden = true;
  if (modalResolve) { const r = modalResolve; modalResolve = null; r(null); }
}
// In-app replacements for confirm()/prompt(): those are silently blocked in some browsers and home-screen apps.
function askConfirm(message, okLabel = "Delete") {
  const body = openModal(`<h1>${esc(message)}</h1>
    <button class="danger block" id="askok">${esc(okLabel)}</button><button class="ghost block" id="askno">Cancel</button>`);
  return new Promise((resolve) => {
    modalResolve = (v) => resolve(!!v);
    $("#askok", body).onclick = () => { const r = modalResolve; modalResolve = null; closeModal(); r(true); };
    $("#askno", body).onclick = closeModal;
  });
}
function askText(title, value = "") {
  const body = openModal(`<h1>${esc(title)}</h1><form><input name="v" value="${esc(value)}" autocomplete="off">
    <button class="primary block">Save</button></form>`);
  const form = $("form", body);
  setTimeout(() => form.v.focus(), 50);
  return new Promise((resolve) => {
    modalResolve = resolve;
    form.onsubmit = (e) => { e.preventDefault(); const r = modalResolve; modalResolve = null; closeModal(); r(form.v.value); };
  });
}
$("#modal .close").onclick = closeModal;
$("#modal").onclick = (e) => { if (e.target.id === "modal") closeModal(); };

const spotOptions = (sel) => spots.map((s) => `<option value="${s.id}" ${s.id == sel ? "selected" : ""}>${esc(s.name)}</option>`).join("");
async function loadSpots() {
  spots = await api("/spots");
  if (!spots.find((s) => s.id === store.spot) && spots[0]) store.spot = spots[0].id;
}
const numOrNull = (v) => (v === "" || v == null ? null : +v);

// ---------- status bar ----------
async function refreshStatus() {
  const bar = $("#statusbar");
  try {
    const s = await api("/status");
    const wxErr = s.weather.last_error_at && (!s.weather.last_ok || s.weather.last_error_at > s.weather.last_ok);
    const color = wxErr ? "var(--mid)" : "var(--good)";
    const pct = Math.round(s.model.confidence * 100);
    bar.innerHTML = `<span class="dot" style="background:${color}"></span><b>Online</b> · ${s.db.trips} trips · ${s.db.fish} fish` +
      ` · wx ${wxErr ? "⚠" : "✓"} · learned <b>${pct}%</b>`;
    bar.onclick = () => openModal(statusDetails(s));
  } catch (e) {
    bar.innerHTML = `<span class="dot" style="background:var(--bad)"></span><b>Offline</b> · server unreachable`;
  }
}
function statusDetails(s) {
  const pct = Math.round(s.model.confidence * 100);
  return `<h1>System status</h1>
  <div class="card"><h3>Server</h3><div class="muted">Version ${esc(s.version)} · up ${Math.floor(s.uptime_s / 3600)}h ${Math.floor((s.uptime_s % 3600) / 60)}m</div>
    <div class="muted">${s.db.spots} spots · ${s.db.trips} trips · ${s.db.fish} fish · ${s.db.weather} cached weather hours</div></div>
  <div class="card"><h3>Weather (Open-Meteo)</h3>
    <div class="muted">Last success: ${esc(s.weather.last_ok || "not yet")}</div>
    ${s.weather.last_error ? `<div class="muted">Last error (${esc(s.weather.last_error_at)}): ${esc(s.weather.last_error)}</div>` : ""}
    <button class="sm ghost" id="wxsync" style="margin-top:8px">Backfill weather for all trips</button></div>
  <div class="card"><h3>Model</h3>
    <div class="row spread"><span>Learned from your data</span><b>${pct}%</b></div><div class="meter"><i style="width:${pct}%"></i></div>
    <div class="muted" style="margin-top:6px">${s.model.hours} hours fished · ${s.model.catches} fish${s.model.hours_missing_weather ? ` · ${s.model.hours_missing_weather} hours without weather (backfill above)` : ""}</div>
    <div class="muted small" style="margin-top:6px">Reaches 100% around 60 fish and 75 hours fished. Until then the folklore prior fills the gaps.</div></div>`;
}
document.addEventListener("click", async (e) => {
  if (e.target.id === "wxsync") {
    e.target.disabled = true; e.target.textContent = "Syncing…";
    try { await api("/weather/sync", { method: "POST" }); toast("Weather synced"); } catch (err) { toast(err.message, true); }
    closeModal(); refreshStatus();
  }
});

// ---------- Today ----------
async function viewToday() {
  const recent = await api("/trips?limit=3");
  const me = store.me;
  view.innerHTML = `
    <h1>Hi ${esc(me)}</h1>
    <div class="card"><div class="row spread"><div><h3>Been fishing?</h3>
      <div class="muted">Log the day: where, when, how many fish and the best ones. Blank days count too.</div></div></div>
      <a class="btn primary" href="#log" style="display:block;margin-top:10px">＋ Log a trip</a>
      ${recent.length ? `<div style="margin-top:10px">${recent.map((t) => `<div class="item row spread" data-date="${t.start.slice(0, 10)}" style="cursor:pointer">
        <div><b>${longDate(t.start.slice(0, 10))}</b><div class="muted small">${esc(t.spot_name)} · ${hm(t.start)}-${hm(t.end)}</div></div><b>🐟 ${t.n_fish}</b></div>`).join("")}</div>` : ""}</div>
    <h2>Forecast</h2>
    <div class="card"><div class="row"><select id="fcspot" class="grow">${spotOptions(store.spot)}</select><a class="btn sm" href="#spot" title="Add a spot on the map">＋ Spot</a></div><div id="fc" style="margin-top:12px"><div class="empty">Loading forecast…</div></div></div>`;

  $$("[data-date]").forEach((el) => (el.onclick = () => dayModal(el.dataset.date)));
  $("#fcspot").onchange = (e) => { store.spot = +e.target.value; selDay = null; loadForecast(); };
  loadForecast();
}

async function loadForecast() {
  const box = $("#fc");
  if (!box) return;
  try {
    lastForecast = await api(`/forecast?spot_id=${store.spot}`);
  } catch (e) {
    box.innerHTML = `<div class="empty">Couldn't get the forecast: ${esc(e.message)}</div>`; return;
  }
  renderForecast();
}

function renderForecast() {
  const f = lastForecast, box = $("#fc");
  if (!f || !box) return;
  selDay = selDay || f.daily[0]?.date;
  const d = f.daily.find((x) => x.date === selDay) || f.daily[0];
  const hours = f.hours.filter((h) => h.time.startsWith(selDay));
  const best = hours.reduce((a, h) => (h.features.light !== "night" && (!a || h.score > a.score) ? h : a), null);
  const conf = Math.round(f.confidence * 100);
  box.innerHTML = `
    <div class="row" style="gap:14px">${scoreBadge(d.best, "big")}
      <div class="grow"><b>${longDate(d.date)}</b>
        <div class="muted">Best around ${d.best_time || "-"}${best?.expected != null ? ` · ~${best.expected} fish/hour` : ""}</div>
        <div>${reasonChips(best?.reasons)}</div></div></div>
    <div class="days" style="margin-top:14px">${f.daily.map((x) => `
      <div class="d ${x.date === selDay ? "on" : ""}" data-day="${x.date}"><div class="small muted">${dayName(x.date)}</div>${scoreBadge(x.best)}</div>`).join("")}</div>
    ${hourChart(hours)}
    <div class="row spread" style="margin-top:14px"><h3 style="margin:0">Best 3-hour windows this week</h3>${GLOSS_LINK}</div>
    ${f.windows.map((w) => `<div class="window">${scoreBadge(w.score)}<div><b>${dayName(w.start.slice(0, 10))} ${hm(w.start)}-${pad(+w.end.slice(11, 13) + 1)}:00</b>
      <div>${reasonChips(w.reasons)}</div></div></div>`).join("") || `<div class="muted">No daylight windows in range.</div>`}
    <div class="muted small" style="margin-top:10px">Model is ${conf}% your own data${conf < 30 ? " - mostly folklore so far. Log trips (also blank ones!) to teach it." : "."}
      50 = an average hour for you. Tap a tag to see what it means.</div>`;
  $$("[data-day]", box).forEach((el) => (el.onclick = () => { selDay = el.dataset.day; renderForecast(); }));
}

function hourChart(hours) {
  if (!hours.length) return "";
  const w = 24 * 14, h = 110;
  const bars = hours.map((x) => {
    const i = +x.time.slice(11, 13), bh = Math.max(2, (x.score / 100) * (h - 18));
    const night = x.features.light === "night";
    const tip = `${x.time.slice(11, 16)} score ${x.score} · ${x.wx.temp ?? "?"}° · ${x.wx.wind_speed ?? "?"} m/s ${dir(x.wx.wind_dir)} · ${x.wx.pressure ?? "?"} hPa`;
    return `<rect x="${i * 14 + 1}" y="${h - 16 - bh}" width="12" height="${bh}" rx="2" fill="${scoreColor(x.score)}" opacity="${night ? 0.35 : 1}"><title>${tip}</title></rect>`;
  }).join("");
  const labels = [0, 3, 6, 9, 12, 15, 18, 21].map((i) => `<text x="${i * 14 + 7}" y="${h - 3}" font-size="9" fill="#8fb0b8" text-anchor="middle">${pad(i)}</text>`).join("");
  return `<svg class="hourchart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="margin-top:12px">
    <line x1="0" x2="${w}" y1="${h - 16 - (h - 18) / 2}" y2="${h - 16 - (h - 18) / 2}" stroke="#24505e" stroke-dasharray="3 3"/>${bars}${labels}</svg>
    <div class="muted small">Hourly score (faded = night, dashed = average). Tap a bar for weather.</div>`;
}

// ---------- Calendar ----------
async function viewCalendar() {
  const [y, m] = calMonth.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  const startOffset = (first.getDay() + 6) % 7; // Monday first
  const daysIn = new Date(y, m, 0).getDate();
  const data = await api(`/calendar?month=${calMonth}`);
  if (!lastForecast) { try { lastForecast = await api(`/forecast?spot_id=${store.spot}`); } catch {} }
  const fc = Object.fromEntries((lastForecast?.daily || []).map((d) => [d.date, d.best]));
  const t = today();
  let cells = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => `<div class="h">${d}</div>`).join("");
  for (let i = 0; i < startOffset; i++) cells += `<div class="c out"></div>`;
  for (let d = 1; d <= daysIn; d++) {
    const ds = `${calMonth}-${pad(d)}`, info = data[ds] || {};
    cells += `<div class="c ${ds === t ? "today" : ""}" data-date="${ds}"><span class="n">${d}</span>
      ${info.trips ? `<span class="fish">🐟${info.catches}</span>${info.biggest ? `<span class="muted small">${info.biggest}cm</span>` : ""}` : ""}
      ${info.trips ? `<span class="trip" title="trip logged"></span>` : ""}
      ${fc[ds] != null && ds >= t ? `<span class="fc" style="background:${scoreColor(fc[ds])}" title="forecast ${fc[ds]}"></span>` : ""}</div>`;
  }
  const monthName = first.toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  view.innerHTML = `<div class="row spread"><button class="sm ghost" id="prev">‹</button><h1>${monthName}</h1><button class="sm ghost" id="next">›</button></div>
    <div class="cal">${cells}</div>
    <div class="muted small" style="margin-top:10px">Tap a day to see or log trips. 🐟 fish caught · grey dot = trip logged · coloured dot = forecast for ${esc(lastForecast?.spot?.name || "your spot")}</div>`;
  const shift = (n) => { const d = new Date(y, m - 1 + n, 1); calMonth = `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; viewCalendar(); };
  $("#prev").onclick = () => shift(-1);
  $("#next").onclick = () => shift(1);
  $$("[data-date]").forEach((el) => (el.onclick = () => dayModal(el.dataset.date)));
  if (openDayAfterRoute) { const d = openDayAfterRoute; openDayAfterRoute = null; dayModal(d); }
}

let openDayAfterRoute = null; // set after saving a trip so the calendar opens on that day
async function dayModal(d) {
  const data = await api(`/day/${d}`);
  const fcDay = lastForecast?.daily.find((x) => x.date === d);
  const future = d > today();
  const body = openModal(`<h1>${longDate(d)}</h1>
    ${fcDay ? `<div class="card row">${scoreBadge(fcDay.best)}<div>Forecast best ${fcDay.best_time} at ${esc(lastForecast.spot.name)}</div></div>` : ""}
    ${data.trips.map((t) => `<div class="card">
      <div class="row spread"><h3>${esc(t.spot_name)} · ${hm(t.start)}-${hm(t.end)}</h3><b>🐟 ${t.n_fish}</b></div>
      <div class="muted">${esc(t.anglers)}${t.counts.length ? " · " + t.counts.map((c) => `${c.count} ${esc(c.species)}`).join(", ") : " · blank day"}</div>
      ${t.notes ? `<div class="muted small">${esc(t.notes)}</div>` : ""}
      ${t.top_fish.length ? `<div style="margin-top:6px">${t.top_fish.map((f) => `<div class="item"><b>🏆 ${topFishText(f)}</b>
        <div class="muted small">${[f.angler, f.lure, f.released ? "released" : "kept"].filter(Boolean).map(esc).join(" · ")}</div></div>`).join("")}</div>` : ""}
      ${t.conditions ? `<div style="margin-top:6px">${Object.entries(t.conditions).map(([k, v]) => conditionChip(k, v)).join("")}</div>` : ""}
      ${t.weather?.length ? `<details style="margin-top:8px"><summary class="muted">Weather during trip</summary><div class="scroll-x"><table class="wx">
        <tr><th>Time</th><th>°C</th><th>hPa</th><th>Wind</th><th>Cloud</th><th>mm</th><th>Sea°</th></tr>
        ${t.weather.map((w) => `<tr><td>${w.time.slice(11, 16)}</td><td>${w.temp ?? ""}</td><td>${w.pressure ?? ""}</td><td>${w.wind_speed ?? ""} ${dir(w.wind_dir)}</td><td>${w.cloud ?? ""}%</td><td>${w.precip ?? ""}</td><td>${w.sst ?? ""}</td></tr>`).join("")}
        </table></div></details>` : `<div class="muted small" style="margin-top:6px">Weather not fetched yet.</div>`}
      <a class="btn sm ghost" href="#log?id=${t.id}" style="margin-top:10px">Edit</a></div>`).join("")
      || `<div class="empty">${future ? "This day hasn't happened yet." : "No trips this day."}</div>`}
    ${future ? "" : `<a class="btn primary block" href="#log?date=${d}" style="display:block;margin-top:14px">＋ Log a trip on this day</a>`}`);
  $$("a", body).forEach((a) => a.addEventListener("click", closeModal));
}

// ---------- Log (one trip = one day, logged afterwards) ----------
async function viewLog(params) {
  let t;
  if (params.get("id")) {
    t = await api(`/trips/${params.get("id")}`);
  } else {
    const last = (await api("/trips?limit=1"))[0];
    t = {
      date: params.get("date") || today(), spot_id: store.spot,
      start_time: last?.start.slice(11, 16) || "07:00", end_time: last?.end.slice(11, 16) || "10:00",
      anglers: store.me, notes: "", counts: [{ species: "Perch", count: 0 }], top_fish: [],
    };
  }
  const editing = !!t.id;
  const state = {
    anglers: new Set((t.anglers || "").split(",").map((s) => s.trim()).filter(Boolean)),
    known: new Set([store.me, ...(await api("/anglers"))].filter(Boolean)),
    counts: t.counts.length ? t.counts.map((c) => ({ ...c })) : [{ species: "Perch", count: 0 }],
    top: t.top_fish.map((f) => ({ ...f })),
    species: [...new Set([...(await api("/species")), ...SPECIES])],
  };
  state.anglers.forEach((a) => state.known.add(a));

  view.innerHTML = `<h1>${editing ? "Edit trip" : "Log a trip"}</h1>
    <form id="trip">
    <div class="card">
      <label>Day</label><input type="date" name="date" value="${esc(t.date)}" max="${today()}" required>
      <label>Spot <a href="#spot" class="small" style="float:right">＋ new spot on map</a></label><select name="spot_id">${spotOptions(t.spot_id)}</select>
      <div class="grid2"><div><label>Fished from</label><input type="time" name="start_time" value="${esc(t.start_time)}" required></div>
        <div><label>to</label><input type="time" name="end_time" value="${esc(t.end_time)}" required></div></div>
      <label>Who fished?</label><div class="chips" id="ang"></div>
      <div class="row" style="margin-top:6px"><input id="newang" placeholder="Add name" class="grow"><button type="button" class="sm" id="addang">Add</button></div>
    </div>
    <div class="card">
      <div class="row spread"><h3>Fish caught</h3><span class="muted" id="total"></span></div>
      <div id="counts"></div>
      <button type="button" class="sm ghost" id="addsp" style="margin-top:8px">＋ Another species</button>
      <div class="muted small" style="margin-top:6px">Caught nothing? Save it anyway - blank days teach the model which conditions don't work.</div>
    </div>
    <div class="card">
      <h3>Top fish</h3><div class="muted small">Only the ones worth remembering - measure what you like.</div>
      <div id="topfish"></div>
      <button type="button" class="sm ghost" id="addtop" style="margin-top:8px">🏆 Add top fish</button>
    </div>
    <div class="card"><label style="margin-top:0">Notes</label>
      <textarea name="notes" placeholder="Water colour, bait fish seen, what worked…">${esc(t.notes || "")}</textarea></div>
    <button class="primary block">${editing ? "Save changes" : "Save trip"}</button>
    ${editing ? `<button type="button" class="danger block" id="deltrip">Delete trip</button>` : ""}
    </form>
    <datalist id="splist">${state.species.map((s) => `<option>${esc(s)}</option>`).join("")}</datalist>`;

  const renderAng = () => {
    $("#ang").innerHTML = [...state.known].map((n) => `<button type="button" class="chip ${state.anglers.has(n) ? "on" : ""}">${esc(n)}</button>`).join("");
    $$("#ang .chip").forEach((b) => (b.onclick = () => {
      state.anglers.has(b.textContent) ? state.anglers.delete(b.textContent) : state.anglers.add(b.textContent);
      renderAng(); renderTop();
    }));
  };
  const renderCounts = () => {
    $("#counts").innerHTML = state.counts.map((c, i) => `<div class="countrow" data-i="${i}">
      <input class="grow" list="splist" value="${esc(c.species)}" data-k="species" placeholder="Species">
      <button type="button" class="step" data-d="-1">−</button><input class="num" type="number" min="0" inputmode="numeric" value="${c.count}" data-k="count">
      <button type="button" class="step" data-d="1">＋</button>
      ${state.counts.length > 1 ? `<button type="button" class="x" aria-label="Remove">✕</button>` : ""}</div>`).join("");
    $$("#counts .countrow").forEach((row) => {
      const c = state.counts[+row.dataset.i];
      $('[data-k="species"]', row).oninput = (e) => { c.species = e.target.value; };
      $('[data-k="species"]', row).onchange = renderTop;
      $('[data-k="count"]', row).oninput = (e) => { c.count = Math.max(0, +e.target.value || 0); renderTotal(); };
      $$(".step", row).forEach((b) => (b.onclick = () => { c.count = Math.max(0, c.count + +b.dataset.d); renderCounts(); }));
      const x = $(".x", row);
      if (x) x.onclick = () => { state.counts.splice(+row.dataset.i, 1); renderCounts(); renderTop(); };
    });
    renderTotal();
  };
  const renderTotal = () => {
    const n = state.counts.reduce((a, c) => a + (c.count || 0), 0);
    $("#total").textContent = `${n} fish total`;
  };
  const renderTop = () => {
    const sp = [...new Set([...state.counts.map((c) => c.species).filter(Boolean), "Perch", "Pike"])];
    const anglers = [...state.anglers];
    $("#topfish").innerHTML = state.top.map((f, i) => `<div class="topfish" data-i="${i}">
      <div class="row spread"><b>🏆 #${i + 1}</b><button type="button" class="x" aria-label="Remove">✕</button></div>
      <div class="chips">${[...new Set([...sp, f.species])].map((s) => `<button type="button" class="chip ${f.species === s ? "on" : ""}" data-sp="${esc(s)}">${esc(s)}</button>`).join("")}</div>
      <div class="grid2"><div><label>Length (cm)</label><input type="number" step="0.5" inputmode="decimal" value="${f.length_cm ?? ""}" data-k="length_cm"></div>
        <div><label>Weight (g)</label><input type="number" step="10" inputmode="numeric" value="${f.weight_g ?? ""}" data-k="weight_g"></div></div>
      <div class="grid2"><div><label>Caught by</label><select data-k="angler"><option value=""></option>${[...new Set([...anglers, f.angler].filter(Boolean))].map((a) => `<option ${a === f.angler ? "selected" : ""}>${esc(a)}</option>`).join("")}</select></div>
        <div><label>Lure / bait</label><input value="${esc(f.lure || "")}" data-k="lure"></div></div>
      <label><input type="checkbox" data-k="released" ${f.released === 0 || f.released === false ? "" : "checked"}> Released</label></div>`).join("");
    $$("#topfish .topfish").forEach((card) => {
      const f = state.top[+card.dataset.i];
      $$(".chip", card).forEach((b) => (b.onclick = () => { f.species = b.dataset.sp; renderTop(); }));
      $$("[data-k]", card).forEach((inp) => (inp.oninput = inp.onchange = () => {
        const k = inp.dataset.k;
        f[k] = k === "released" ? inp.checked : k.endsWith("_cm") || k.endsWith("_g") ? numOrNull(inp.value) : inp.value;
      }));
      $(".x", card).onclick = () => { state.top.splice(+card.dataset.i, 1); renderTop(); };
    });
  };
  renderAng(); renderCounts(); renderTop();

  $("#addang").onclick = () => {
    const n = $("#newang").value.trim();
    if (n) { state.known.add(n); state.anglers.add(n); $("#newang").value = ""; renderAng(); renderTop(); }
  };
  $("#addsp").onclick = () => { state.counts.push({ species: "", count: 1 }); renderCounts(); $("#counts .countrow:last-child input").focus(); };
  $("#addtop").onclick = () => {
    const firstSp = state.counts.find((c) => c.species && c.count)?.species || "Perch";
    state.top.push({ species: firstSp, length_cm: null, weight_g: null, lure: "", angler: store.me && state.anglers.has(store.me) ? store.me : "", released: true });
    renderTop();
  };
  if (editing) $("#deltrip").onclick = async () => {
    if (!(await askConfirm("Delete this trip and its fish?"))) return;
    await api(`/trips/${t.id}`, { method: "DELETE" }); toast("Trip deleted"); refreshStatus(); location.hash = "#calendar";
  };
  $("#trip").onsubmit = async (e) => {
    e.preventDefault();
    const form = e.target;
    const body = {
      spot_id: +form.spot_id.value, date: form.date.value, start_time: form.start_time.value, end_time: form.end_time.value,
      anglers: [...state.anglers].join(", "), notes: form.notes.value,
      counts: state.counts.filter((c) => c.species.trim()), top_fish: state.top,
    };
    store.spot = body.spot_id;
    try {
      await api(editing ? `/trips/${t.id}` : "/trips", { method: editing ? "PUT" : "POST", body });
      toast(editing ? "Trip updated" : "Trip logged 🎣");
      refreshStatus(); lastForecast = null;
      calMonth = body.date.slice(0, 7);
      openDayAfterRoute = body.date;
      location.hash = "#calendar";
    } catch (err) { toast(err.message, true); }
  };
}

// ---------- Insights ----------
let insSpecies = "", insMin = "";
// The setting is stored as "prior hours": folklore counts as if you'd already fished that long in every condition.
// 100% = 40 hours, the default 8 hours = 20%.
const PRIOR_MAX = 40;
function priorText(hours) {
  if (hours <= 0) return "0% = only your own data. Expect jumpy numbers until you have many trips.";
  const h = Math.round(hours * 10) / 10;
  return `Folklore counts as much as ${h} hours of your own fishing in each condition. Once you've fished a condition for ${h} hours, ` +
    `your data and folklore weigh equally; after ${h * 4} hours your data counts 80%.`;
}
async function viewInsights() {
  const species = await api("/species");
  const d = await api(`/insights?species=${encodeURIComponent(insSpecies)}&min_length=${insMin || 0}`);
  const conf = Math.round(d.confidence * 100);
  view.innerHTML = `<h1>What makes them bite?</h1>
    <div class="card"><div class="grid2">
      <div><label>Species</label><select id="insp"><option value="">All species</option>${species.map((s) => `<option ${s === insSpecies ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></div>
      <div><label>Min. length (cm) - top fish only</label><input id="inmin" type="number" inputmode="numeric" value="${esc(insMin)}" placeholder="any"></div></div></div>
    <div class="card">
      <div class="row spread"><b>${d.catches} fish · ${d.effort.toFixed(1)} hours fished</b><span>${d.r0 ? d.r0.toFixed(2) + " fish/hour" : ""}</span></div>
      <div class="row spread muted small" style="margin-top:8px"><span>Learned from your data</span><span>${conf}%</span></div>
      <div class="meter"><i style="width:${conf}%"></i></div>
      ${d.skipped_hours ? `<div class="muted small" style="margin-top:6px">${d.skipped_hours} fished hours have no weather yet - use the status bar to backfill.</div>` : ""}
      <label>Trust in folklore: <b id="pv">${Math.round(d.prior_hours / PRIOR_MAX * 100)}%</b></label>
      <input type="range" id="prior" min="0" max="100" step="5" value="${Math.round(d.prior_hours / PRIOR_MAX * 100)}">
      <div class="muted small" id="pexp">${priorText(d.prior_hours)}</div>
    </div>
    <div class="muted small" style="margin:0 2px 10px">${GLOSS_LINK}<br>Bars show how much better (green, right) or worse (red, left) each condition is than your average hour.
      Faded bars are still mostly prior; solid bars are backed by your own logs. Features sorted by how much they matter.</div>
    ${d.features.map(featCard).join("")}
    <div class="card"><h3>How the score works</h3><div class="muted small">
      Each trip is cut into hours and the day's fish are spread over them by effort. For every condition bin the model compares your
      catch rate (fish per hour fished) with your overall rate, shrunk towards a mild folklore guess until you have enough hours there.
      A forecast hour multiplies the factors of the bins it falls in (damped, since weather factors overlap) and maps the result to 0-100,
      where 50 is an average hour for you. Shorter, precisely timed trips sharpen the time-of-day picture.</div>
      <a class="btn" href="/api/export.csv" style="margin-top:10px">⬇ Export trips + conditions (CSV)</a></div>`;
  $("#insp").onchange = (e) => { insSpecies = e.target.value; viewInsights(); };
  $("#inmin").onchange = (e) => { insMin = e.target.value; viewInsights(); };
  $("#prior").oninput = (e) => { $("#pv").textContent = e.target.value + "%"; $("#pexp").textContent = priorText(e.target.value / 100 * PRIOR_MAX); };
  $("#prior").onchange = async (e) => {
    await api("/settings", { method: "PUT", body: { prior_hours: e.target.value / 100 * PRIOR_MAX } }); lastForecast = null; viewInsights();
  };
}
function featCard(f) {
  const bins = Object.values(f.bins);
  return `<div class="card feat"><div class="row spread"><h3>${esc(f.label)}</h3><span class="imp">impact ${"●".repeat(Math.min(5, Math.ceil(f.importance * 8))) || "○"}</span></div>
    <div class="muted small" style="margin-bottom:6px">${esc(f.desc)}</div>
    ${bins.map((b) => {
      const x = Math.max(-2, Math.min(2, Math.log2(b.m))) / 2 * 50; // ±50% of track = ×4 / ÷4
      const left = x >= 0 ? 50 : 50 + x, width = Math.abs(x);
      return `<div class="bin"><span title="${esc(b.help)}">${esc(b.label)}</span><div class="track"><span class="mid"></span>
        <span class="bar" style="left:${left}%;width:${width}%;background:${b.m >= 1 ? "var(--good)" : "var(--bad)"};opacity:${0.3 + 0.7 * b.data_weight}"></span></div>
        <span class="small">${pct(b.m)}<br><span class="muted">${b.effort}h · ${b.catches}🐟</span></span></div>`;
    }).join("")}</div>`;
}

// ---------- Spots ----------
function viewSpots() {
  view.innerHTML = `<div class="row spread"><h1>Spots</h1><a class="btn primary sm" href="#spot">＋ Add on map</a></div>
    <div class="card">${spots.map((s) => `
    <div class="item row spread"><div><b>${esc(s.name)}</b><div class="muted small">${s.lat.toFixed(4)}, ${s.lon.toFixed(4)}</div>
      ${s.notes ? `<div class="muted small">${esc(s.notes)}</div>` : ""}</div>
      <a class="btn sm ghost" href="#spot?id=${s.id}">Edit</a></div>`).join("") || `<div class="empty">No spots yet.</div>`}</div>
    <div class="muted small" style="margin-top:12px">Each spot gets its own weather (~1 km grid). Inlets in the same bay can share one spot.</div>`;
}

let leaflet;
function loadLeaflet() {
  // Only loaded when the map is opened, so the rest of the app stays light.
  return (leaflet ||= new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet"; css.href = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css";
    const js = document.createElement("script");
    js.src = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js";
    js.onload = () => resolve(window.L);
    js.onerror = () => { leaflet = null; reject(new Error("Couldn't load the map library - are you online?")); };
    document.head.append(css, js);
  }));
}

// OpenStreetMap's standard style paints all water #aad3df. We read the already-loaded tiles
// into a canvas and look for the closest water-coloured pixel to the tap - no extra API needed.
const WATER_RGB = [170, 211, 223];
function snapToWater(map, latlng, maxPx = 220) {
  const box = map.getContainer().getBoundingClientRect();
  const canvas = document.createElement("canvas");
  canvas.width = box.width; canvas.height = box.height;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  for (const img of map.getContainer().querySelectorAll("img.leaflet-tile-loaded")) {
    const r = img.getBoundingClientRect();
    ctx.drawImage(img, r.left - box.left, r.top - box.top, r.width, r.height);
  }
  const p = map.latLngToContainerPoint(latlng);
  const x0 = Math.max(0, Math.round(p.x - maxPx)), y0 = Math.max(0, Math.round(p.y - maxPx));
  const w = Math.min(canvas.width, Math.round(p.x + maxPx)) - x0, h = Math.min(canvas.height, Math.round(p.y + maxPx)) - y0;
  if (w <= 0 || h <= 0) return null;
  const px = ctx.getImageData(x0, y0, w, h).data;
  let best = null, bestD = maxPx * maxPx;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = (y * w + x) * 4;
    if (Math.abs(px[i] - WATER_RGB[0]) + Math.abs(px[i + 1] - WATER_RGB[1]) + Math.abs(px[i + 2] - WATER_RGB[2]) > 18) continue;
    const d = (x0 + x - p.x) ** 2 + (y0 + y - p.y) ** 2;
    if (d < bestD) { bestD = d; best = [x0 + x, y0 + y]; }
  }
  return best && map.containerPointToLatLng(best);
}

let spotReturnTo = "#spots";
async function viewSpotMap(params) {
  const s = params.get("id") ? spots.find((x) => x.id == params.get("id")) || {} : {};
  view.innerHTML = `<h1>${s.id ? "Edit spot" : "New spot"}</h1>
    <div id="map" class="map"><div class="empty">Loading map…</div></div>
    <div class="row spread" style="margin:8px 0"><span class="muted small" id="snapmsg">Tap where you fish - it snaps to the nearest water.</span>
      <button type="button" class="sm ghost" id="locate">📍 Me</button></div>
    <form class="card" id="spotform">
      <div id="watername" style="margin-bottom:6px"></div>
      <label style="margin-top:0">Name</label><input name="name" value="${esc(s.name || "")}" required placeholder="e.g. Værebro Å outlet">
      <label>Notes</label><textarea name="notes" placeholder="Parking, where to stand, depth…">${esc(s.notes || "")}</textarea>
      <div class="muted small" id="coords" style="margin-top:8px"></div>
      <button class="primary block">Save spot</button>
      ${s.id ? `<button type="button" class="danger block" id="dels">Delete spot</button>` : ""}</form>`;

  let L;
  try { L = await loadLeaflet(); } catch (e) { $("#map").innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  $("#map").innerHTML = "";
  const others = spots.filter((x) => x.id !== s.id);
  const start = s.id ? [s.lat, s.lon] : others.length ? [others[0].lat, others[0].lon] : [55.62, 11.85];
  const map = L.map("map", { zoomControl: true }).setView(start, s.id ? 15 : others.length ? 12 : 9);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, crossOrigin: "anonymous", attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  others.forEach((o) => L.circleMarker([o.lat, o.lon], { radius: 6, color: "#8fb0b8", weight: 2, fillOpacity: 0.4 }).bindTooltip(o.name).addTo(map));

  let chosen = s.id ? L.latLng(s.lat, s.lon) : null, tapped = null;
  const pin = L.marker(chosen || start, { draggable: true });
  const tapDot = L.circleMarker(start, { radius: 4, color: "#f2a541", weight: 2 });
  const showCoords = () => ($("#coords").textContent = chosen ? `${chosen.lat.toFixed(5)}, ${chosen.lng.toFixed(5)}` : "No point chosen yet.");
  let suggested = "", lookup = 0;
  const nameWater = async (ll) => {
    // Ask OpenStreetMap what this water is called; prefill the name unless the user already typed one.
    const id = ++lookup, box = $("#watername"), nameInput = $("#spotform").name;
    box.textContent = "🌊 Looking up the water…";
    let w = {};
    try { w = await api(`/water-name?lat=${ll.lat.toFixed(5)}&lon=${ll.lng.toFixed(5)}`); } catch { }
    if (id !== lookup) return; // a newer tap won
    if (!w.name) { box.textContent = "🌊 Unnamed water"; return; }
    box.innerHTML = `🌊 <b>${esc(w.name)}</b> <span class="muted">${esc(w.kind || "")}${w.place ? " · " + esc(w.place) : ""}</span>`;
    const proposal = w.place && !w.name.includes(w.place) ? `${w.name} (${w.place})` : w.name;
    if (!nameInput.value.trim() || nameInput.value === suggested) nameInput.value = suggested = proposal;
  };
  const place = (ll) => { chosen = ll; pin.setLatLng(ll).addTo(map); showCoords(); nameWater(ll); };
  if (chosen) place(chosen); else showCoords();
  pin.on("dragend", () => { place(pin.getLatLng()); tapDot.remove(); $("#snapmsg").textContent = "Moved by hand."; });

  const snap = (ll) => {
    const w = snapToWater(map, ll);
    tapDot.setLatLng(ll).addTo(map);
    if (!w) { place(ll); $("#snapmsg").textContent = "No water near your tap - using the exact point."; return; }
    const m = Math.round(map.distance(ll, w));
    place(w);
    $("#snapmsg").innerHTML = m < 3 ? "On the water ✓" : `Snapped to water ${m} m from your tap · <a href="#" id="exact">use my tap</a>`;
    const ex = $("#exact");
    if (ex) ex.onclick = (e) => { e.preventDefault(); place(tapped); tapDot.remove(); $("#snapmsg").textContent = "Using your exact tap."; };
  };
  map.on("click", (e) => {
    tapped = e.latlng;
    if (map.getZoom() < 14) {
      // Too far out to see creeks - zoom in on the tap, then snap once the detailed tiles are drawn.
      $("#snapmsg").textContent = "Zooming in…";
      map.once("moveend", () => setTimeout(() => snap(tapped), 700));
      map.setView(tapped, 15);
    } else snap(tapped);
  });

  $("#locate").onclick = () => {
    if (!navigator.geolocation || !window.isSecureContext) { toast("Location needs the HTTPS address (see README)", true); return; }
    navigator.geolocation.getCurrentPosition((p) => map.setView([p.coords.latitude, p.coords.longitude], 16),
      (err) => toast(err.message, true), { enableHighAccuracy: true, timeout: 15000 });
  };
  if (s.id) $("#dels").onclick = async () => {
    if (!(await askConfirm("Delete this spot?"))) return;
    try { await api(`/spots/${s.id}`, { method: "DELETE" }); await loadSpots(); location.hash = "#spots"; } catch (e) { toast(e.message, true); }
  };
  $("#spotform").onsubmit = async (e) => {
    e.preventDefault();
    if (!chosen) { toast("Tap the map to place the spot first", true); return; }
    const f = e.target;
    const body = { name: f.name.value.trim(), notes: f.notes.value, lat: +chosen.lat.toFixed(6), lon: +chosen.lng.toFixed(6) };
    const res = await api(s.id ? `/spots/${s.id}` : "/spots", { method: s.id ? "PUT" : "POST", body });
    if (!s.id) store.spot = res.id;
    await loadSpots(); lastForecast = null; toast("Spot saved");
    location.hash = spotReturnTo;
  };
}

// ---------- Feedback ----------
let feedbackFrom = "";
async function viewFeedback() {
  const items = await api("/feedback");
  view.innerHTML = `<h1>Feedback</h1>
    <form class="card" id="fbform">
      <label style="margin-top:0">Something broken, confusing or missing? Ideas welcome.</label>
      <textarea name="text" required rows="4" placeholder="e.g. The calendar should show the spot name…"></textarea>
      <div class="muted small" style="margin-top:6px">Sent as ${esc(store.me)}</div>
      <button class="primary block">Send feedback</button></form>
    <h2>All feedback (${items.length})</h2>
    <div class="card">${items.map((f) => `<div class="item" style="${f.done ? "opacity:.5" : ""}">
      <div class="row spread"><span class="muted small">${esc(f.author || "Anonymous")} · ${esc(f.created_at.replace("T", " "))}${f.page ? " · from " + esc(f.page) : ""}</span>
        <span class="row" style="gap:4px"><button type="button" class="sm ghost" data-done="${f.id}" data-v="${f.done ? 0 : 1}">${f.done ? "Reopen" : "✓ Done"}</button>
        <button type="button" class="x" data-del="${f.id}" aria-label="Delete">✕</button></span></div>
      <div style="white-space:pre-wrap;margin-top:4px">${esc(f.text)}</div></div>`).join("") || `<div class="empty">No feedback yet.</div>`}</div>`;
  $("#fbform").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/feedback", { method: "POST", body: { text: f.text.value, page: feedbackFrom } });
      toast("Thanks - feedback saved"); viewFeedback();
    } catch (err) { toast(err.message, true); }
  };
  $$("[data-done]").forEach((b) => (b.onclick = async () => { await api(`/feedback/${b.dataset.done}`, { method: "PUT", body: { done: b.dataset.v === "1" } }); viewFeedback(); }));
  $$("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!(await askConfirm("Delete this feedback?"))) return;
    await api(`/feedback/${b.dataset.del}`, { method: "DELETE" }); viewFeedback();
  }));
}

// ---------- Login & account ----------
const pinInput = (name, auto = "current-password") =>
  `<input name="${name}" type="password" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" autocomplete="${auto}" required placeholder="••••" class="pin">`;
function showLogin() {
  if (document.body.classList.contains("loggedout")) return;
  user = null;
  document.body.classList.add("loggedout");
  toggleMenu(false); closeModal();
  let last = "";
  try { last = localStorage.getItem("lastUser") || ""; } catch {}
  view.innerHTML = `<h1>Log in</h1>
    <form class="card" id="loginform">
      <label style="margin-top:0">Username</label><input name="name" autocomplete="username" autocapitalize="off" required value="${esc(last)}">
      <label>PIN</label>${pinInput("pin")}
      <button class="primary block" style="margin-top:14px">Log in</button>
      <div class="muted small" style="margin-top:8px">No account? Ask whoever runs Fishalyzer to add you.</div></form>`;
  const f = $("#loginform");
  (last ? f.pin : f.name).focus();
  let setup = false;
  api("/auth/setup").then((st) => {
    if (!st.needed) return;
    setup = st.allowed;
    $("h1", view).textContent = st.allowed ? "Create the admin account" : "Not set up yet";
    $("#loginform .muted").textContent = st.allowed
      ? "First start: no accounts exist yet. This creates the admin, who can then add everyone else."
      : "No accounts exist yet. Open Fishalyzer from the home network or over Tailscale to create the admin account.";
    $("#loginform button").textContent = "Create admin";
    $("#loginform button").disabled = !st.allowed;
  }).catch(() => {});
  f.onsubmit = async (e) => {
    e.preventDefault();
    try {
      user = await api(setup ? "/auth/setup" : "/auth/login", { method: "POST", body: { name: f.name.value, pin: f.pin.value } });
      try { localStorage.setItem("lastUser", user.name); } catch {}
      document.body.classList.remove("loggedout");
      start();
    } catch (err) { f.pin.value = ""; toast(err.message, true); }
  };
}

async function viewAccount() {
  view.innerHTML = `<h1>Account</h1>
    <div class="card row spread"><div><b>${esc(user.name)}</b>${user.admin ? ` <span class="chip on" style="cursor:default">admin</span>` : ""}
      <div class="muted small">You stay logged in on this device until you log out.</div></div>
      <button class="sm ghost" id="logout">Log out</button></div>
    <h2>Change PIN</h2>
    <form class="card" id="pinform">
      <div class="grid2"><div><label style="margin-top:0">Current PIN</label>${pinInput("old_pin")}</div>
        <div><label style="margin-top:0">New PIN</label>${pinInput("new_pin", "new-password")}</div></div>
      <button class="primary block" style="margin-top:12px">Change PIN</button>
      <div class="muted small" style="margin-top:6px">Logs you out on your other devices.</div></form>
    ${user.admin ? `<a class="btn block" href="#admin" style="margin-top:12px">Admin: manage accounts →</a>` : ""}`;
  $("#logout").onclick = async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => {});
    showLogin();
  };
  $("#pinform").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/auth/pin", { method: "PUT", body: { old_pin: f.old_pin.value, new_pin: f.new_pin.value } });
      f.reset(); toast("PIN changed");
    } catch (err) { toast(err.message, true); }
  };
}

async function viewAdmin() {
  if (!user.admin) { view.innerHTML = `<div class="empty">Only admins can manage accounts.</div>`; return; }
  view.innerHTML = `<h1>Admin</h1><div id="users"><div class="empty">Loading…</div></div>`;
  await renderUsers();
}

async function renderUsers() {
  const users = await api("/users");
  const locked = (u) => u.locked_until && u.locked_until > localNow();
  $("#users").innerHTML = `
    <form class="card" id="userform"><h3>Create account</h3>
      <div class="grid2"><div><label>Username</label><input name="name" required autocapitalize="off" autocomplete="off"></div>
        <div><label>PIN (4 digits)</label><input name="pin" inputmode="numeric" pattern="[0-9]{4}" maxlength="4" required autocomplete="off"></div></div>
      <label class="row" style="gap:6px"><input type="checkbox" name="admin" style="width:auto"> Admin (can manage accounts)</label>
      <button class="primary block" style="margin-top:10px">Create account</button>
      <div class="muted small" style="margin-top:6px">Tell them their username and PIN in person; they can change the PIN under Account.</div></form>
    <h2>Accounts (${users.length})</h2>
    <div class="card">${users.map((u) => `<div class="item row spread">
      <div><b>${esc(u.name)}</b>${u.admin ? ` <span class="muted small">admin</span>` : ""}
        <div class="muted small">${u.last_seen ? "last seen " + esc(u.last_seen.slice(0, 16).replace("T", " ")) : "never logged in"}${locked(u) ? ` · <b style="color:var(--bad)">locked until ${esc(u.locked_until.slice(11, 16))}</b>` : u.failed ? ` · ${u.failed} wrong PIN${u.failed > 1 ? "s" : ""}` : ""}</div></div>
      <span class="row" style="gap:4px"><button type="button" class="sm ghost" data-pin="${u.id}" data-name="${esc(u.name)}">${locked(u) ? "Unlock / new PIN" : "New PIN"}</button>
        ${u.id === user.id ? "" : `<button type="button" class="x" data-deluser="${u.id}" data-name="${esc(u.name)}" aria-label="Delete">✕</button>`}</span></div>`).join("")}</div>`;
  $("#userform").onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    try {
      await api("/users", { method: "POST", body: { name: f.name.value, pin: f.pin.value, admin: f.admin.checked } });
      toast(`Created ${f.name.value}`); renderUsers();
    } catch (err) { toast(err.message, true); }
  };
  $$("[data-pin]").forEach((b) => (b.onclick = async () => {
    const pin = await askText(`New 4-digit PIN for ${b.dataset.name}`);
    if (pin == null) return;
    try { await api(`/users/${b.dataset.pin}`, { method: "PUT", body: { pin: pin.trim() } }); toast(`New PIN set for ${b.dataset.name}`); renderUsers(); }
    catch (err) { toast(err.message, true); }
  }));
  $$("[data-deluser]").forEach((b) => (b.onclick = async () => {
    if (!(await askConfirm(`Delete ${b.dataset.name}? Their logged trips stay.`))) return;
    await api(`/users/${b.dataset.deluser}`, { method: "DELETE" }); renderUsers();
  }));
}

// ---------- router ----------
function toggleMenu(open = $("#menu").hidden) {
  $("#menu").hidden = !open;
  $("#burger").setAttribute("aria-expanded", open);
}
$("#burger").onclick = () => toggleMenu();
$("#menu").onclick = (e) => { if (e.target.id === "menu") toggleMenu(false); };
const routes = { today: viewToday, calendar: viewCalendar, log: viewLog, insights: viewInsights, spots: viewSpots, spot: viewSpotMap, feedback: viewFeedback, account: viewAccount, admin: viewAdmin };
let currentHash = "#today";
async function route() {
  if (!user) return; // the login screen stays until login succeeds
  const [name = "today", query = ""] = location.hash.slice(1).split("?");
  const page = name || "today";
  if (page === "spot" && !currentHash.startsWith("#spot")) spotReturnTo = currentHash;
  if (page === "feedback" && !currentHash.startsWith("#feedback")) feedbackFrom = currentHash.slice(1).split("?")[0];
  currentHash = location.hash || "#today";
  $$("#menu a").forEach((a) => a.classList.toggle("active", a.dataset.tab === page));
  $(".logbtn").classList.toggle("active", page === "log");
  toggleMenu(false);
  closeModal();
  try { await (routes[page] || viewToday)(new URLSearchParams(query)); } catch (e) { if (user) view.innerHTML = `<div class="empty">Something went wrong: ${esc(e.message)}</div>`; }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
let started = false;
async function start() {
  $("#menu [data-tab=admin]").hidden = !user.admin;
  try { await loadSpots(); } catch {}
  try { glossary = await api("/glossary"); } catch {}
  route();
  refreshStatus();
  if (!started) setInterval(() => user && refreshStatus(), 60000);
  started = true;
}
(async () => {
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  try { user = await api("/auth/me"); } catch (e) {
    // a 401 has already shown the login screen; anything else means the server can't be reached
    if (!document.body.classList.contains("loggedout")) view.innerHTML = `<div class="empty">Can't reach the server: ${esc(e.message)}</div>`;
    return;
  }
  start();
})();
