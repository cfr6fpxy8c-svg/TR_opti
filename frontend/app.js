/* Tendance 4H — dashboard (vanilla JS, no dependencies).
 *
 * Reads trend4h-paper.json (written by scanner/trend4h.py every 15 min) and
 * shows the simulated capital, one card per crypto and the trade history.
 */
"use strict";

const DATA_INTERVAL_MS = 60_000;
const TZ = "Europe/Paris";
const $ = (id) => document.getElementById(id);
// JSON fields are untrusted whenever they are inserted into HTML.
const escapeHtml = (value) => String(value ?? "").replace(/[&<>\"']/g,
  c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ms) => ms ? new Date(ms).toLocaleString("fr-FR", {
  timeZone: TZ, hour12: false, day: "2-digit", month: "short",
  hour: "2-digit", minute: "2-digit" }) : "—";
const fmtPrice = (v) => v == null || !Number.isFinite(Number(v)) ? "—"
  : Number(v).toLocaleString("fr-FR", { maximumFractionDigits: Number(v) < 10 ? 4 : 2 });
const fmtAgo = (ms) => {
  if (!ms) return "—";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 90) return `il y a ${Math.round(s)} s`;
  if (s < 5400) return `il y a ${Math.round(s / 60)} min`;
  return `il y a ${(s / 3600).toFixed(1)} h`;
};
const fmtDur = (ms) => {
  if (!Number.isFinite(ms)) return "—";
  const h = Math.round(ms / 3_600_000);
  return h < 48 ? `${h} h` : `${Math.floor(h / 24)} j ${h % 24} h`;
};
const fmtEur = (v) => v == null || !Number.isFinite(Number(v)) ? "—"
  : Number(v).toLocaleString("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 2 });
const fmtSigned = (v, digits = 2, suffix = "") => v == null || !Number.isFinite(Number(v)) ? "—"
  : `${Number(v) > 0 ? "+" : ""}${Number(v).toFixed(digits)}${suffix}`;
const fmtLev = (v) => v == null || !Number.isFinite(Number(v)) ? "—" : `${Number(v).toFixed(2)}×`;
const pctFrom = (a, b) => Number(b) ? 100 * (Number(a) - Number(b)) / Number(b) : null;
const sideClass = (side) => side === "LONG" ? "dir-l" : "dir-s";
const sideWord = (side) => side === "LONG" ? "ACHAT" : "VENTE";
const EXIT_LABELS = { STOP: "Stop touché", TRAIL: "Sortie suiveuse" };
const shadowCell = (sh) => {
  if (!sh || typeof sh !== "object") return "—";
  if (sh.status === "closed") {
    const r = Number(sh.rNet);
    return `<b class="${r > 0 ? "dir-l" : "dir-s"}">${fmtSigned(r)}R</b> <span class="muted small">${escapeHtml(EXIT_LABELS[sh.exitReason] || sh.exitReason)}</span>`;
  }
  if (sh.status === "open") return `en cours ${fmtSigned(sh.markR)}R`;
  return "entrée à l’ouverture suivante";
};

const state = { trend: null, ai: null, embeddedLoaded: false };

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
    state.trend = await loadJSON("trend4h-paper.json");
    state.ai = await loadJSON("ai-analyst.json").catch(() => null);
    state.embeddedLoaded = false;
    $("preview-notice").classList.add("hidden");
  } catch (err) {
    // Offline file:// open or blocked network: fall back to the snapshot embedded at build time.
    if (window.__EMBEDDED_DATA__ && !state.embeddedLoaded) {
      state.trend = window.__EMBEDDED_DATA__.trend || null;
      state.embeddedLoaded = true;
    }
    console.warn("data refresh failed:", err);
    $("preview-notice").classList.remove("hidden");
  }
  renderTrend();
}

