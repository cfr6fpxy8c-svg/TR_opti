/* Crypto 15M Signal Scanner — dashboard logic (vanilla JS, no dependencies).
 *
 * Data flow:
 *  - /data/*.json files are refreshed every 60 s (produced by the Python engine
 *    every 15 min via GitHub Actions). The "last data update" label always shows
 *    the browser refresh time of those files.
 *  - Live prices are fetched directly from Binance's public ticker every 20 s.
 *    A price is labelled "LIVE" ONLY when that browser fetch succeeded; if it
 *    fails we fall back to the scan-time price and label it "SCAN".
 *  - Performance metrics for the selected date range are recomputed client-side
 *    from signals.json with the same definitions as the Python engine
 *    (win rate = wins / (wins + losses); ambiguous/expired excluded).
 */
"use strict";

const DATA_INTERVAL_MS = 60_000;
const PRICE_INTERVAL_MS = 20_000;
const PRICE_ENDPOINTS = [
  "https://fapi.binance.com/fapi/v1/ticker/price",
  "https://www.binance.com/fapi/v1/ticker/price",
];

const TZ = "Europe/Paris";
const DEFAULT_SCAN_MINUTES_UTC = [0, 15, 30, 45];
const SCAN_GRACE_MS = 5 * 60_000;
const QUALITIES = ["A+", "A", "B+", "B", "C"];
const $ = (id) => document.getElementById(id);
// JSON/API fields are untrusted whenever they are inserted into HTML.
const escapeHtml = (value) => String(value ?? "").replace(/[&<>\"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Quoting alone does not stop spreadsheet formulas in exported data.
const csvCell = (value) => {
  let text = String(value ?? "");
  if (/^[\s\uFEFF]*[=+@-]/u.test(text) || /^[\t\r\n]/u.test(text)) text = "'" + text;
  return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
};
const fmtTime = (ms) => ms ? new Date(ms).toLocaleString("fr-FR", {
  timeZone: TZ, hour12: false, day: "2-digit", month: "short",
  hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—";
const fmtClock = (ms) => ms ? new Date(ms).toLocaleTimeString("fr-FR", {
  timeZone: TZ, hour12: false }) : "—";
const fmtPrice = (v) => v == null ? "—" : Number(v).toLocaleString("fr-FR", { maximumFractionDigits: 2 });
const fmtAgo = (ms) => {
  if (!ms) return "—";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 90) return `il y a ${Math.round(s)} s`;
  if (s < 5400) return `il y a ${Math.round(s / 60)} min`;
  return `il y a ${(s / 3600).toFixed(1)} h`;
};
const fmtDur = (ms) => {
  if (ms == null) return "—";
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
};

const state = {
  signals: [], status: null, snapshots: [], performance: null, screener: null, latestChart: null,
  trend: null,
  lastDataUpdate: null, embeddedLoaded: false,
  livePrices: {}, liveOk: false, liveAt: null,
  activeFilter: new Set(QUALITIES),
  perfRange: 7, histRange: 0, histDir: "all", histQuality: "all",
  histOutcome: "all", histSearch: "",
  screenerSearch: "", screenerSort: { key: "rank", dir: 1 },
};

/* ------------------------------------------------------------------ data */
// Data is read from the repo so the static host need not redeploy on every scan commit.
const DATA_SOURCES = [document.querySelector('meta[name="data-source"]')?.content, "data/"]
  .filter(Boolean);

async function loadJSON(name) {
  let lastErr;
  for (const base of DATA_SOURCES) {
    try {
      const res = await fetch(`${base}${name}?t=${Date.now()}`, { cache: "no-store" });
      if (res.ok) return await res.json();
      lastErr = new Error(`${name}: HTTP ${res.status}`);
    } catch (err) { lastErr = err; }
  }
  throw lastErr;
}

async function refreshData() {
  try {
    const [signals, status, snapshots, performance, screener, latestChart, trend] = await Promise.all([
      loadJSON("signals.json"), loadJSON("system-status.json"),
      loadJSON("market-snapshots.json").catch(() => []),
      loadJSON("performance.json").catch(() => null),
      loadJSON("universe-snapshot.json").catch(() => null),
      loadJSON("latest-chart.json").catch(() => null),
      loadJSON("trend4h-paper.json").catch(() => null),
    ]);
    state.signals = Array.isArray(signals?.signals) ? signals.signals : [];
    state.status = status || null;
    state.snapshots = Array.isArray(snapshots) ? snapshots : [];
    state.performance = performance;
    state.screener = screener;
    state.latestChart = latestChart;
    state.trend = trend;
    state.lastDataUpdate = Date.now();
    state.embeddedLoaded = false;
    $("preview-notice").classList.add("hidden");
    renderAll();
  } catch (err) {
    // No reachable data files (offline file:// open, sandboxed preview, blocked
    // network): fall back to the snapshot embedded at build time, if present.
    if (window.__EMBEDDED_DATA__ && !state.embeddedLoaded) {
      const d = window.__EMBEDDED_DATA__;
      state.signals = Array.isArray(d.signals?.signals) ? d.signals.signals : [];
      state.status = d.status || null;
      state.snapshots = Array.isArray(d.snapshots) ? d.snapshots : [];
      state.performance = d.performance || null;
      state.screener = d.screener || null;
      state.latestChart = d.chart || null;
      state.trend = d.trend || null;
      state.lastDataUpdate = d.builtAt || null;
      state.embeddedLoaded = true;
      renderAll();
    }
    console.warn("data refresh failed:", err);
    $("preview-notice").classList.remove("hidden");
  }
}

/* live prices from the browser (Binance public ticker, CORS-enabled) */
async function refreshPrices() {
  const symbols = [...new Set(["BTCUSDT", ...state.signals
    .filter(s => s.status === "WAITING_TRIGGER" || s.status === "TRIGGERED")
    .map(s => s.symbol)])];
  for (const base of PRICE_ENDPOINTS) {
    try {
      const qs = encodeURIComponent(JSON.stringify(symbols.slice(0, 40)));
      const res = await fetch(`${base}?symbols=${qs}`, { cache: "no-store" });
      if (!res.ok) continue;
      const rows = await res.json();
      if (!Array.isArray(rows)) continue;
      rows.forEach(r => { state.livePrices[r.symbol] = parseFloat(r.price); });
      state.liveOk = true;
      state.liveAt = Date.now();
      renderTopChips(); renderActive(); renderRecent(); renderLatestAnalysis();
      return;
    } catch (_) { /* try next endpoint */ }
  }
  state.liveOk = false;
  renderTopChips(); renderActive(); renderRecent(); renderLatestAnalysis();
}

/* ------------------------------------------------------------------ render */
function renderAll() {
  renderTopChips();
  renderStatus();
  renderLatestAnalysis();
  renderOverview();
  renderScreener();
  renderActive();
  renderRecent();
  renderFunnel();
  renderPerformance();
  renderHistory();
  renderLogs();
  renderTrend();
  $("tab-signal-count").textContent = state.signals.length;
  $("ft-version").textContent = state.status?.version ? `engine v${state.status.version}` : "";
}

/* Health as reported by the last scan is a statement about THAT scan, not
 * about now. If the scheduler stopped (GitHub cron skips runs, a workflow got
 * disabled, the repo went quiet) the last scan can say HEALTHY forever while
 * the page shows hours-old prices. Age is measured in the browser against
 * wall-clock time and always overrides the stored health. */
const STALE_AFTER_MS = 45 * 60_000;    // 3 missed 15-minute scans
const OFFLINE_AFTER_MS = 3 * 3600_000; // no successful scan for 3 hours

function nextScanSlot(afterMs, minutes = state.status?.scanScheduleUtcMinutes || DEFAULT_SCAN_MINUTES_UTC) {
  if (!Number.isFinite(afterMs) || !Array.isArray(minutes) || !minutes.length) return null;
  const hourMs = 60 * 60_000;
  const hourStart = Math.floor(afterMs / hourMs) * hourMs;
  let slots = [...new Set(minutes)].filter(m => Number.isInteger(m) && m >= 0 && m < 60).sort((a, b) => a - b);
  if (!slots.length) slots = DEFAULT_SCAN_MINUTES_UTC;
  for (let offset = 0; offset <= 1; offset++) {
    for (const minute of slots) {
      const candidate = hourStart + offset * hourMs + minute * 60_000;
      if (candidate > afterMs) return candidate;
    }
  }
  return null;
}

function scheduleState(now = Date.now()) {
  const st = state.status;
  const last = st?.lastScan?.executedAt ?? st?.lastSuccessfulScan;
  const next = nextScanSlot(now);
  const missedAt = last ? nextScanSlot(last) : null;
  const overdueMs = missedAt && now > missedAt ? now - missedAt : 0;
  return { next, missedAt, overdueMs, late: overdueMs > SCAN_GRACE_MS };
}

function dataAgeMs() {
  const st = state.status;
  const last = st?.lastScan?.executedAt ?? st?.lastSuccessfulScan;
  return last ? Math.max(0, Date.now() - last) : null;
}

/** Effective health = stored health downgraded by how old the data actually is. */
function effectiveHealth() {
  const stored = state.status?.health ?? "UNKNOWN";
  const age = dataAgeMs();
  const schedule = scheduleState();
  if (age == null) return { level: stored, age, stale: false };
  if (age >= OFFLINE_AFTER_MS) return { level: "OFFLINE", age, stale: true };
  if (schedule.late || age >= STALE_AFTER_MS) return { level: "STALE", age, stale: true };
  return { level: stored, age, stale: false };
}

function renderScheduleStatus() {
  const target = $("st-nextscan");
  if (!target) return;
  const schedule = scheduleState();
  target.textContent = schedule.next ? fmtClock(schedule.next) : "—";
  const subtitle = $("st-nextscan-sub");
  if (!subtitle) return;
  subtitle.textContent = schedule.late
    ? `créneau ${fmtClock(schedule.missedAt)} dépassé de ${fmtDur(schedule.overdueMs)}`
    : schedule.next ? `prochain créneau théorique · dans ${fmtDur(schedule.next - Date.now())}`
      : "Cadence visée : 15 min";
}

function renderTopChips() {
  const st = state.status;
  renderScheduleStatus();
  const { level: health, age, stale } = effectiveHealth();
  const chip = $("chip-health");
  chip.textContent =
    health === "HEALTHY" ? "SCANNER EN LIGNE" :
    health === "DEGRADED" ? "DÉGRADÉ" :
    health === "STALE" ? "DONNÉES EN RETARD" :
    health === "OFFLINE" ? "SCANNER HORS LIGNE" :
    health === "FAILED" ? "SYSTÈME HORS LIGNE" : "…";
  chip.className = "chip " + (health === "HEALTHY" ? "chip-ok" :
    (health === "DEGRADED" || health === "STALE") ? "chip-warn" : "chip-bad");
  const last = st?.lastScan?.executedAt;
  // Always show how old the data is, not just when the scan happened -- a
  // bare timestamp reads as "current" to anyone who does not check the date.
  $("chip-lastscan").textContent = last
    ? `scan ${fmtClock(last)} · ${fmtAgo(last)}`
    : "scan —";
  const next = st?.nextExpectedScanAt;
  if ($("chip-nextscan")) $("chip-nextscan").textContent = next ? `prochain ~${fmtClock(next)}` : "prochain —";
  const pm = $("chip-price-mode");
  if (state.liveOk) {
    pm.textContent = `BTC en direct · ${fmtClock(state.liveAt)}`;
    pm.className = "chip chip-ok";
  } else {
    pm.textContent = "cours du scan";
    pm.className = "chip";
  }
  $("data-updated").textContent = state.lastDataUpdate
    ? `Données actualisées ${fmtTime(state.lastDataUpdate)}${state.embeddedLoaded ? " (copie locale)" : ""}`
    : "Données actualisées —";

  const notice = $("health-notice");
  const ageTxt = age != null ? fmtDur(age) : "unknown";
  if (health === "OFFLINE") {
    notice.textContent = `Aucun scan réussi depuis ${ageTxt}. Les éléments ci-dessous sont historiques, pas les cours actuels.`;
    notice.classList.remove("hidden");
  } else if (health === "STALE") {
    const schedule = scheduleState();
    notice.textContent = schedule.late
      ? `Créneau de scan ${fmtClock(schedule.missedAt)} dépassé de ${fmtDur(schedule.overdueMs)}. Dernier scan il y a ${ageTxt} ; prochain créneau théorique : ${fmtClock(schedule.next)}.`
      : `Données âgées de ${ageTxt}. Le scan prévu toutes les 15 min a été retardé ou manqué.`;
    notice.classList.remove("hidden");
  } else if (health === "FAILED") {
    notice.textContent = "Le dernier scan a échoué. Affichage du dernier scan réussi ; aucune donnée simulée n’est affichée.";
    notice.classList.remove("hidden");
  } else if (health === "DEGRADED") {
    notice.textContent = "Le dernier scan est terminé avec des données dégradées ou retardées.";
    notice.classList.remove("hidden");
  } else notice.classList.add("hidden");
  document.body.classList.toggle("data-stale", stale);
}

function renderStatus() {
  const st = state.status; if (!st) return;
  const ls = st.lastScan || {};
  const eff = effectiveHealth();
  const healthLabel = { HEALTHY: "EN LIGNE", DEGRADED: "DÉGRADÉ", STALE: "EN RETARD", OFFLINE: "HORS LIGNE", FAILED: "ÉCHEC" };
  $("st-system").textContent = healthLabel[eff.level] || eff.level;
  $("st-health-sub").textContent = eff.stale
    ? `données âgées de ${fmtDur(eff.age)}`
    : (ls.status ? `dernier résultat : ${ls.status === "OK" ? "réussi" : ls.status}` : "—");
  $("st-lastscan").textContent = ls.executedAt
    ? `${fmtClock(ls.executedAt)} · ${fmtAgo(ls.executedAt)}` : "—";
  const triggerNames = { schedule: "par cron", workflow_dispatch: "manuellement", push: "par push", local: "en local" };
  $("st-jitter").textContent = ls.trigger === "schedule" && ls.jitterSeconds != null
    ? `créneau ${fmtClock(ls.scheduledAt)} · retard ${Math.max(0, Math.round(ls.jitterSeconds))} s`
    : ls.trigger ? `déclenché ${triggerNames[ls.trigger] || ls.trigger}`
      : ls.jitterSeconds != null ? `horaire enregistré ${fmtClock(ls.scheduledAt)} · décalage ${Math.round(ls.jitterSeconds)} s`
        : "—";
  renderScheduleStatus();
  $("st-duration").textContent = ls.durationMs != null ? (ls.durationMs / 1000).toFixed(1) + "s" : "—";
  $("st-symbols").textContent = `${ls.symbolsValid ?? 0}/${ls.symbolsScanned ?? 0} valides · ${ls.dataFailures ?? 0} échec(s)`;
  $("st-universe").textContent = `${ls.universeSize ?? st.universeSize ?? "—"} paire(s)`;
  $("st-universe-src").textContent = ls.universeSource ? `source : ${ls.universeSource}` : "—";
  const api = ls.apiHealth ?? "—";
  $("st-api").textContent = api;
  $("st-api-reqs").textContent = ls.apiStats ? `${ls.apiStats.requests ?? 0} requête(s) · ${ls.apiStats.errors ?? 0} erreur(s) · ${ls.apiStats.retries ?? 0} reprise(s)` : "—";
  const fresh = ls.dataFreshness ?? {};
  const newestClose = fresh.newestCandleCloseMs;
  const candleAgeMs = newestClose
    ? Math.max(0, Date.now() - newestClose)
    : fresh.ageSeconds != null ? fresh.ageSeconds * 1000 + (dataAgeMs() || 0) : null;
  const freshLabel = candleAgeMs == null ? (fresh.label || "—")
    : candleAgeMs <= 16 * 60_000 ? "LIVE"
      : candleAgeMs <= 30 * 60_000 ? "DELAYED" : "STALE";
  $("st-fresh").textContent = ({ LIVE: "À JOUR", DELAYED: "RETARDÉES", STALE: "PÉRIMÉES" })[freshLabel] || freshLabel;
  $("st-fresh-age").textContent = candleAgeMs != null ? `bougie âgée de ${fmtDur(candleAgeMs)}` : "—";
  const active = state.signals.filter(s => s.status === "WAITING_TRIGGER" || s.status === "TRIGGERED");
  $("st-active").textContent = active.length;
  $("st-active-sub").textContent = `${active.filter(s => s.status === "TRIGGERED").length} déclenché(s) · ${active.filter(s => s.status === "WAITING_TRIGGER").length} en attente`;
}

function renderLatestAnalysis() {
  const chart = state.latestChart || {};
  const context = chart.context || {};
  const indicators = chart.indicators || {};
  const active = state.signals
    .filter(s => s.symbol === "BTCUSDT" && (s.status === "WAITING_TRIGGER" || s.status === "TRIGGERED"))
    .sort((a, b) => b.generatedAt - a.generatedAt)[0] || null;
  const signal = active;
  const score = signal?.score == null ? null : Math.max(0, Math.min(100, Number(signal.score)));

  $("setup-state").textContent = signal
    ? `${signal.direction} · ${signal.status === "TRIGGERED" ? "DÉCLENCHÉ" : "EN ATTENTE"}`
    : "AUCUN SETUP";
  $("setup-state").className = `setup-state ${signal ? signal.direction.toLowerCase() : "idle"}`;
  $("latest-score").textContent = score == null ? "—" : `${score.toFixed(0)} / 100`;
  $("latest-tier").textContent = signal?.quality || "—";
  $("latest-score-fill").style.width = `${score ?? 0}%`;
  $("latest-score-fill").className = signal ? signal.direction.toLowerCase() : "";
  $("latest-entry").textContent = signal ? fmtPrice(signal.entryPrice ?? signal.triggerPrice) : "—";
  const zone = signal?.entryZone;
  $("latest-entry-zone").textContent = zone?.length === 2
    ? `Zone : ${fmtPrice(zone[0])} – ${fmtPrice(zone[1])}` : "Zone d’entrée —";
  $("latest-tp").textContent = signal ? fmtPrice(signal.takeProfit) : "—";
  $("latest-sl").textContent = signal ? fmtPrice(signal.stopLoss) : "—";
  $("latest-rr").textContent = signal?.riskReward != null ? `1 : ${Number(signal.riskReward).toFixed(2)}` : "—";
  $("setup-explanation").textContent = signal
    ? `Setup retenu · score de confluence ${score.toFixed(0)}/100 · suivi automatique du déclenchement et de l’issue.`
    : "Aucun setup BTC n’a validé les règles lors du dernier scan. Les niveaux restent volontairement vides.";

  const lastCandle = chart.lastClosedAt || chart.generatedAt;
  $("chart-updated").textContent = lastCandle
    ? `Bougie analysée · ${fmtTime(lastCandle)}` : "En attente du prochain scan";
  const livePrice = state.liveOk ? state.livePrices.BTCUSDT : null;
  $("chart-price-label").textContent = livePrice != null ? "Cours en direct" : "Clôture analysée";
  $("chart-price").textContent = fmtPrice(livePrice ?? chart.price);
  $("chart-source").textContent = chart.source ? `Données du scan · ${chart.source}` : "Données du scan";

  const m15 = indicators["15m"] || {};
  $("metric-rsi").textContent = m15.rsi == null ? "—" : Number(m15.rsi).toFixed(1);
  $("metric-adx").textContent = m15.adx == null ? "—" : Number(m15.adx).toFixed(1);
  $("metric-atr").textContent = m15.atrPercent == null ? "—" : `${Number(m15.atrPercent).toFixed(2)} %`;
  $("metric-relvol").textContent = m15.relativeVolume == null ? "—" : `${Number(m15.relativeVolume).toFixed(2)}×`;
  $("metric-regime").textContent = String(context.regime || "—").replaceAll("_", " ");
  $("metric-bias").textContent = `${String(context.bias4h || "—").replaceAll("_", " ")} / ${String(context.bias1h || "—").replaceAll("_", " ")}`;

  const tfNames = { "4h": "4 h", "1h": "1 h", "15m": "15 min" };
  $("latest-indicators-body").innerHTML = ["4h", "1h", "15m"].map(tf => {
    const m = indicators[tf] || {};
    const cell = (value, digits = 2) => value == null ? "—" : Number(value).toLocaleString("fr-FR", { maximumFractionDigits: digits });
    return `<tr><td>${tfNames[tf]}</td><td>${cell(m.ema20)}</td><td>${cell(m.ema50)}</td><td>${cell(m.ema200)}</td>
      <td>${cell(m.rsi, 1)}</td><td>${cell(m.adx, 1)}</td><td>${cell(m.plusDi, 1)} / ${cell(m.minusDi, 1)}</td>
      <td>${cell(m.atr)}</td><td>${cell(m.atrPercent, 3)}%</td><td>${cell(m.relativeVolume, 2)}×</td><td>${cell(m.vwap)}</td></tr>`;
  }).join("");
  const structure = chart.structure || {};
  const structureNames = { events: "BOS/CHoCH", sweeps: "sweeps", displacements: "déplacements", fvgs: "FVG", orderBlocks: "OB" };
  $("latest-structure").textContent = ["4h", "1h", "15m"].map(tf => {
    const row = structure[tf] || {};
    const counts = Object.keys(structureNames).map(key => `${structureNames[key]} : ${(row[key] || []).length}`);
    return `${tfNames[tf]} — ${counts.join(" · ")}`;
  }).join("\n");

  drawBtcCandles(chart, signal, livePrice);
}

function drawBtcCandles(chart, signal, livePrice) {
  const svg = $("btc-candle-chart");
  const empty = $("chart-empty");
  svg.replaceChildren();
  const candles = (chart.candles || []).slice(-80);
  empty.classList.toggle("hidden", candles.length > 0);
  if (!candles.length) return;

  const W = 900, H = 340, left = 76, right = 82, top = 18, bottom = 34;
  const plotW = W - left - right, plotH = H - top - bottom;
  const minLow = Math.min(...candles.map(c => Number(c.low)));
  const maxHigh = Math.max(...candles.map(c => Number(c.high)));
  const pad = Math.max((maxHigh - minLow) * 0.08, maxHigh * 0.0002);
  const min = minLow - pad, max = maxHigh + pad;
  const y = value => top + ((max - value) / (max - min)) * plotH;
  const slot = plotW / candles.length;
  const x = index => left + (index + 0.5) * slot;

  for (let i = 0; i <= 4; i++) {
    const value = max - (i / 4) * (max - min), yy = y(value);
    svg.appendChild(svgEl("line", { x1: left, x2: W - right, y1: yy, y2: yy, class: "chart-grid-line" }));
    const label = svgEl("text", { x: left - 9, y: yy + 4, "text-anchor": "end", class: "chart-axis-label" });
    label.textContent = fmtPrice(value);
    svg.appendChild(label);
  }

  candles.forEach((candle, index) => {
    const xx = x(index), openY = y(Number(candle.open)), closeY = y(Number(candle.close));
    const up = Number(candle.close) >= Number(candle.open);
    const cls = up ? "candle-up" : "candle-down";
    svg.appendChild(svgEl("line", { x1: xx, x2: xx, y1: y(Number(candle.high)), y2: y(Number(candle.low)), class: cls }));
    const bodyTop = Math.min(openY, closeY), bodyHeight = Math.max(1.3, Math.abs(closeY - openY));
    svg.appendChild(svgEl("rect", { x: xx - Math.max(1.4, slot * 0.31), y: bodyTop,
      width: Math.max(2.8, slot * 0.62), height: bodyHeight, rx: 0.8, class: cls }));
  });

  [0, Math.floor((candles.length - 1) / 2), candles.length - 1].forEach(index => {
    const text = svgEl("text", { x: x(index), y: H - 8, "text-anchor": "middle", class: "chart-axis-label" });
    text.textContent = new Date(candles[index].openTime).toLocaleTimeString("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
    svg.appendChild(text);
  });

  const overlays = [
    { value: livePrice, label: "LIVE", cls: "chart-live-line" },
    { value: signal?.triggerPrice, label: "ENTRÉE", cls: "chart-entry-line" },
    { value: signal?.takeProfit, label: "TP", cls: "chart-tp-line" },
    { value: signal?.stopLoss, label: "SL", cls: "chart-sl-line" },
  ];
  overlays.forEach(item => {
    const value = Number(item.value);
    if (!Number.isFinite(value) || value < min || value > max) return;
    const yy = y(value);
    svg.appendChild(svgEl("line", { x1: left, x2: W - right, y1: yy, y2: yy, class: item.cls }));
    const label = svgEl("text", { x: W - right + 7, y: yy + 4, class: "chart-axis-label chart-level-label" });
    label.textContent = item.label;
    svg.appendChild(label);
  });
}

function renderOverview() {
  const snap = state.snapshots[state.snapshots.length - 1];
  const bar = $("breadth-bar"), legend = $("breadth-legend");
  bar.innerHTML = ""; legend.innerHTML = "";
  if (!snap) return;
  const colors = { TRENDING_UP: "var(--green)", TRENDING_DOWN: "var(--red)",
                   RANGING: "var(--amber)", MIXED: "var(--blue)" };
  const total = Object.values(snap.breadth || {}).reduce((a, b) => a + b, 0) || 1;
  for (const [k, v] of Object.entries(snap.breadth || {})) {
    if (!v) continue;
    const seg = document.createElement("div");
    seg.className = "seg";
    seg.style.background = colors[k] || "var(--muted)";
    seg.style.width = `${(v / total) * 100}%`;
    seg.title = `${k}: ${v}`;
    if (v / total > 0.08) seg.textContent = v;
    bar.appendChild(seg);
    const li = document.createElement("span");
    li.innerHTML = `<span class="dot" style="background:${colors[k] || "var(--muted)"}"></span>${escapeHtml(k.replace("_", " "))} · ${Number(v)} (${Math.round(100 * v / total)}%)`;
    legend.appendChild(li);
  }
  const top = snap.topVolume || [];
  $("top-volume").innerHTML = top.slice(0, 10).map((t, i) =>
    `<li><span><span class="rank">#${i + 1}</span>${escapeHtml(t.symbol)}</span>
     <span>$${Number(t.quoteVolume / 1e6).toFixed(1)}M</span></li>`).join("");
}

/* --------------------------------------------------- universe screener */
function renderScreener() {
  const tbody = document.querySelector("#screener-table tbody");
  const rows = state.screener?.rows || [];
  $("screener-meta").textContent = rows.length
    ? `${rows.length} pairs · snapshot ${fmtTime(state.screener.generatedAt)} · source ${state.screener.universeSource || "—"}`
    : "";
  $("screener-empty").classList.toggle("hidden", rows.length > 0);
  let list = rows;
  if (state.screenerSearch)
    list = list.filter(r => r.symbol.toLowerCase().includes(state.screenerSearch));
  const { key, dir } = state.screenerSort;
  list = [...list].sort((a, b) => {
    const va = a[key], vb = b[key];
    if (typeof va === "number" || typeof vb === "number")
      return ((va ?? -1e9) - (vb ?? -1e9)) * dir;
    return String(va ?? "").localeCompare(String(vb ?? "")) * dir;
  });
  const biasCls = (b) => b?.includes("bullish") ? "dir-l" : (b?.includes("bearish") ? "dir-s" : "");
  tbody.innerHTML = list.slice(0, 120).map(r => {
    const f = r.fundingRatePct;
    const fCls = f == null ? "" : (Math.abs(f) >= 0.05 ? "funding-hot" : "");
    return `<tr>
      <td class="muted">${escapeHtml(r.rank ?? "")}</td>
      <td><b>${escapeHtml(r.symbol)}</b></td>
      <td>${fmtPrice(r.price)}</td>
      <td class="${escapeHtml(biasCls(r.bias1d))}">${escapeHtml((r.bias1d || "—").replace("strong_", "s.").replace("_", " "))}</td>
      <td class="${escapeHtml(biasCls(r.bias4h))}">${escapeHtml((r.bias4h || "—").replace("strong_", "s.").replace("_", " "))}</td>
      <td class="${escapeHtml(biasCls(r.bias1h))}">${escapeHtml((r.bias1h || "—").replace("strong_", "s.").replace("_", " "))}</td>
      <td>${escapeHtml(r.regime || "—")}</td>
      <td>${r.rsi == null ? "—" : Number(r.rsi).toFixed(0)}</td>
      <td>${r.adx == null ? "—" : Number(r.adx).toFixed(0)}</td>
      <td>${r.atrPercent == null ? "—" : Number(r.atrPercent).toFixed(2)}</td>
      <td>${r.relVolume == null ? "—" : Number(r.relVolume).toFixed(2)}</td>
      <td class="${escapeHtml(fCls)}">${f == null ? "—" : Number(f).toFixed(4)}</td>
    </tr>`;
  }).join("");
}

function signalCard(s) {
  const long = s.direction === "LONG";
  const live = state.livePrices[s.symbol];
  const price = state.liveOk && live != null ? live : s.currentPrice;
  const priceLbl = state.liveOk && live != null ? "LIVE MARKET PRICE" : "PRICE (SCAN)";
  const trigger = s.triggerPrice;
  const gapPct = trigger ? ((price - trigger) / trigger * 100) : null;
  const gapTxt = gapPct == null ? "" :
    (s.status === "WAITING_TRIGGER"
      ? (long ? `needs +${Math.max(0, ((trigger - price) / price) * 100).toFixed(2)}% to trigger`
              : `needs −${Math.max(0, ((price - trigger) / price) * 100).toFixed(2)}% to trigger`)
      : `filled @ ${fmtPrice(s.entryPrice)}`);
  const zone = Array.isArray(s.entryZone) ? s.entryZone : [null, null];
  const f = s.fvg, ob = s.orderBlock;
  const cal = state.performance?.scoreCalibration?.[s.quality];
  const histTxt = cal && cal.resolved
    ? `hist. ${Number(cal.winRate).toFixed(0)}% gagnants (n=${Number(cal.resolved)})`
    : "hist. — (n=0)";
  const card = document.createElement("div");
  card.className = `sig-card ${long ? "long" : "short"}`;
  card.innerHTML = `
    <div class="sig-head">
      <span class="sig-symbol">${escapeHtml(s.symbol)} <span class="sig-dir">${escapeHtml(s.direction)}</span></span>
      <span class="sig-quality q-${escapeHtml(s.quality)}">${escapeHtml(s.quality)} · ${Number(s.score).toFixed(1)}/100 · ${escapeHtml(histTxt)}</span>
    </div>
    <div class="sig-price-row">
      <div class="price-box">
        <span class="lbl">${priceLbl} <span>${state.liveOk && live != null ? fmtAgo(state.liveAt) : fmtAgo(s.currentPriceAt)}</span></span>
        <div class="val">${fmtPrice(price)}</div>
      </div>
      <div class="price-box">
        <span class="lbl">TRIGGER</span>
        <div class="val">${fmtPrice(trigger)}</div>
        <div class="trigger-gap">${gapTxt}</div>
      </div>
    </div>
    <div class="sig-levels">
      <div class="lvl"><span class="lbl">Entry zone</span><div class="val">${fmtPrice(zone[0])}–${fmtPrice(zone[1])}</div></div>
      <div class="lvl tp"><span class="lbl">Take profit</span><div class="val">${fmtPrice(s.takeProfit)}</div></div>
      <div class="lvl sl"><span class="lbl">Stop loss</span><div class="val">${fmtPrice(s.stopLoss)}</div></div>
      <div class="lvl rr"><span class="lbl">Risk / reward</span><div class="val">1 : ${Number(s.riskReward).toFixed(2)}</div></div>
      <div class="lvl"><span class="lbl">4H bias</span><div class="val">${escapeHtml((s.htf4hBias || "—").replace("_", " "))}</div></div>
      <div class="lvl"><span class="lbl">1H bias</span><div class="val">${escapeHtml((s.htf1hBias || "—").replace("_", " "))}</div></div>
    </div>
    <div class="sig-meta">
      <span class="tag ${s.liquiditySweep ? "on" : "off"}">sweep ${s.liquiditySweep ? "@" + fmtPrice(s.liquiditySweep.level) : "✗"}</span>
      <span class="tag ${s.structure?.event?.startsWith("CHoCH") ? "on" : ""}">${escapeHtml(s.structure?.event ?? "—")}</span>
      <span class="tag ${s.displacement ? "on" : "off"}">displacement ${s.displacement ? "✓" : "✗"}</span>
      <span class="tag ${f ? "on" : "off"}">FVG ${f ? "@" + fmtPrice(f.bottom) + "–" + fmtPrice(f.top) : "✗"}</span>
      <span class="tag ${ob ? "on" : "off"}">OB ${ob ? "@" + fmtPrice(ob.bottom) + "–" + fmtPrice(ob.top) : "✗"}</span>
      <span class="tag">vol ${Number(s.relativeVolume).toFixed(2)}×</span>
      <span class="tag">RSI ${Number(s.rsi).toFixed(0)}</span>
      <span class="tag">ADX ${Number(s.adx).toFixed(0)}</span>
      <span class="tag">${escapeHtml(s.marketRegime ?? "")}</span>
      <span class="tag">1D ${escapeHtml((s.htf1dBias || "unknown").replace("strong_", "s.").replace("_", " "))}</span>
      ${s.session ? `<span class="tag ${s.session !== "OFF" ? "on" : "off"}">${escapeHtml(s.session)} KZ</span>` : ""}
      ${s.fundingRatePct != null ? `<span class="tag ${s.fundingExtreme ? "hot" : ""}">funding ${Number(s.fundingRatePct).toFixed(4)}%/8h${s.fundingExtreme ? " ⚠" : ""}</span>` : ""}
      ${s.rangeLabel ? `<span class="tag ${s.rangeLabel === "DISCOUNT" || s.rangeLabel === "PREMIUM" ? "on" : ""}">${escapeHtml(s.rangeLabel)} ${(Number(s.rangePosition) * 100).toFixed(0)}%</span>` : ""}
    </div>
    <div class="sig-status">
      <span class="status-badge st-${escapeHtml(s.status)}">${escapeHtml(String(s.status).replace("_", " "))}</span>
      <span class="sig-time">generated ${fmtTime(s.generatedAt)} · ${escapeHtml(s.timeframe)}</span>
    </div>`;
  return card;
}

function renderActive() {
  const grid = $("active-grid"), empty = $("active-empty");
  const active = state.signals
    .filter(s => (s.status === "WAITING_TRIGGER" || s.status === "TRIGGERED")
                 && state.activeFilter.has(s.quality))
    .sort((a, b) => b.score - a.score || b.generatedAt - a.generatedAt);
  grid.innerHTML = "";
  empty.classList.toggle("hidden", state.signals
    .filter(s => s.status === "WAITING_TRIGGER" || s.status === "TRIGGERED").length > 0);
  const order = Object.fromEntries(QUALITIES.map((q, i) => [q, i]));
  active.sort((a, b) => (order[a.quality] ?? 9) - (order[b.quality] ?? 9) || b.score - a.score);
  active.forEach(s => grid.appendChild(signalCard(s)));
}

function renderRecent() {
  const tbody = document.querySelector("#recent-table tbody");
  const recent = [...state.signals].sort((a, b) => b.generatedAt - a.generatedAt).slice(0, 10);
  $("recent-empty").classList.toggle("hidden", recent.length > 0);
  tbody.innerHTML = recent.map(s => `
    <tr>
      <td>${fmtTime(s.generatedAt)}</td>
      <td><b>${escapeHtml(s.symbol)}</b></td>
      <td class="${s.direction === "LONG" ? "dir-l" : "dir-s"}">${escapeHtml(s.direction)}</td>
      <td class="sig-quality q-${escapeHtml(s.quality)}">${escapeHtml(s.quality)} ${Number(s.score).toFixed(0)}</td>
      <td>${fmtPrice(s.triggerPrice)}</td>
      <td>${fmtPrice(s.takeProfit)}</td>
      <td>${fmtPrice(s.stopLoss)}</td>
      <td>1:${Number(s.riskReward).toFixed(2)}</td>
      <td><span class="status-badge st-${escapeHtml(s.status)}">${escapeHtml(String(s.status).replace("_", " "))}</span></td>
    </tr>`).join("");
}

/* --------------------------------------------------- performance (JS mirror) */
function perfFor(rangeDays) {
  const since = rangeDays ? Date.now() - rangeDays * 864e5 : 0;
  const sigs = state.signals.filter(s => s.generatedAt >= since);
  const count = (st) => sigs.filter(s => s.status === st).length;
  const wins = count("WIN"), losses = count("LOSS");
  const rWins = sigs.filter(s => s.status === "WIN").map(s => s.rMultiple ?? 0);
  const rLosses = sigs.filter(s => s.status === "LOSS").map(s => s.rMultiple ?? 0);
  const grossW = rWins.reduce((a, b) => a + b, 0);
  const grossL = Math.abs(rLosses.reduce((a, b) => a + b, 0));
  const byDir = (d) => {
    const sub = sigs.filter(s => s.direction === d);
    const w = sub.filter(s => s.status === "WIN").length;
    const l = sub.filter(s => s.status === "LOSS").length;
    return { w, l, n: w + l, wr: w + l ? (100 * w / (w + l)) : null };
  };
  const byQ = (q) => {
    const sub = sigs.filter(s => s.quality === q);
    const w = sub.filter(s => s.status === "WIN").length;
    const l = sub.filter(s => s.status === "LOSS").length;
    return { w, l, n: w + l, wr: w + l ? (100 * w / (w + l)) : null };
  };
  return {
    total: sigs.length, waiting: count("WAITING_TRIGGER"), triggeredOpen: count("TRIGGERED"),
    wins, losses, expired: count("EXPIRED"), ambiguous: count("AMBIGUOUS"), cancelled: count("CANCELLED"),
    resolved: wins + losses,
    winRate: wins + losses ? 100 * wins / (wins + losses) : null,
    pf: grossL > 0 ? grossW / grossL : null,
    avgRr: sigs.length ? sigs.reduce((a, s) => a + (s.riskReward || 0), 0) / sigs.length : null,
    avgScore: sigs.length ? sigs.reduce((a, s) => a + (s.score || 0), 0) / sigs.length : null,
    long: byDir("LONG"), short: byDir("SHORT"),
    q: Object.fromEntries(QUALITIES.map(q => [q, byQ(q)])),
    sigs,
  };
}

function renderPerformance() {
  const p = perfFor(state.perfRange);
  $("pf-total").textContent = p.total;
  $("pf-waiting").textContent = `${p.waiting} waiting · ${p.triggeredOpen} open`;
  $("pf-resolved").textContent = p.resolved;
  $("pf-split").textContent = `${p.wins}W / ${p.losses}L · ${p.expired} exp · ${p.ambiguous} amb · ${p.cancelled} canc`;
  $("pf-winrate").textContent = p.winRate == null ? "—" + "" : p.winRate.toFixed(1) + "%";
  $("pf-winrate-n").textContent = `based on ${p.wins} wins / ${p.resolved} resolved trades`;
  $("pf-pf").textContent = p.pf == null ? "—" : p.pf.toFixed(2);
  $("pf-rr").textContent = p.avgRr == null ? "—" : "1:" + p.avgRr.toFixed(2);
  $("pf-score").textContent = p.avgScore == null ? "—" : `avg score ${p.avgScore.toFixed(1)}`;

  const st = state.performance;
  $("pf-streaks").textContent = st ? `${st.maxWinningStreak ?? 0} / ${st.maxLosingStreak ?? 0}` : "—";
  $("pf-times").textContent = st && st.avgTimeToTpMs != null
    ? `avg→TP ${fmtDur(st.avgTimeToTpMs)} · avg→SL ${fmtDur(st.avgTimeToSlMs ?? null)}` : "—";

  const eng = state.performance || {};
  $("pf-expectancy").textContent = eng.expectancyR == null ? "—" : (eng.expectancyR > 0 ? "+" : "") + eng.expectancyR.toFixed(3) + "R";
  $("pf-maxdd").textContent = eng.maxDrawdownR == null ? "—" : "-" + eng.maxDrawdownR.toFixed(2) + "R";
  $("pf-sharpe").textContent = eng.sharpeR == null ? "—" : eng.sharpeR.toFixed(2);
  const slip = eng.slippage || {};
  $("pf-slip").textContent = slip.avgPct == null ? "—" : slip.avgPct.toFixed(3) + "%";
  $("pf-slip-n").textContent = `trigger vs fill · ${slip.fills || 0} fills · max ${slip.maxPct == null ? "—" : slip.maxPct.toFixed(3) + "%"}`;
  const mc = eng.monteCarlo;
  $("pf-mc").textContent = mc ? `${mc.terminalRp50 > 0 ? "+" : ""}${mc.terminalRp50}R` : "—";
  $("pf-mc-sub").textContent = mc ? `bootstrap ${mc.paths}×${mc.tradesPerPath} trades (p5 ${mc.terminalRp5}R · p95 ${mc.terminalRp95}R)` : "needs ≥ 5 resolved trades";
  $("pf-mcdd").textContent = mc ? "-" + mc.maxDDp95 + "R" : "—";

  const cal = eng.scoreCalibration || {};
  const ctbody = document.querySelector("#calibration-table tbody");
  ctbody.innerHTML = QUALITIES.map(q => {
    const c = cal[q];
    if (!c) return `<tr><td class="sig-quality q-${escapeHtml(q)}">${escapeHtml(q)}</td><td colspan="6" class="muted">no data yet</td></tr>`;
    const ci = c.wilson95 || {};
    return `<tr>
      <td class="sig-quality q-${escapeHtml(q)}">${escapeHtml(q)}</td>
      <td>${Number(c.signals)}</td><td>${Number(c.resolved)}</td><td class="dir-l">${Number(c.wins)}</td>
      <td class="dir-s">${Number(c.losses)}</td>
      <td>${c.winRate == null ? "—" : Number(c.winRate).toFixed(1) + "%"}</td>
      <td>${ci.lower == null ? "—" : Number(ci.lower) + "% – " + Number(ci.upper) + "%"}</td>
    </tr>`;
  }).join("");

  drawEquityChart(p.sigs);
  drawQualityChart(p);
  drawDirectionChart(p);
  drawOutcomeChart(p);
}

/* --------------------------------------------------- SVG charts (no libs) */
const NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function drawEquityChart(sigs) {
  const box = $("chart-equity"); box.innerHTML = "";
  const resolved = sigs.filter(s => s.status === "WIN" || s.status === "LOSS")
    .sort((a, b) => (a.closedAt ?? 0) - (b.closedAt ?? 0));
  if (resolved.length < 2) {
    box.innerHTML = `<div class="empty">Needs ≥ 2 resolved trades to plot
      (currently ${resolved.length}). History accumulates as signals resolve.</div>`;
    return;
  }
  const W = 560, H = 190, PAD = 28;
  let cum = 0;
  const pts = resolved.map(s => { cum += (s.rMultiple ?? 0); return cum; });
  const min = Math.min(0, ...pts), max = Math.max(1, ...pts);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
  const x = (i) => PAD + (i / (pts.length - 1)) * (W - 2 * PAD);
  const y = (v) => H - PAD - ((v - min) / (max - min)) * (H - 2 * PAD);
  [0, 0.5, 1].forEach(f => svg.appendChild(svgEl("line", {
    x1: PAD, x2: W - PAD, y1: y(min + f * (max - min)), y2: y(min + f * (max - min)),
    class: "grid-line" })));
  const zero = svgEl("line", { x1: PAD, x2: W - PAD, y1: y(0), y2: y(0), class: "zero-line" });
  svg.appendChild(zero);
  const path = pts.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  svg.appendChild(svgEl("path", { d: path, fill: "none", stroke: pts[pts.length - 1] >= 0 ? "var(--green)" : "var(--red)", "stroke-width": 2 }));
  const last = svgEl("circle", { cx: x(pts.length - 1), cy: y(pts[pts.length - 1]), r: 3.5,
    fill: pts[pts.length - 1] >= 0 ? "var(--green)" : "var(--red)" });
  svg.appendChild(last);
  const label = svgEl("text", { x: W - PAD, y: Math.max(12, y(pts[pts.length - 1]) - 8),
    "text-anchor": "end", class: "bar-label" });
  label.textContent = `${pts[pts.length - 1] >= 0 ? "+" : ""}${pts[pts.length - 1].toFixed(1)}R over ${pts.length} trades`;
  svg.appendChild(label);
  box.appendChild(svg);
}

function drawQualityChart(p) {
  const box = $("chart-quality"); box.innerHTML = "";
  const rows = QUALITIES.map(q => ({ q, ...p.q[q] }));
  const rowH = 44, pad = 46, W = 560, H = 16 + rows.length * rowH, barW = W - pad - 90;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  rows.forEach((r, i) => {
    const y0 = 16 + i * rowH;
    const n = r.w + r.l || 1;
    const wl = (r.w / n) * barW, ll = (r.l / n) * barW;
    svg.appendChild(svgEl("text", { x: pad - 8, y: y0 + 14, "text-anchor": "end", class: "axis-label" })).textContent = r.q;
    svg.appendChild(svgEl("rect", { x: pad, y: y0, width: Math.max(wl, 0), height: 20, rx: 3, fill: "var(--green)" }));
    svg.appendChild(svgEl("rect", { x: pad + wl, y: y0, width: Math.max(ll, 0), height: 20, rx: 3, fill: "var(--red)" }));
    svg.appendChild(svgEl("text", { x: pad + barW + 8, y: y0 + 14, class: "bar-label" })).textContent =
      `${r.w}W/${r.l}L${r.wr != null ? ` · ${r.wr.toFixed(0)}%` : ""} (n=${r.n})`;
  });
  box.appendChild(svg);
}

function drawDirectionChart(p) {
  const box = $("chart-direction"); box.innerHTML = "";
  const W = 560, H = 190, rowH = 60, pad = 60, barW = W - pad - 120;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  [["LONG", p.long, "var(--green)"], ["SHORT", p.short, "var(--red)"]].forEach(([name, d], i) => {
    const y0 = 24 + i * rowH;
    const n = d.w + d.l || 1;
    svg.appendChild(svgEl("text", { x: pad - 8, y: y0 + 14, "text-anchor": "end", class: "axis-label" })).textContent = name;
    svg.appendChild(svgEl("rect", { x: pad, y: y0, width: barW, height: 20, rx: 3, fill: "var(--card-2)", stroke: "var(--line)" }));
    svg.appendChild(svgEl("rect", { x: pad, y: y0, width: Math.max((d.w / n) * barW, 0), height: 20, rx: 3, fill: d3color(name) }));
    svg.appendChild(svgEl("text", { x: pad + barW + 8, y: y0 + 14, class: "bar-label" })).textContent =
      `${d.w}W/${d.l}L · ${d.wr == null ? "—" : d.wr.toFixed(0) + "%"} (n=${d.n})`;
  });
  function d3color(name) { return name === "LONG" ? "var(--green)" : "var(--red)"; }
  box.appendChild(svg);
}

function drawOutcomeChart(p) {
  const box = $("chart-outcomes"); box.innerHTML = "";
  const parts = [["WIN", p.wins, "var(--green)"], ["LOSS", p.losses, "var(--red)"],
                 ["EXPIRED", p.expired, "var(--muted)"], ["AMBIGUOUS", p.ambiguous, "var(--violet)"],
                 ["CANCELLED", p.cancelled, "var(--amber)"]];
  const total = parts.reduce((a, [, v]) => a + v, 0) || 1;
  const W = 560, H = 190, rowH = 32, pad = 90, barW = W - pad - 80;
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}` });
  parts.forEach(([name, v, color], i) => {
    const y0 = 12 + i * rowH;
    svg.appendChild(svgEl("text", { x: pad - 8, y: y0 + 13, "text-anchor": "end", class: "axis-label" })).textContent = name;
    svg.appendChild(svgEl("rect", { x: pad, y: y0, width: Math.max((v / total) * barW, 1), height: 17, rx: 3, fill: color, opacity: .85 }));
    svg.appendChild(svgEl("text", { x: pad + (v / total) * barW + 6, y: y0 + 13, class: "bar-label" })).textContent = v;
  });
  box.appendChild(svg);
}

/* --------------------------------------------------- history table */
function renderHistory() {
  const p = perfFor(state.histRange);
  let rows = p.sigs;
  if (state.histDir !== "all") rows = rows.filter(s => s.direction === state.histDir.toUpperCase());
  if (state.histQuality !== "all") rows = rows.filter(s => s.quality === state.histQuality);
  if (state.histOutcome !== "all") rows = rows.filter(s => s.status === state.histOutcome);
  if (state.histSearch) rows = rows.filter(s => s.symbol.toLowerCase().includes(state.histSearch));
  rows.sort((a, b) => b.generatedAt - a.generatedAt);
  const tbody = document.querySelector("#history-table tbody");
  $("history-empty").classList.toggle("hidden", rows.length > 0);
  $("history-count").textContent = `${rows.length} / ${state.signals.length} signal${state.signals.length === 1 ? "" : "s"}`;
  tbody.innerHTML = rows.slice(0, 300).map(s => {
    const dur = s.closedAt && s.triggeredAt ? s.closedAt - s.triggeredAt :
      (s.closedAt ? s.closedAt - s.generatedAt : null);
    const stateLabels = {
      WAITING_TRIGGER: "En attente d’entrée", TRIGGERED: "Entrée déclenchée",
      WIN: "Terminé", LOSS: "Terminé", EXPIRED: "Entrée expirée",
      AMBIGUOUS: "Terminé", CANCELLED: "Terminé",
    };
    const outcomes = {
      WAITING_TRIGGER: "En attente d’entrée", TRIGGERED: "En cours",
      WIN: "TP atteint", LOSS: "SL touché", EXPIRED: "Entrée non déclenchée",
      AMBIGUOUS: "Indéterminé (TP et SL touchés)", CANCELLED: "Temps maximal atteint",
    };
    const result = s.rMultiple != null
      ? `${outcomes[s.status] || s.status} · ${s.rMultiple > 0 ? "+" : ""}${Number(s.rMultiple).toFixed(2)}R`
      : (outcomes[s.status] || s.status);
    return `<tr>
      <td>${fmtTime(s.generatedAt)}</td>
      <td><b>${escapeHtml(s.symbol)}</b>${s.dataSource === "replay" ? ' <span class="tag">replay</span>' : ""}</td>
      <td class="${s.direction === "LONG" ? "dir-l" : "dir-s"}">${escapeHtml(s.direction)}</td>
      <td>${Number(s.score).toFixed(1)}</td>
      <td class="sig-quality q-${escapeHtml(s.quality)}">${escapeHtml(s.quality)}</td>
      <td>${fmtPrice(s.triggerPrice)}</td>
      <td>${fmtPrice(s.entryZone?.[0])}–${fmtPrice(s.entryZone?.[1])}</td>
      <td>${fmtPrice(s.takeProfit)}</td>
      <td>${fmtPrice(s.stopLoss)}</td>
      <td>1:${Number(s.riskReward).toFixed(2)}</td>
      <td><span class="status-badge st-${escapeHtml(s.status)}">${escapeHtml(stateLabels[s.status] || s.status)}</span></td>
      <td>${escapeHtml(result)}</td>
      <td>${fmtDur(dur)}</td>
    </tr>`;
  }).join("");
}

function selectView(view) {
  for (const name of ["analysis", "signals", "trend"]) {
    const on = name === view;
    $(`view-${name}`).classList.toggle("hidden", !on);
    $(`tab-${name}`).classList.toggle("active", on);
    $(`tab-${name}`).setAttribute("aria-selected", String(on));
  }
  if (view === "signals") renderHistory();
  if (view === "trend") renderTrend();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

$("tab-analysis").addEventListener("click", () => selectView("analysis"));
$("tab-signals").addEventListener("click", () => selectView("signals"));
$("tab-trend").addEventListener("click", () => selectView("trend"));

/* ------------------------------------------------- Tendance 4H (simulation) */
const fmtEur = (v) => v == null || !Number.isFinite(Number(v)) ? "—"
  : Number(v).toLocaleString("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 2 });
const fmtSigned = (v, digits = 2, suffix = "") => v == null || !Number.isFinite(Number(v)) ? "—"
  : `${Number(v) > 0 ? "+" : ""}${Number(v).toFixed(digits)}${suffix}`;
const sideClass = (side) => side === "LONG" ? "dir-l" : "dir-s";
const EXIT_LABELS = { STOP: "Stop touché", TRAIL: "Sortie suiveuse" };

function renderTrend() {
  const t = state.trend;
  const notice = $("trend-notice");
  if (!t || !t.version) {
    notice.textContent = "La simulation démarrera au prochain scan : aucune donnée pour le moment.";
    notice.classList.remove("hidden");
    return;
  }
  const p = t.params || {};
  const s = t.stats || {};
  const run = t.lastRun || {};
  const errors = Array.isArray(run.errors) ? run.errors : [];
  notice.textContent = errors.length ? `Dernier passage dégradé : ${errors.join(" · ")}` : "";
  notice.classList.toggle("hidden", !errors.length);
  $("trend-updated").textContent = `Mis à jour ${fmtAgo(t.generatedAt)}`;
  $("trend-subtitle").textContent = `Capital fictif de départ ${fmtEur(t.startCapital)} · risque ${Number(p.riskPct)} % par trade · aucun ordre réel n’est passé.`;
  $("tr-equity").textContent = fmtEur(s.equityMarked);
  $("tr-equity-sub").textContent = `${fmtSigned(s.returnPct, 2, " %")} · réalisé ${fmtEur(t.equity)}`;
  $("tr-open").textContent = `${Number(s.openPositions ?? 0)} / ${Number(p.maxPositions ?? 0)}`;
  $("tr-open-sub").textContent = s.exposure != null ? `exposition ${Number(s.exposure).toFixed(2)}× le capital` : "—";
  $("tr-trades").textContent = String(Number(s.trades ?? 0));
  $("tr-trades-sub").textContent = s.trades
    ? `${Math.round(100 * Number(s.winRate))} % gagnants · ${fmtSigned(s.avgR)}R en moyenne` : "aucun pour le moment";
  $("tr-dd").textContent = `${Number(s.maxDrawdownPct ?? 0).toFixed(1)} %`;
  $("tr-started").textContent = `démarrée le ${fmtTime(t.startedAt)}`;

  const positions = Array.isArray(t.positions) ? t.positions : [];
  $("trend-positions-empty").classList.toggle("hidden", positions.length > 0);
  document.querySelector("#trend-positions-table tbody").innerHTML = positions.map(pos => `<tr>
      <td>${fmtTime(pos.entryTime)}</td>
      <td><b>${escapeHtml(pos.symbol)}</b></td>
      <td class="${sideClass(pos.side)}">${escapeHtml(pos.side)}</td>
      <td>${fmtPrice(pos.entryPrice)}</td>
      <td>${fmtPrice(pos.initialStop)}</td>
      <td>${fmtPrice(pos.trailLevel)}</td>
      <td>${fmtPrice(pos.markPrice)}</td>
      <td>${fmtSigned(pos.unrealizedR)}R</td>
      <td>${fmtEur(-Number(pos.fundingPaid || 0))}</td>
    </tr>`).join("");

  const trades = (Array.isArray(t.trades) ? t.trades : []).slice().reverse();
  $("trend-trades-empty").classList.toggle("hidden", trades.length > 0);
  document.querySelector("#trend-trades-table tbody").innerHTML = trades.slice(0, 300).map(tr => `<tr>
      <td>${fmtTime(tr.exitTime)}</td>
      <td><b>${escapeHtml(tr.symbol)}</b></td>
      <td class="${sideClass(tr.side)}">${escapeHtml(tr.side)}</td>
      <td>${fmtPrice(tr.entryPrice)}</td>
      <td>${fmtPrice(tr.exitPrice)}</td>
      <td>${escapeHtml(EXIT_LABELS[tr.exitReason] || tr.exitReason)}</td>
      <td>${fmtDur(Number(tr.exitTime) - Number(tr.entryTime))}</td>
      <td>${fmtSigned(tr.rNet)}R</td>
      <td>${fmtEur(tr.pnl)}</td>
      <td>${fmtEur(tr.equityAfter)}</td>
    </tr>`).join("");

  const evals = (Array.isArray(t.evaluations) ? t.evaluations : []).slice().reverse();
  $("trend-evaluations-empty").classList.toggle("hidden", evals.length > 0);
  document.querySelector("#trend-evaluations-table tbody").innerHTML = evals.slice(0, 50).map(e => {
    const reasons = Array.isArray(e.reasons) ? e.reasons : [];
    return `<tr>
      <td>${fmtTime(e.barClose)}</td>
      <td><b>${escapeHtml(e.symbol)}</b></td>
      <td class="${sideClass(e.side)}">${escapeHtml(e.side)}</td>
      <td>${fmtPrice(e.close)}</td>
      <td>${e.relVolume != null ? `${Number(e.relVolume).toFixed(2)}×` : "—"}</td>
      <td>${e.trendAligned == null ? "—" : (e.trendAligned ? "dans le sens" : "contre")}</td>
      <td>${e.taken ? "<b>Prise</b>" : escapeHtml(`Écartée : ${reasons.join(", ")}`)}</td>
    </tr>`;
  }).join("");

  const symbols = Array.isArray(p.symbols) ? p.symbols.map(escapeHtml).join(", ") : "—";
  $("trend-rules-list").innerHTML = [
    `Cryptos suivies : ${symbols}.`,
    `Signal : une bougie 4H clôture au-dessus du plus haut (achat) ou sous le plus bas (vente) des ${Number(p.channelBars)} bougies précédentes.`,
    `Filtre 1 : uniquement dans le sens de ${escapeHtml(p.trendSymbol)} par rapport à sa moyenne mobile ${Number(p.trendEma)} en 4H.`,
    `Filtre 2 : volume de la bougie de signal supérieur à ${Number(p.volumeMultiple)} × la moyenne des ${Number(p.volumeLookback)} précédentes.`,
    `Filtre 3 : pas d’entrée si le financement dépasse ${(Number(p.maxFundingRate) * 100).toFixed(3)} % par 8 h dans le sens du trade.`,
    `Entrée à l’ouverture de la bougie suivante, stop initial à ${Number(p.stopAtr)} × ATR(${Number(p.atrPeriod)}).`,
    `Sortie : clôture 4H sous le plus bas (achat) ou au-dessus du plus haut (vente) des ${Number(p.trailBars)} dernières bougies.`,
    `Taille : ${Number(p.riskPct)} % du capital perdu si le stop est touché · ${Number(p.maxPositions)} positions maximum · exposition limitée à ${Number(p.maxLeverage)}× le capital.`,
    `Coûts simulés : frais ${Number(p.takerFeePct)} % et glissement ${Number(p.slippagePct)} % par ordre, financement des contrats perpétuels.`,
  ].map(line => `<li>${line}</li>`).join("");
}

function renderLogs() {
  const logs = state.status?.logs ?? [];
  const tail = logs.slice(-140).reverse();
  $("log-box").innerHTML = tail.length
    ? tail.map(l => {
        const cls = l.level === "error" ? "lv-error" : l.level === "warn" ? "lv-warn" : "lv-info";
        return `<span class="${cls}">[${fmtTime(l.ts)}] [${escapeHtml(String(l.level).toUpperCase().padEnd(5))}] ${escapeHtml(l.msg)}</span>`;
      }).join("\n")
    : "—";
}
/* --------------------------------------------------- interactions */
function bindPills(containerId, onPick, multi) {
  const root = $(containerId);
  root.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".pill"); if (!btn) return;
    if (multi) {
      const q = btn.dataset.q;
      if (state.activeFilter.has(q)) { btn.classList.remove("active"); state.activeFilter.delete(q); }
      else { btn.classList.add("active"); state.activeFilter.add(q); }
    } else {
      root.querySelectorAll(".pill").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
    }
    onPick(btn);
  });
}
bindPills("active-filter", () => renderActive(), true);
bindPills("perf-range", (b) => { state.perfRange = +b.dataset.range; renderPerformance(); });
bindPills("hist-direction", (b) => { state.histDir = b.dataset.d; renderHistory(); });
bindPills("hist-quality", (b) => { state.histQuality = b.dataset.q; renderHistory(); });
bindPills("hist-outcome", (b) => { state.histOutcome = b.dataset.o; renderHistory(); });
bindPills("hist-range", (b) => { state.histRange = +b.dataset.range; renderHistory(); });
$("hist-search").addEventListener("input", (e) => {
  state.histSearch = e.target.value.trim().toLowerCase(); renderHistory();
});

document.querySelector("#screener-table thead").addEventListener("click", (ev) => {
  const th = ev.target.closest("th.sortable"); if (!th) return;
  const key = th.dataset.k;
  state.screenerSort = { key, dir: state.screenerSort.key === key ? -state.screenerSort.dir : 1 };
  renderScreener();
});
$("screener-search").addEventListener("input", (e) => {
  state.screenerSearch = e.target.value.trim().toUpperCase(); renderScreener();
});

$("csv-export").addEventListener("click", () => {
  const p = perfFor(state.histRange);
  let rows = p.sigs;
  if (state.histDir !== "all") rows = rows.filter(s => s.direction === state.histDir.toUpperCase());
  if (state.histQuality !== "all") rows = rows.filter(s => s.quality === state.histQuality);
  if (state.histOutcome !== "all") rows = rows.filter(s => s.status === state.histOutcome);
  if (state.histSearch) rows = rows.filter(s => s.symbol.toLowerCase().includes(state.histSearch));
  rows.sort((a, b) => b.generatedAt - a.generatedAt);
  const head = ["generatedAt","symbol","direction","score","quality","triggerPrice","entryLow",
    "entryHigh","takeProfit","stopLoss","riskReward","status","rMultiple","session",
    "fundingRatePct","rangeLabel","htf1dBias","htf4hBias","htf1hBias"];
  const lines = [head.join(",")];
  for (const s of rows) {
    lines.push(head.map(k => {
      if (k === "generatedAt") return new Date(s.generatedAt).toISOString();
      if (k === "entryLow") return s.entryZone?.[0] ?? "";
      if (k === "entryHigh") return s.entryZone?.[1] ?? "";
      const v = s[k];
      return v == null ? "" : String(v);
    }).map(csvCell).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `signals-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
});


/* Rejection funnel: turns "0 signals" from an unexplained silence into an
 * auditable chain. Reads the stable stage codes the engine now emits. */
const FUNNEL_LABELS = {
  data_incomplete: "Data incomplete",
  htf_conflict: "4H + 1H directly oppose",
  htf_not_aligned: "Higher timeframes not aligned",
  bias1d_conflict: "1D bias conflicts",
  regime_ranging: "4H regime is RANGING",
  volatility_out_of_band: "ATR% outside band",
  momentum_adx_low: "ADX too low (no emerging trend)",
  momentum_rsi_out_of_band: "RSI outside band",
  volume_thin: "Relative volume below minimum",
  no_liquidity_sweep: "No liquidity sweep in window",
  no_structure_event: "No CHoCH/BOS after the sweep",
  no_displacement: "No displacement after structure",
  no_fvg_or_order_block: "No FVG and no order block",
  late_entry: "Late entry (price already ran)",
  risk_model_rejected: "Risk model rejected (SL/TP/RR/cost)",
  entry_blocked_by_structure: "Entry too close to opposing structure",
  funding_extreme: "Funding extreme (crowd positioned)",
  score_below_threshold: "Confluence score below threshold",
  rr_below_min: "Risk/reward below minimum",
  weaker_direction: "Opposite direction scored higher",
  duplicate_active: "Duplicate of an active signal",
  symbol_cooldown: "Symbol in cooldown",
  duplicate_setup_hash: "Identical setup already seen",
  max_active_signals: "Active-signal cap reached",
};

function renderFunnel() {
  const card = $("funnel-card");
  if (!card) return;
  const ls = state.status?.lastScan || {};
  const funnel = ls.rejectFunnel;
  const tbody = card.querySelector("tbody");
  tbody.innerHTML = "";

  if (!Array.isArray(funnel) || !funnel.length) {
    card.classList.add("hidden");
    return;
  }
  card.classList.remove("hidden");

  // Evaluations = symbols x both directions; each rejection consumes one.
  const evaluated = (ls.symbolsValid ?? 0) * 2;
  const total = funnel.reduce((a, r) => a + (r.rejected || 0), 0);
  $("funnel-intro").textContent =
    `${evaluated || total} direction-evaluations across ${ls.symbolsValid ?? 0} symbols · ` +
    `${total} rejected · ${ls.signalsGenerated ?? 0} published`;

  let remaining = evaluated || total;
  for (const row of funnel) {
    remaining -= row.rejected || 0;
    const tr = document.createElement("tr");
    const label = escapeHtml(FUNNEL_LABELS[row.stage] || row.stage);
    tr.innerHTML =
      `<td>${label}</td>` +
      `<td class="num">${Number(row.rejected)}</td>` +
      `<td class="num">${Math.max(0, remaining)}</td>`;
    tbody.appendChild(tr);
  }
}

/* --------------------------------------------------- boot */
refreshData();
refreshPrices();
setInterval(refreshData, DATA_INTERVAL_MS);
setInterval(refreshPrices, PRICE_INTERVAL_MS);
setInterval(() => { renderTopChips(); renderStatus(); }, 10_000); // keep ages and schedule status current
