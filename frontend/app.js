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

const state = { trend: null, embeddedLoaded: false };

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
      ${body}
    </article>`;
}

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
  document.querySelector("#trend-evaluations-table tbody").innerHTML = evals.slice(0, 50).map(e => {
    const reasons = Array.isArray(e.reasons) ? e.reasons : [];
    return `<tr>
      <td>${fmtTime(e.barClose)}</td>
      <td><b>${escapeHtml(e.symbol)}</b></td>
      <td class="${sideClass(e.side)}">${escapeHtml(sideWord(e.side))}</td>
      <td>${fmtPrice(e.close)}</td>
      <td>${e.relVolume != null ? `${Number(e.relVolume).toFixed(2)}×` : "—"}</td>
      <td>${e.trendAligned == null ? "—" : (e.trendAligned ? "dans le sens" : "contre")}</td>
      <td>${e.taken ? "<b>Prise</b>" : escapeHtml(`Écartée : ${reasons.join(", ")}`)}</td>
    </tr>`;
  }).join("");

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

/* --------------------------------------------------- boot */
refreshData();
setInterval(refreshData, DATA_INTERVAL_MS);