/* ------------------------------------------------------------- rendering */
const SCAN_EVERY_MS = 15 * 60_000;
const H4_MS = 4 * 3_600_000;
const LATE_AFTER_MS = 45 * 60_000;
const OFFLINE_AFTER_MS = 3 * 3_600_000;
const fmtClock = (ms) => new Date(ms).toLocaleTimeString("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const fmtIn = (ms) => {
  const m = Math.max(0, Math.round(ms / 60_000));
  return m < 60 ? `dans ${m} min` : `dans ${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
};

function renderSystem(t) {
  const now = Date.now();
  const run = t?.lastRun || {};
  const at = Number(run.at) || 0;
  const age = at ? now - at : Infinity;
  const errors = Array.isArray(run.errors) ? run.errors.length : 0;
  const [label, cls, sub] = !at ? ["En attente", "warn", "aucun passage enregistré"]
    : age > OFFLINE_AFTER_MS ? ["Hors ligne", "bad", "aucun passage depuis plus de 3 h"]
    : age > LATE_AFTER_MS ? ["En retard", "warn", "au moins 3 passages manqués"]
    : run.status !== "ok" ? ["Dégradé", "warn", `${errors} erreur(s) de données`]
    : ["OK", "ok", "données Binance reçues"];
  $("sy-health").textContent = label;
  $("sy-health").className = `stat-value ${cls}`;
  $("sy-health-sub").textContent = sub;
  $("sy-last").textContent = at ? fmtClock(at) : "—";
  $("sy-last-sub").textContent = at ? fmtAgo(at) : "—";
  const next = Math.max(Math.floor(now / SCAN_EVERY_MS) + 1, at ? Math.floor(at / SCAN_EVERY_MS) + 1 : 0) * SCAN_EVERY_MS;
  $("sy-next").textContent = `~${fmtClock(next)}`;
  const bar = (Math.floor(now / H4_MS) + 1) * H4_MS;
  $("sy-bar").textContent = fmtClock(bar);
  $("sy-bar-sub").textContent = `${fmtIn(bar - now)} · décisions à chaque clôture`;
}

// Last 4H candles with the levels that matter for this crypto (all values are coerced to numbers).
function miniChart(w, pos, p) {
  const rows = (Array.isArray(w?.candles) ? w.candles : [])
    .map(r => Array.isArray(r) ? r.slice(0, 5).map(Number) : [])
    .filter(r => r.length === 5 && r.every(Number.isFinite));
  if (rows.length < 2) return "";
  const W = 360, H = 150, PAD = 6, n = rows.length;
  const step = (W - 2 * PAD) / n;
  const channel = Math.min(n, Number(p.channelBars) + 1 || n);
  const entryIdx = pos ? Math.max(0, rows.findIndex(r => r[0] >= Number(pos.entryTime))) : 0;
  const levels = (pos
    ? [[pos.entryPrice, "lvl-entry", entryIdx], [pos.stop, "lvl-stop", entryIdx], [pos.trailLevel, "lvl-trail", entryIdx]]
    : [[w.channelHigh, "lvl-trail", n - channel], [w.channelLow, "lvl-stop", n - channel]])
    .map(([v, cls, from]) => [Number(v), cls, from]).filter(([v]) => v != null && Number.isFinite(v) && v > 0);
  const vals = rows.flatMap(r => [r[2], r[3]]).concat(levels.map(l => l[0]));
  const lo = Math.min(...vals), hi = Math.max(...vals), span = hi - lo || 1;
  const y = v => (PAD + (hi - v) / span * (H - 2 * PAD)).toFixed(1);
  const x = i => PAD + i * step + step / 2;
  const bodyW = Math.max(1, step * 0.6).toFixed(1);
  const candles = rows.map(([, o, h, l, c], i) => {
    const cls = c >= o ? "candle-up" : "candle-down";
    const top = y(Math.max(o, c)), bot = y(Math.min(o, c));
    return `<line class="${cls}" x1="${x(i).toFixed(1)}" x2="${x(i).toFixed(1)}" y1="${y(h)}" y2="${y(l)}"/>`
      + `<rect class="${cls}" x="${(x(i) - bodyW / 2).toFixed(1)}" y="${top}" width="${bodyW}" height="${Math.max(1, bot - top).toFixed(1)}"/>`;
  }).join("");
  const lines = levels.map(([v, cls, from]) =>
    `<line class="${cls}" x1="${(PAD + from * step).toFixed(1)}" x2="${W - PAD}" y1="${y(v)}" y2="${y(v)}"/>`).join("");
  const legend = pos
    ? `<span class="lg-entry">entrée</span><span class="lg-stop">stop</span><span class="lg-trail">sortie suiveuse</span>`
    : `<span class="lg-trail">seuil d’achat</span><span class="lg-stop">seuil de vente</span>`;
  return `<svg class="mini-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Bougies 4H">${candles}${lines}</svg>
    <div class="mini-legend">${n} bougies 4H · ${legend}</div>`;
}
const levelNote = (level, price, dir, allowed) => {
  if (!allowed) return "bloqué par le filtre tendance";
  const gap = pctFrom(level, price);
  if (gap == null) return "—";
  return dir * gap <= 0 ? "cours déjà au-delà : à confirmer à la clôture" : `${fmtSigned(gap, 2, " %")} du cours`;
};

function trendCard(sym, t, p) {
  const w = (t.watch && typeof t.watch === "object" ? t.watch[sym] : null) || null;
  const pos = (Array.isArray(t.positions) ? t.positions : []).find(x => x.symbol === sym);
  const pend = (Array.isArray(t.pending) ? t.pending : []).find(x => x.symbol === sym);
  const lastEval = (Array.isArray(t.evaluations) ? t.evaluations : []).filter(e => e.symbol === sym).slice(-1)[0];
  const equity = Number(t.equity);
  const name = escapeHtml(String(sym).replace(/USDT$/, ""));
  const price = pos ? pos.markPrice : w?.price;
  let badge, badgeCls, body;
  if (pos) {
    const side = pos.side === "LONG" ? 1 : -1;
    const pnl = Number(pos.stake) * Number(pos.unrealizedR) - Number(pos.fundingPaid || 0);
    badge = `${sideWord(pos.side)} en cours`;
    badgeCls = side > 0 ? "long" : "short";
    body = `<div class="trade-levels">
        <div class="trade-level entry"><span>Entrée</span><strong>${fmtPrice(pos.entryPrice)}</strong>
          <small>${fmtTime(pos.entryTime)} · cours ${fmtPrice(pos.markPrice)} (${fmtSigned(side * pctFrom(pos.markPrice, pos.entryPrice), 2, " %")})</small></div>
        <div class="trade-level sl"><span>Stop loss</span><strong>${fmtPrice(pos.stop)}</strong>
          <small>${fmtSigned(pctFrom(pos.stop, pos.entryPrice), 2, " %")} depuis l’entrée</small></div>
        <div class="trade-level tp"><span>Sortie suiveuse (pas de TP fixe)</span><strong>${fmtPrice(pos.trailLevel)}</strong>
          <small>${pos.trailLevel == null ? "calculée à la prochaine clôture" : `si clôture 4H ${side > 0 ? "sous" : "au-dessus de"} ce niveau`}</small></div>
      </div>
      <div class="setup-foot"><span>Risque si stop</span><strong>${fmtEur(pos.stake)} (${Number(p.riskPct)} %)</strong></div>
      <div class="setup-foot"><span>Taille · levier</span><strong>${fmtEur(pos.notional)} · ${fmtLev(equity > 0 ? Number(pos.notional) / equity : null)}</strong></div>
      <div class="setup-foot"><span>Résultat latent</span><strong>${fmtSigned(pos.unrealizedR)}R · ${fmtEur(pnl)}</strong></div>
      <p class="setup-explanation">Financement payé : ${fmtEur(pos.fundingPaid)}. Le trade reste ouvert tant que la tendance tient ; il n’y a pas d’objectif fixe.</p>`;
  } else if (pend) {
    badge = `${sideWord(pend.side)} à l’ouverture`;
    badgeCls = "wait";
    body = `<p class="setup-explanation">Cassure ${pend.side === "LONG" ? "haussière" : "baissière"} validée à la clôture de
      ${fmtTime(Number(pend.signalClose) + 1)} : entrée à l’ouverture de la bougie suivante, stop à ${fmtPrice(pend.riskPerUnit)} du prix d’entrée,
      risque ${fmtEur(pend.stake)}.</p>`;
  } else if (!w) {
    badge = "En attente"; badgeCls = "";
    body = `<p class="setup-explanation">Données disponibles au prochain passage du moteur.</p>`;
  } else {
    badge = "Pas de trade"; badgeCls = "";
    const longOk = w.trendSide !== "SHORT", shortOk = w.trendSide !== "LONG";
    const why = [`Aucune cassure exploitable : il faut une clôture 4H hors du canal des ${Number(p.channelBars)} dernières bougies.`];
    if (w.trendSide) {
      why.push(`${escapeHtml(p.trendSymbol)} est ${w.trendSide === "LONG" ? "au-dessus" : "en dessous"} de sa moyenne ${Number(p.trendEma)} : ${w.trendSide === "LONG" ? "seuls les achats sont autorisés" : "seules les ventes sont autorisées"}.`);
    }
    if (w.relVolume != null) {
      why.push(`Il faudra aussi un volume supérieur à ${Number(p.volumeMultiple)}× la moyenne (dernière bougie : ${Number(w.relVolume).toFixed(2)}×).`);
    }
    const rate = Number(w.fundingRate);
    if (w.fundingRate != null && Math.abs(rate) >= Number(p.maxFundingRate)) {
      why.push(`Financement déjà saturé côté ${rate > 0 ? "achat" : "vente"}.`);
    }
    if (Number(t.stats?.openPositions ?? 0) + (Array.isArray(t.pending) ? t.pending.length : 0) >= Number(p.maxPositions)) {
      why.push("Nombre maximal de positions atteint.");
    }
    if (lastEval) {
      const reasons = Array.isArray(lastEval.reasons) ? lastEval.reasons : [];
      why.push(`Dernière cassure (${sideWord(lastEval.side)}, ${fmtTime(lastEval.barClose)}) ${lastEval.taken ? "prise" : `écartée : ${escapeHtml(reasons.join(", "))}`}.`);
    }
    const stopPct = w.stopDistance && w.price ? 100 * Number(w.stopDistance) / Number(w.price) : null;
    body = `<div class="trade-levels">
        <div class="trade-level tp"><span>Achat si clôture 4H &gt;</span><strong>${fmtPrice(w.channelHigh)}</strong>
          <small>${levelNote(w.channelHigh, w.price, 1, longOk)}</small></div>
        <div class="trade-level sl"><span>Vente si clôture 4H &lt;</span><strong>${fmtPrice(w.channelLow)}</strong>
          <small>${levelNote(w.channelLow, w.price, -1, shortOk)}</small></div>
      </div>
      <div class="setup-foot"><span>Si trade maintenant : stop</span><strong>${stopPct == null ? "—" : `±${stopPct.toFixed(2)} %`}</strong></div>
      <div class="setup-foot"><span>Risque · taille · levier</span><strong>${fmtEur(w.plannedStake)} · ${fmtEur(w.plannedNotional)} · ${fmtLev(w.plannedLeverage)}</strong></div>
      <p class="setup-explanation">${why.join(" ")}</p>`;
  }
  return `<article class="card setup-card">
      <div class="setup-title-row"><h3>${name} <span class="muted small">${fmtPrice(price)}</span></h3>
        <span class="setup-state ${badgeCls}">${escapeHtml(badge)}</span></div>
      ${miniChart(w, pos, p)}
      ${body}
      ${aiCardView(sym)}
    </article>`;
}

/* ------------------------------------------------------- analyste IA */
const DIRECTION_LABELS = { UP: ["hausse", "dir-l"], DOWN: ["baisse", "dir-s"], NEUTRAL: ["neutre", "muted"] };
const aiAnalyses = () => Array.isArray(state.ai?.analyses) ? state.ai.analyses : [];
const httpsUrl = (u) => typeof u === "string" && /^https:\/\//.test(u) ? u : null;
const decisionHtml = (b) => b ? `<b class="${b.decision === "GO" ? "dir-l" : "dir-s"}">${b.decision === "GO" ? "GO" : "NO-GO"}</b> (${Number(b.probTarget)} % d’atteindre +2R)` : "—";

function aiCardView(sym) {
  const a = aiAnalyses().slice(-1)[0];
  const v = a?.assets?.[sym];
  if (!v || typeof v !== "object") return "";
  const [label, cls] = DIRECTION_LABELS[v.direction] || ["—", "muted"];
  const open = (Array.isArray(state.ai?.portfolio?.positions) ? state.ai.portfolio.positions : []).find(x => x.symbol === sym);
  return `<div class="ai-view"><div><span class="ai-tag">Avis IA</span> ${fmtTime(a.barClose)} ·
      <b class="${cls}">${label} sur 24 h</b> · P(hausse) ${Number(v.probUp)} %${v.breakout ? ` · cassure : ${decisionHtml(v.breakout)}` : ""}</div>
      <p>${escapeHtml(v.breakout?.reason || v.reason)}${v.risks ? ` <span class="muted">Risques : ${escapeHtml(v.risks)}</span>` : ""}</p>
      ${aiTradeLine(v.trade)}${open ? `<p><b>Position IA ouverte</b> : ${escapeHtml(sideWord(open.side))} depuis ${fmtPrice(open.entryPrice)} · stop ${fmtPrice(open.stop)}${open.target != null ? ` · objectif ${fmtPrice(open.target)}` : ""} · ${fmtSigned(open.markR)}R</p>` : ""}</div>`;
}

const AI_EXIT_LABELS = { STOP: "stop", TARGET: "objectif", TIME: "durée max" };
const AI_TRADE_STATUS = { opened: "ouvert dans le portefeuille IA", "skipped-open": "ignoré : position IA déjà ouverte",
  "invalid-stop": "rejeté : stop incohérent", "invalid-target": "rejeté : objectif incohérent",
  "cancelled-gap": "annulé : le prix d’ouverture dépassait le stop", expired: "expiré" };
function aiTradeLine(t) {
  if (!t || typeof t !== "object") return "";
  if (t.action === "NONE") return `<p><b>Trade IA</b> : aucun${t.reason ? ` — ${escapeHtml(t.reason)}` : ""}</p>`;
  return `<p><b>Trade IA</b> : <b class="${t.action === "LONG" ? "dir-l" : "dir-s"}">${escapeHtml(sideWord(t.action))}</b>
    (conviction ${Number(t.conviction)} %) · stop ${fmtPrice(t.stop)}${t.target != null ? ` · objectif ${fmtPrice(t.target)}` : ""}
    · ${Number(t.maxHoldHours)} h max${t.status ? ` · <i>${escapeHtml(AI_TRADE_STATUS[t.status] || t.status)}</i>` : ""}
    ${t.reason ? `— ${escapeHtml(t.reason)}` : ""}</p>`;
}

function aiBreakoutCell(e) {
  const a = aiAnalyses().find(x => x.barClose === e.barClose);
  const b = a?.assets?.[e.symbol]?.breakout;
  return b ? `<span title="${escapeHtml(b.reason)}">${decisionHtml(b)}</span>` : "—";
}

function renderAi() {
  const ai = state.ai;
  const run = ai?.lastRun || {};
  const STATUS = { ok: "analyse faite", "up-to-date": "à jour", "not-configured": "non configuré",
    "skipped-late": "clôture trop ancienne, ignorée", "gave-up": "échecs répétés sur cette clôture",
    error: "erreur", "no-data": "pas de données" };
  const last = aiAnalyses().slice(-1)[0];
  $("ai-status").textContent = !ai?.version ? "L’analyste démarrera au prochain passage."
    : `Modèle ${ai.model || "—"} · dernier passage ${fmtAgo(run.at)} : ${STATUS[run.status] || run.status || "—"}`
      + `${run.error ? ` (${run.error})` : ""} · dernière analyse ${last ? fmtTime(last.barClose) : "aucune"}.`;
  $("ai-macro").textContent = last?.macro ? `Macro : ${last.macro}` : "";
  const d = ai?.stats?.direction || {};
  $("ai-direction").textContent = d.scored
    ? `Directions à 24 h notées : ${Number(d.scored)} · réussite ${Math.round(100 * Number(d.hitRate))} % sur ${Number(d.called)} avis hausse/baisse`
      + ` (${Number(d.neutral)} neutres) · score de Brier ${Number(d.brier).toFixed(3)} (hasard = 0,250, plus bas = mieux).`
    : "Directions à 24 h : premières notes 24 h après la première analyse.";
  const m = ai?.stats?.breakouts || {};
  const cell = (g) => g && g.count
    ? `${Number(g.count)} cassure(s) · ${g.closed ? `moyenne ${fmtSigned(g.avgR)}R sur ${Number(g.closed)} terminée(s)` : "en cours"}` : "—";
  document.querySelector("#ai-matrix tbody").innerHTML = [["taken", "Nous : prise"], ["rejected", "Nous : écartée"]]
    .map(([k, label]) => `<tr><th>${label}</th><td>${cell(m[`${k}_GO`])}</td><td>${cell(m[`${k}_NO_GO`])}</td></tr>`).join("");
  const queries = Array.isArray(last?.queries) ? last.queries : [];
  const ps = ai?.stats?.portfolio;
  const sys = state.trend?.stats || {};
  $("ai-portfolio").textContent = ps
    ? `Portefeuille IA : ${fmtEur(ps.equityMarked)} (${fmtSigned(ps.returnPct, 2, " %")}) · ${Number(ps.trades)} trade(s) clos`
      + `${ps.trades ? `, ${Math.round(100 * Number(ps.winRate))} % gagnants, moyenne ${fmtSigned(ps.avgR)}R` : ""} · ${Number(ps.open)} ouvert(s)`
      + ` — Système : ${fmtEur(sys.equityMarked)} (${fmtSigned(sys.returnPct, 2, " %")}).`
    : "Portefeuille IA : démarre avec la prochaine analyse.";
  const pf = ai?.portfolio || {};
  const rows = [
    ...(Array.isArray(pf.positions) ? pf.positions : []).map(x => ({ ...x, open: true })),
    ...(Array.isArray(pf.trades) ? pf.trades : []).slice().reverse(),
  ].slice(0, 100);
  $("ai-trades-empty").classList.toggle("hidden", rows.length > 0);
  document.querySelector("#ai-trades-table tbody").innerHTML = rows.map(x => `<tr>
      <td>${fmtTime(x.entryTime)}</td>
      <td><b>${escapeHtml(x.symbol)}</b></td>
      <td class="${sideClass(x.side)}">${escapeHtml(sideWord(x.side))}</td>
      <td>${fmtPrice(x.entryPrice)}</td>
      <td>${fmtPrice(x.stop)}</td>
      <td>${x.target != null ? fmtPrice(x.target) : "—"}</td>
      <td>${x.open ? "en cours" : `${fmtPrice(x.exitPrice)} · ${escapeHtml(AI_EXIT_LABELS[x.exitReason] || x.exitReason)}`}</td>
      <td>${fmtSigned(x.open ? x.markR : x.rNet)}R</td>
      <td title="${escapeHtml(x.reason)}">${Number(x.conviction)} %</td>
    </tr>`).join("");
  const sources = Array.isArray(last?.sources) ? last.sources : [];
  $("ai-sources").innerHTML = [
    queries.length ? `Recherches Google : ${queries.map(q => `<a href="https://www.google.com/search?q=${encodeURIComponent(q)}" target="_blank" rel="noopener noreferrer">${escapeHtml(q)}</a>`).join(" · ")}` : "",
    sources.length ? `Sources : ${sources.filter(s => httpsUrl(s?.uri)).map(s => `<a href="${escapeHtml(s.uri)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.title || "source")}</a>`).join(" · ")}`
      : queries.length ? "Sources : non transmises par Google pour cette analyse." : "",
  ].filter(Boolean).map(x => `<p>${x}</p>`).join("");
}

function renderTrend() {
  const t = state.trend;
  const notice = $("trend-notice");
  renderSystem(t);
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
  $("trend-updated").textContent = `mis à jour ${fmtAgo(t.generatedAt)}`;
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

  const symbols = Array.isArray(p.symbols) ? p.symbols : [];
  $("trend-cryptos-grid").innerHTML = symbols.map(sym => trendCard(sym, t, p)).join("");

  const trades = (Array.isArray(t.trades) ? t.trades : []).slice().reverse();
  $("trend-trades-empty").classList.toggle("hidden", trades.length > 0);
  document.querySelector("#trend-trades-table tbody").innerHTML = trades.slice(0, 300).map(tr => `<tr>
      <td>${fmtTime(tr.exitTime)}</td>
      <td><b>${escapeHtml(tr.symbol)}</b></td>
      <td class="${sideClass(tr.side)}">${escapeHtml(sideWord(tr.side))}</td>
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
  document.querySelector("#trend-evaluations-table tbody").innerHTML = evals.slice(0, 300).map(e => {
    const reasons = Array.isArray(e.reasons) ? e.reasons : [];
    return `<tr>
      <td>${fmtTime(e.barClose)}</td>
      <td><b>${escapeHtml(e.symbol)}</b></td>
      <td class="${sideClass(e.side)}">${escapeHtml(sideWord(e.side))}</td>
      <td>${fmtPrice(e.close)}</td>
      <td>${e.relVolume != null ? `${Number(e.relVolume).toFixed(2)}×` : "—"}</td>
      <td>${e.trendAligned == null ? "—" : (e.trendAligned ? "dans le sens" : "contre")}</td>
      <td>${e.taken ? "<b>Prise</b>" : escapeHtml(`Écartée : ${reasons.join(", ")}`)}</td>
      <td>${shadowCell(e.shadow)}</td>
      <td>${aiBreakoutCell(e)}</td>
    </tr>`;
  }).join("");
  const ss = t.shadowStats || {};
  const line = (label, g) => g && g.count
    ? `${label} : ${Number(g.count)} (${Number(g.closed)} terminée(s)${g.closed ? `, ${Number(g.wins)} gagnante(s), moyenne ${fmtSigned(g.avgR)}R, total ${fmtSigned(g.totalR)}R` : ""})`
    : `${label} : aucune`;
  $("trend-shadow-summary").textContent = `${line("Cassures prises", ss.taken)} · ${line("Cassures écartées", ss.rejected)}. `
    + "Résultats en R après frais, hors financement.";
  renderAi();

  const symbolList = symbols.length ? symbols.map(escapeHtml).join(", ") : "—";
  $("trend-rules-list").innerHTML = [
    `Cryptos suivies : ${symbolList}.`,
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

/* ------------------------------------------- 4H + renfort 1H (second portfolio) */
const combo = { state: null, ai: null };
const BOOK_LABELS = { "4h": "4H", "1h": "renfort 1H" };
const cbAnalyses = () => Array.isArray(combo.ai?.analyses) ? combo.ai.analyses : [];
const asList = (v) => Array.isArray(v) ? v : [];

function selectTab(name) {
  for (const tab of ["main", "combo", "opti"]) {
    const on = tab === name;
    $(`view-${tab}`).classList.toggle("hidden", !on);
    $(`tab-${tab}`).classList.toggle("active", on);
    $(`tab-${tab}`).setAttribute("aria-selected", String(on));
  }
}

async function refreshCombo() {
  combo.state = await loadJSON("trend-combo-paper.json").catch(() => null);
  combo.ai = await loadJSON("ai-combo.json").catch(() => null);
  renderCombo();
}

function positionLine(label, pos) {
  const side = pos.side === "LONG" ? 1 : -1;
  return `<p><b>${label}</b> : <b class="${sideClass(pos.side)}">${escapeHtml(sideWord(pos.side))}</b> depuis ${fmtTime(pos.entryTime)}
    \u00e0 ${fmtPrice(pos.entryPrice)} \u00b7 stop ${fmtPrice(pos.stop)} \u00b7 sortie suiveuse ${fmtPrice(pos.trailLevel)}
    \u00b7 cours ${fmtPrice(pos.markPrice)} (${fmtSigned(side * pctFrom(pos.markPrice, pos.entryPrice), 2, " %")})
    \u00b7 <b>${fmtSigned(pos.unrealizedR)}R</b> \u00b7 risque ${fmtEur(pos.stake)}</p>`;
}

function comboCard(sym, b4, b1) {
  const w4 = b4.watch?.[sym] || null;
  const w1 = b1.watch?.[sym] || null;
  const p4 = asList(b4.positions).find(x => x.symbol === sym);
  const p1 = asList(b1.positions).find(x => x.symbol === sym);
  const name = escapeHtml(String(sym).replace(/USDT$/, ""));
  const lines = [];
  lines.push(p4 ? positionLine("Position 4H", p4)
    : `<p><b>Position 4H</b> : aucune${w4 ? ` \u00b7 achat si cl\u00f4ture 4H &gt; ${fmtPrice(w4.channelHigh)}, vente si &lt; ${fmtPrice(w4.channelLow)}` : ""}</p>`);
  if (p1) lines.push(positionLine("Renfort 1H", p1));
  else if (p4 && w1) lines.push(`<p><b>Renfort 1H</b> : en attente d\u2019une cl\u00f4ture 1H ${p4.side === "LONG"
    ? `au-dessus de ${fmtPrice(w1.channelHigh)}` : `sous ${fmtPrice(w1.channelLow)}`} (plus ${p4.side === "LONG" ? "haut" : "bas"} des 160 derni\u00e8res bougies 1H, avec les 3 filtres)</p>`);
  else lines.push("<p><b>Renfort 1H</b> : possible seulement pendant une position 4H, dans le m\u00eame sens.</p>");
  const a = cbAnalyses().slice(-1)[0];
  const v = a?.assets?.[sym];
  if (v && typeof v === "object") {
    const [label, cls] = DIRECTION_LABELS[v.direction] || ["\u2014", "muted"];
    const views = ["4h", "1h"].filter(f => v[`breakout${f}`])
      .map(f => ` \u00b7 cassure ${BOOK_LABELS[f]} : ${decisionHtml(v[`breakout${f}`])}`).join("");
    lines.push(`<div class="ai-view"><div><span class="ai-tag">Avis IA</span> ${fmtTime(a.barClose)} \u00b7
      <b class="${cls}">${label} sur 24 h</b> \u00b7 P(hausse) ${Number(v.probUp)} %${views}</div>
      <p>${escapeHtml(v.breakout1h?.reason || v.breakout4h?.reason || v.reason)}${v.risks ? ` <span class="muted">Risques : ${escapeHtml(v.risks)}</span>` : ""}</p>
      ${aiTradeLine(v.trade)}</div>`);
  }
  const badge = p4 && p1 ? ["4H + renfort", p4.side === "LONG" ? "long" : "short"]
    : p4 ? [`${sideWord(p4.side)} 4H`, p4.side === "LONG" ? "long" : "short"] : ["Pas de trade", ""];
  return `<article class="card setup-card">
      <div class="setup-title-row"><h3>${name} <span class="muted small">${fmtPrice(w4?.price)}</span></h3>
        <span class="setup-state ${badge[1]}">${escapeHtml(badge[0])}</span></div>
      ${w4 ? miniChart(w4, p4, b4.params || {}) : ""}
      <div class="small">${lines.join("")}</div>
    </article>`;
}

function cbAiCell(frame, e) {
  const a = cbAnalyses().find(x => x.barClose === e.barClose);
  const b = a?.assets?.[e.symbol]?.[`breakout${frame}`];
  return b ? `<span title="${escapeHtml(b.reason)}">${decisionHtml(b)}</span>` : "\u2014";
}

function renderComboAi() {
  const ai = combo.ai;
  const run = ai?.lastRun || {};
  const STATUS = { ok: "analyse faite", "up-to-date": "\u00e0 jour", waiting: "en attente d\u2019une cl\u00f4ture 4H ou d\u2019une cassure",
    "not-configured": "non configur\u00e9", "skipped-late": "cl\u00f4ture trop ancienne, ignor\u00e9e",
    "gave-up": "\u00e9checs r\u00e9p\u00e9t\u00e9s sur cette cl\u00f4ture", error: "erreur", "no-data": "pas de donn\u00e9es" };
  const last = cbAnalyses().slice(-1)[0];
  $("cb-ai-status").textContent = !ai?.version ? "L\u2019analyste de ce portefeuille d\u00e9marrera au prochain passage."
    : `Mod\u00e8le ${ai.model || "\u2014"} \u00b7 dernier passage ${fmtAgo(run.at)} : ${STATUS[run.status] || run.status || "\u2014"}`
      + `${run.error ? ` (${run.error})` : ""} \u00b7 derni\u00e8re analyse ${last ? fmtTime(last.barClose) : "aucune"}.`;
  $("cb-ai-macro").textContent = last?.macro ? `Macro : ${last.macro}` : "";
  const d = ai?.stats?.direction || {};
  $("cb-ai-direction").textContent = d.scored
    ? `Directions \u00e0 24 h not\u00e9es : ${Number(d.scored)} \u00b7 r\u00e9ussite ${Math.round(100 * Number(d.hitRate))} % sur ${Number(d.called)} avis hausse/baisse`
      + ` (${Number(d.neutral)} neutres) \u00b7 score de Brier ${Number(d.brier).toFixed(3)} (hasard = 0,250, plus bas = mieux).`
    : "Directions \u00e0 24 h : premi\u00e8res notes 24 h apr\u00e8s la premi\u00e8re analyse.";
  const ps = ai?.stats?.portfolio;
  const sys = combo.state?.stats || {};
  $("cb-ai-portfolio").textContent = ps
    ? `Portefeuille IA : ${fmtEur(ps.equityMarked)} (${fmtSigned(ps.returnPct, 2, " %")}) \u00b7 ${Number(ps.trades)} trade(s) clos`
      + `${ps.trades ? `, ${Math.round(100 * Number(ps.winRate))} % gagnants, moyenne ${fmtSigned(ps.avgR)}R` : ""} \u00b7 ${Number(ps.open)} ouvert(s)`
      + ` \u2014 4H + renfort 1H : ${fmtEur(sys.equityMarked)} (${fmtSigned(sys.returnPct, 2, " %")}).`
    : "Portefeuille IA : d\u00e9marre avec la prochaine analyse.";
  const pf = ai?.portfolio || {};
  const rows = [...asList(pf.positions).map(x => ({ ...x, open: true })), ...asList(pf.trades).slice().reverse()].slice(0, 100);
  $("cb-ai-trades-empty").classList.toggle("hidden", rows.length > 0);
  document.querySelector("#cb-ai-trades-table tbody").innerHTML = rows.map(x => `<tr>
      <td>${fmtTime(x.entryTime)}</td>
      <td><b>${escapeHtml(x.symbol)}</b></td>
      <td class="${sideClass(x.side)}">${escapeHtml(sideWord(x.side))}</td>
      <td>${fmtPrice(x.entryPrice)}</td>
      <td>${fmtPrice(x.stop)}</td>
      <td>${x.target != null ? fmtPrice(x.target) : "\u2014"}</td>
      <td>${x.open ? "en cours" : `${fmtPrice(x.exitPrice)} \u00b7 ${escapeHtml(AI_EXIT_LABELS[x.exitReason] || x.exitReason)}`}</td>
      <td>${fmtSigned(x.open ? x.markR : x.rNet)}R</td>
      <td title="${escapeHtml(x.reason)}">${Number(x.conviction)} %</td>
    </tr>`).join("");
  const m = ai?.stats?.breakouts || {};
  const cell = (g) => g && g.count
    ? `${Number(g.count)} cassure(s) \u00b7 ${g.closed ? `moyenne ${fmtSigned(g.avgR)}R sur ${Number(g.closed)} termin\u00e9e(s)` : "en cours"}` : "\u2014";
  document.querySelector("#cb-ai-matrix tbody").innerHTML = [["taken", "Nous : prise"], ["rejected", "Nous : \u00e9cart\u00e9e"]]
    .map(([k, label]) => `<tr><th>${label}</th><td>${cell(m[`${k}_GO`])}</td><td>${cell(m[`${k}_NO_GO`])}</td></tr>`).join("");
  const queries = asList(last?.queries);
  const sources = asList(last?.sources);
  $("cb-ai-sources").innerHTML = [
    queries.length ? `Recherches Google : ${queries.map(q => `<a href="https://www.google.com/search?q=${encodeURIComponent(q)}" target="_blank" rel="noopener noreferrer">${escapeHtml(q)}</a>`).join(" \u00b7 ")}` : "",
    sources.length ? `Sources : ${sources.filter(s => httpsUrl(s?.uri)).map(s => `<a href="${escapeHtml(s.uri)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.title || "source")}</a>`).join(" \u00b7 ")}`
      : queries.length ? "Sources : non transmises par Google pour cette analyse." : "",
  ].filter(Boolean).map(x => `<p>${x}</p>`).join("");
}

function renderCombo() {
  const c = combo.state;
  const notice = $("cb-notice");
  if (!c || !c.version) {
    notice.textContent = "Ce portefeuille d\u00e9marrera au prochain passage du moteur : aucune donn\u00e9e pour le moment.";
    notice.classList.remove("hidden");
    renderComboAi();
    return;
  }
  const b4 = c.book4h || {}, b1 = c.book1h || {};
  const p = c.params || {};
  const s = c.stats || {};
  const s4 = b4.stats || {}, s1 = b1.stats || {};
  const errors = asList(c.lastRun?.errors);
  notice.textContent = errors.length ? `Dernier passage d\u00e9grad\u00e9 : ${errors.join(" \u00b7 ")}` : "";
  notice.classList.toggle("hidden", !errors.length);
  $("cb-subtitle").textContent = `Capital fictif de d\u00e9part ${fmtEur(c.startCapital)} \u00b7 risque ${Number(p.risk4hPct)} % par trade 4H`
    + ` et ${Number(p.risk1hPct)} % par renfort 1H \u00b7 ind\u00e9pendant de la Tendance 4H \u00b7 aucun ordre r\u00e9el.`;
  const solo = state.trend?.stats;
  $("cb-equity").textContent = fmtEur(s.equityMarked);
  $("cb-equity-sub").textContent = `${fmtSigned(s.returnPct, 2, " %")}${solo ? ` \u00b7 Tendance 4H seule : ${fmtSigned(solo.returnPct, 2, " %")}` : ""}`;
  $("cb-open").textContent = `${Number(s4.openPositions ?? 0)} 4H \u00b7 ${Number(s1.openPositions ?? 0)} 1H`;
  $("cb-open-sub").textContent = s.exposure != null ? `exposition ${Number(s.exposure).toFixed(2)}\u00d7 le capital` : "\u2014";
  $("cb-trades").textContent = `${Number(s4.trades ?? 0)} 4H \u00b7 ${Number(s1.trades ?? 0)} 1H`;
  const wr = (x) => x.trades ? `${Math.round(100 * Number(x.winRate))} % gagnants, ${fmtSigned(x.avgR)}R` : "aucun";
  $("cb-trades-sub").textContent = `4H : ${wr(s4)} \u00b7 1H : ${wr(s1)}`;
  $("cb-dd").textContent = `${Number(s.maxDrawdownPct ?? 0).toFixed(1)} %`;
  $("cb-started").textContent = `d\u00e9marr\u00e9 le ${fmtTime(c.startedAt)}`;

  const symbols = asList(b4.params?.symbols);
  $("cb-grid").innerHTML = symbols.map(sym => comboCard(sym, b4, b1)).join("");

  const trades = [...asList(b4.trades).map(t => ({ ...t, frame: "4h" })), ...asList(b1.trades).map(t => ({ ...t, frame: "1h" }))]
    .sort((x, y) => Number(y.exitTime) - Number(x.exitTime));
  $("cb-trades-empty").classList.toggle("hidden", trades.length > 0);
  document.querySelector("#cb-trades-table tbody").innerHTML = trades.slice(0, 300).map(tr => `<tr>
      <td>${fmtTime(tr.exitTime)}</td>
      <td>${BOOK_LABELS[tr.frame]}</td>
      <td><b>${escapeHtml(tr.symbol)}</b></td>
      <td class="${sideClass(tr.side)}">${escapeHtml(sideWord(tr.side))}</td>
      <td>${fmtPrice(tr.entryPrice)}</td>
      <td>${fmtPrice(tr.exitPrice)}</td>
      <td>${escapeHtml(EXIT_LABELS[tr.exitReason] || tr.exitReason)}</td>
      <td>${fmtDur(Number(tr.exitTime) - Number(tr.entryTime))}</td>
      <td>${fmtSigned(tr.rNet)}R</td>
      <td>${fmtEur(tr.pnl)}</td>
    </tr>`).join("");

  const evals = [...asList(b4.evaluations).map(e => ({ ...e, frame: "4h" })), ...asList(b1.evaluations).map(e => ({ ...e, frame: "1h" }))]
    .sort((x, y) => Number(y.barClose) - Number(x.barClose));
  $("cb-evaluations-empty").classList.toggle("hidden", evals.length > 0);
  document.querySelector("#cb-evaluations-table tbody").innerHTML = evals.slice(0, 300).map(e => `<tr>
      <td>${fmtTime(e.barClose)}</td>
      <td>${BOOK_LABELS[e.frame]}</td>
      <td><b>${escapeHtml(e.symbol)}</b></td>
      <td class="${sideClass(e.side)}">${escapeHtml(sideWord(e.side))}</td>
      <td>${fmtPrice(e.close)}</td>
      <td>${e.relVolume != null ? `${Number(e.relVolume).toFixed(2)}\u00d7` : "\u2014"}</td>
      <td>${e.taken ? "<b>Prise</b>" : escapeHtml(`\u00c9cart\u00e9e : ${asList(e.reasons).join(", ")}`)}</td>
      <td>${shadowCell(e.shadow)}</td>
      <td>${cbAiCell(e.frame, e)}</td>
    </tr>`).join("");
  const line = (label, g) => g && g.count
    ? `${label} : ${Number(g.count)} (${Number(g.closed)} termin\u00e9e(s)${g.closed ? `, moyenne ${fmtSigned(g.avgR)}R, total ${fmtSigned(g.totalR)}R` : ""})`
    : `${label} : aucune`;
  $("cb-shadow-summary").textContent = [["4H", b4], ["1H", b1]].map(([f, b]) =>
    `${f} \u2014 ${line("prises", b.shadowStats?.taken)} \u00b7 ${line("\u00e9cart\u00e9es", b.shadowStats?.rejected)}`).join(" | ")
    + ". R\u00e9sultats en R apr\u00e8s frais, hors financement.";

  const bp = b4.params || {};
  $("cb-rules-list").innerHTML = [
    `Livre 4H : exactement les r\u00e8gles de la Tendance 4H (canal ${Number(bp.channelBars)}, sortie suiveuse ${Number(bp.trailBars)}, stop ${Number(bp.stopAtr)} \u00d7 ATR, 3 filtres), risque ${Number(p.risk4hPct)} % par trade.`,
    `Renfort 1H : cl\u00f4ture 1H au-del\u00e0 du plus haut / plus bas des ${Number(p.h1ChannelBars)} bougies 1H, avec les m\u00eames 3 filtres (tendance BTC lue en 4H), uniquement pendant une position 4H ouverte dans le m\u00eame sens, risque ${Number(p.risk1hPct)} % par trade.`,
    `Renfort 1H : stop ${Number(p.h1StopAtr)} \u00d7 ATR 1H, sortie suiveuse sur les ${Number(p.h1TrailBars)} derni\u00e8res bougies 1H.`,
    `Capital commun de ${fmtEur(c.startCapital)}, gains r\u00e9investis ; m\u00eames frais, glissement et financement que la Tendance 4H.`,
  ].map(x => `<li>${x}</li>`).join("");
  renderComboAi();
}

$("tab-main").addEventListener("click", () => selectTab("main"));
$("tab-combo").addEventListener("click", () => selectTab("combo"));

/* ------------------------------------------- 4H + 1H optimisé (third portfolio) */
const opti = { state: null };

async function refreshOpti() {
  opti.state = await loadJSON("trend-opti-paper.json").catch(() => null);
  renderOpti();
}

function optiCard(sym, b4, b1) {
  const w4 = b4.watch?.[sym] || null;
  const w1 = b1.watch?.[sym] || null;
  const p4 = asList(b4.positions).find(x => x.symbol === sym);
  const p1 = asList(b1.positions).find(x => x.symbol === sym);
  const name = escapeHtml(String(sym).replace(/USDT$/, ""));
  const lines = [];
  lines.push(p4 ? positionLine("Position 4H", p4)
    : `<p><b>Position 4H</b> : aucune${w4 ? ` \u00b7 achat si cl\u00f4ture 4H &gt; ${fmtPrice(w4.channelHigh)}, vente si &lt; ${fmtPrice(w4.channelLow)}` : ""}</p>`);
  if (p1) lines.push(positionLine("Renfort 1H", p1));
  else if (p4 && w1) lines.push(`<p><b>Renfort 1H</b> : en attente d\u2019une cl\u00f4ture 1H ${p4.side === "LONG"
    ? `au-dessus de ${fmtPrice(w1.channelHigh)}` : `sous ${fmtPrice(w1.channelLow)}`}</p>`);
  else lines.push("<p><b>Renfort 1H</b> : possible seulement pendant une position 4H, dans le m\u00eame sens.</p>");
  const badge = p4 && p1 ? ["4H + renfort", p4.side === "LONG" ? "long" : "short"]
    : p4 ? [`${sideWord(p4.side)} 4H`, p4.side === "LONG" ? "long" : "short"] : ["Pas de trade", ""];
  return `<article class="card setup-card">
      <div class="setup-title-row"><h3>${name} <span class="muted small">${fmtPrice(w4?.price)}</span></h3>
        <span class="setup-state ${badge[1]}">${escapeHtml(badge[0])}</span></div>
      ${w4 ? miniChart(w4, p4, b4.params || {}) : ""}
      <div class="small">${lines.join("")}</div>
    </article>`;
}

function renderOpti() {
  const c = opti.state;
  const notice = $("op-notice");
  if (!c || !c.version) {
    notice.textContent = "Ce portefeuille d\u00e9marrera au prochain passage du moteur : aucune donn\u00e9e pour le moment.";
    notice.classList.remove("hidden");
    return;
  }
  const b4 = c.book4h || {}, b1 = c.book1h || {};
  const p = c.params || {};
  const s = c.stats || {};
  const s4 = b4.stats || {}, s1 = b1.stats || {};
  const errors = asList(c.lastRun?.errors);
  notice.textContent = errors.length ? `Dernier passage d\u00e9grad\u00e9 : ${errors.join(" \u00b7 ")}` : "";
  notice.classList.toggle("hidden", !errors.length);
  $("op-subtitle").textContent = `Capital fictif de d\u00e9part ${fmtEur(c.startCapital)} \u00b7 risque ${Number(p.risk4hPct)} % par trade 4H`
    + ` et ${Number(p.risk1hPct)} % par renfort 1H \u00b7 ${Number(p.maxSameSide)} positions maximum dans le m\u00eame sens`
    + ` \u00b7 ind\u00e9pendant des deux autres \u00b7 aucun ordre r\u00e9el.`;
  const others = [["Tendance 4H", state.trend?.stats], ["4H + renfort 1H", combo.state?.stats]]
    .filter(([, x]) => x).map(([label, x]) => `${label} : ${fmtSigned(x.returnPct, 2, " %")}`).join(" \u00b7 ");
  $("op-equity").textContent = fmtEur(s.equityMarked);
  $("op-equity-sub").textContent = `${fmtSigned(s.returnPct, 2, " %")}${others ? ` \u00b7 ${others}` : ""}`;
  $("op-open").textContent = `${Number(s4.openPositions ?? 0)} 4H \u00b7 ${Number(s1.openPositions ?? 0)} 1H`;
  $("op-open-sub").textContent = s.exposure != null ? `exposition ${Number(s.exposure).toFixed(2)}\u00d7 le capital` : "\u2014";
  $("op-trades").textContent = `${Number(s4.trades ?? 0)} 4H \u00b7 ${Number(s1.trades ?? 0)} 1H`;
  const wr = (x) => x.trades ? `${Math.round(100 * Number(x.winRate))} % gagnants, ${fmtSigned(x.avgR)}R` : "aucun";
  $("op-trades-sub").textContent = `4H : ${wr(s4)} \u00b7 1H : ${wr(s1)}`;
  $("op-dd").textContent = `${Number(s.maxDrawdownPct ?? 0).toFixed(1)} %`;
  $("op-started").textContent = `d\u00e9marr\u00e9 le ${fmtTime(c.startedAt)}`;

  $("op-grid").innerHTML = asList(b4.params?.symbols).map(sym => optiCard(sym, b4, b1)).join("");

  const trades = [...asList(b4.trades).map(t => ({ ...t, frame: "4h" })), ...asList(b1.trades).map(t => ({ ...t, frame: "1h" }))]
    .sort((x, y) => Number(y.exitTime) - Number(x.exitTime));
  $("op-trades-empty").classList.toggle("hidden", trades.length > 0);
  document.querySelector("#op-trades-table tbody").innerHTML = trades.slice(0, 300).map(tr => `<tr>
      <td>${fmtTime(tr.exitTime)}</td>
      <td>${BOOK_LABELS[tr.frame]}</td>
      <td><b>${escapeHtml(tr.symbol)}</b></td>
      <td class="${sideClass(tr.side)}">${escapeHtml(sideWord(tr.side))}</td>
      <td>${fmtPrice(tr.entryPrice)}</td>
      <td>${fmtPrice(tr.exitPrice)}</td>
      <td>${escapeHtml(EXIT_LABELS[tr.exitReason] || tr.exitReason)}</td>
      <td>${fmtDur(Number(tr.exitTime) - Number(tr.entryTime))}</td>
      <td>${fmtSigned(tr.rNet)}R</td>
      <td>${fmtEur(tr.pnl)}</td>
    </tr>`).join("");

  const evals = [...asList(b4.evaluations).map(e => ({ ...e, frame: "4h" })), ...asList(b1.evaluations).map(e => ({ ...e, frame: "1h" }))]
    .sort((x, y) => Number(y.barClose) - Number(x.barClose));
  $("op-evaluations-empty").classList.toggle("hidden", evals.length > 0);
  document.querySelector("#op-evaluations-table tbody").innerHTML = evals.slice(0, 300).map(e => `<tr>
      <td>${fmtTime(e.barClose)}</td>
      <td>${BOOK_LABELS[e.frame]}</td>
      <td><b>${escapeHtml(e.symbol)}</b></td>
      <td class="${sideClass(e.side)}">${escapeHtml(sideWord(e.side))}</td>
      <td>${fmtPrice(e.close)}</td>
      <td>${e.relVolume != null ? `${Number(e.relVolume).toFixed(2)}\u00d7` : "\u2014"}</td>
      <td>${e.taken ? "<b>Prise</b>" : escapeHtml(`\u00c9cart\u00e9e : ${asList(e.reasons).join(", ")}`)}</td>
      <td>${shadowCell(e.shadow)}</td>
    </tr>`).join("");
  const line = (label, g) => g && g.count
    ? `${label} : ${Number(g.count)} (${Number(g.closed)} termin\u00e9e(s)${g.closed ? `, moyenne ${fmtSigned(g.avgR)}R, total ${fmtSigned(g.totalR)}R` : ""})`
    : `${label} : aucune`;
  $("op-shadow-summary").textContent = [["4H", b4], ["1H", b1]].map(([f, b]) =>
    `${f} \u2014 ${line("prises", b.shadowStats?.taken)} \u00b7 ${line("\u00e9cart\u00e9es", b.shadowStats?.rejected)}`).join(" | ")
    + ". R\u00e9sultats en R apr\u00e8s frais, hors financement.";

  const bp = b4.params || {};
  $("op-rules-list").innerHTML = [
    `M\u00eames r\u00e8gles que \u00ab 4H + renfort 1H \u00bb (canal 4H ${Number(bp.channelBars)}, sortie suiveuse ${Number(bp.trailBars)}, stop ${Number(bp.stopAtr)} \u00d7 ATR, volume &gt; ${Number(bp.volumeMultiple)}\u00d7, financement), avec 3 r\u00e9glages issus de la recherche :`,
    `<b>Tendance BTC plus lente</b> : on suit le sens de BTC par rapport \u00e0 sa moyenne des ${Number(p.trendEma)} derni\u00e8res bougies 4H (au lieu de 200), moins d\u2019allers-retours.`,
    `<b>Pas plus de ${Number(p.maxSameSide)} positions dans le m\u00eame sens</b> (4H et 1H compt\u00e9es ensemble) : les 5 cryptos bougent ensemble, 5 achats en m\u00eame temps reviennent \u00e0 un seul gros pari.`,
    `<b>Mise doubl\u00e9e</b> : ${Number(p.risk4hPct)} % du capital risqu\u00e9 par trade 4H et ${Number(p.risk1hPct)} % par renfort 1H, rendu possible par la limite ci-dessus.`,
    `Renfort 1H : cl\u00f4ture 1H au-del\u00e0 des ${Number(p.h1ChannelBars)} derni\u00e8res bougies 1H, uniquement pendant une position 4H ouverte dans le m\u00eame sens, stop ${Number(p.h1StopAtr)} \u00d7 ATR 1H, sortie suiveuse sur ${Number(p.h1TrailBars)} bougies 1H.`,
    `Capital commun de ${fmtEur(c.startCapital)}, gains r\u00e9investis ; m\u00eames frais, glissement et financement que les autres portefeuilles. Pas d\u2019analyste IA.`,
  ].map(x => `<li>${x}</li>`).join("");
}

$("tab-opti").addEventListener("click", () => selectTab("opti"));

/* --------------------------------------------------- boot */
refreshData();
setInterval(refreshData, DATA_INTERVAL_MS);
refreshCombo();
setInterval(refreshCombo, DATA_INTERVAL_MS);
refreshOpti();
setInterval(refreshOpti, DATA_INTERVAL_MS);
