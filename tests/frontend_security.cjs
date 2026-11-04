// Execute the actual renderer with hostile JSON, without third-party packages.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const nodes = new Map();
function element() {
  return { innerHTML: "", textContent: "", className: "", style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    addEventListener() {}, appendChild() {}, setAttribute() {},
    querySelector(selector) { return get(selector); }, querySelectorAll() { return []; } };
}
function get(id) { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); }
const context = vm.createContext({
  console, Date, Set, Map, Math, Number, String, JSON,
  document: { getElementById: get, querySelector: get, createElement: element },
  window: {}, setInterval() {},
});
const source = fs.readFileSync("frontend/app.js", "utf8");
// Boot performs live API calls. Exercise renderers independently of the network.
vm.runInContext(source.split("/* --------------------------------------------------- boot */")[0], context);
const run = code => vm.runInContext(code, context);
const attack = '<img src=x onerror="globalThis.compromised=true">';
context.hostile = attack;
assert.equal(run("escapeHtml(hostile)"), "&lt;img src=x onerror=&quot;globalThis.compromised=true&quot;&gt;");
for (const value of ["=1+1", "+SUM(A1)", "@SUM(A1)", "-1+1", "  =1", "\t=1"]) {
  context.cell = value;
  const exported = run("csvCell(cell)");
  assert.ok(exported.startsWith("'") || exported.startsWith('"\''));
}
assert.equal(run("csvCell('BTCUSDT')"), "BTCUSDT");
assert.equal(run("csvCell(12.5)"), "12.5");
run(`state.status = { logs: [{ ts: 0, level: hostile, msg: hostile }],
  lastScan: { symbolsValid: 1, rejectFunnel: [{ stage: hostile, rejected: hostile }] } };
  renderLogs(); renderFunnel();`);
assert.ok(!get("log-box").innerHTML.includes("<img"));
// Signals/history must render hostile attributes and strings as text.
run(`state.signals = [{ symbol: hostile, direction: hostile, quality: hostile,
  score: 90, generatedAt: 1, status: hostile, htf4hBias: hostile, htf1hBias: hostile,
  timeframe: hostile, structure: { event: hostile }, marketRegime: hostile,
  relativeVolume: 1, rsi: 50, adx: 20 }];
  globalThis.cardHTML = signalCard(state.signals[0]).innerHTML;
  renderHistory(); renderRecent();`);
assert.ok(!context.cardHTML.includes("<img"));
assert.ok(!get("#history-table tbody").innerHTML.includes("<img"));
assert.ok(!get("#recent-table tbody").innerHTML.includes("<img"));
// Paper-trading view must escape every string coming from trend4h-paper.json.
run(`state.trend = { version: 1, generatedAt: 1, startedAt: 1, startCapital: 500, equity: 500,
  params: { symbols: [hostile, "BTCUSDT", "ETHUSDT"], trendSymbol: hostile, riskPct: 1, maxPositions: 5 },
  stats: { equityMarked: 500, trades: 1, winRate: 1, avgR: 1 }, lastRun: { errors: [hostile] },
  positions: [{ symbol: hostile, side: hostile, entryTime: 1 }],
  pending: [{ symbol: "ETHUSDT", side: hostile, signalClose: 1 }],
  watch: { BTCUSDT: { price: hostile, channelHigh: hostile, trendSide: hostile, relVolume: hostile } },
  trades: [{ symbol: hostile, side: hostile, exitReason: hostile, exitTime: 2, entryTime: 1 }],
  evaluations: [{ symbol: hostile, side: hostile, taken: false, reasons: [hostile] },
    { symbol: "BTCUSDT", side: hostile, taken: false, reasons: [hostile] }] };
  renderTrend();`);
for (const id of ["trend-cryptos-grid", "#trend-trades-table tbody",
  "#trend-evaluations-table tbody", "trend-rules-list"]) {
  assert.ok(!get(id).innerHTML.includes("<img"), id);
}
assert.ok(!context.compromised);
console.log("Frontend security checks passed: HTML text, attributes, log levels, CSV formulas.");
