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
// Paper-trading view must escape every string coming from trend4h-paper.json.
run(`state.ai = { version: 1, model: hostile, lastRun: { status: hostile, error: hostile },
  stats: { direction: { scored: 1, hitRate: hostile, brier: hostile }, breakouts: { taken_GO: { count: hostile, closed: 1, avgR: hostile } } },
  analyses: [{ barClose: 1, macro: hostile, queries: [hostile], sources: [{ uri: "javascript:alert(1)", title: hostile },
    { uri: 'https://x.org/" onmouseover="globalThis.compromised=true', title: hostile }],
    assets: { BTCUSDT: { direction: hostile, probUp: hostile, reason: hostile, risks: hostile,
      trade: { action: "LONG", conviction: hostile, stop: hostile, target: hostile, status: hostile, reason: hostile },
      breakout: { decision: hostile, probTarget: hostile, reason: hostile } } } }],
  portfolio: { positions: [{ symbol: "BTCUSDT", side: hostile, reason: hostile, conviction: hostile, stop: hostile }],
    trades: [{ symbol: hostile, side: hostile, exitReason: hostile, reason: hostile, rNet: hostile }] } };`);
run(`state.trend = { version: 1, generatedAt: 1, startedAt: 1, startCapital: 500, equity: 500,
  params: { symbols: [hostile, "BTCUSDT", "ETHUSDT"], trendSymbol: hostile, riskPct: 1, maxPositions: 5 },
  stats: { equityMarked: 500, trades: 1, winRate: 1, avgR: 1 }, lastRun: { errors: [hostile] },
  positions: [{ symbol: hostile, side: hostile, entryTime: 1 }],
  pending: [{ symbol: "ETHUSDT", side: hostile, signalClose: 1 }],
  watch: { BTCUSDT: { price: hostile, channelHigh: hostile, trendSide: hostile, relVolume: hostile,
    candles: [[1, 1, 2, 0.5, 1.5], [2, hostile, 2, 1, 1], hostile, [3, 1.5, 2.5, 1, 2]] } },
  trades: [{ symbol: hostile, side: hostile, exitReason: hostile, exitTime: 2, entryTime: 1 }],
  evaluations: [{ symbol: hostile, side: hostile, taken: false, reasons: [hostile],
      shadow: { status: "closed", rNet: hostile, exitReason: hostile } },
    { symbol: "BTCUSDT", side: hostile, taken: false, reasons: [hostile], shadow: { status: "open", markR: hostile } }],
  shadowStats: { taken: { count: hostile, closed: hostile }, rejected: hostile } };
  renderTrend();`);
for (const id of ["trend-cryptos-grid", "#trend-trades-table tbody",
  "#trend-evaluations-table tbody", "trend-rules-list", "trend-shadow-summary",
  "#ai-matrix tbody", "ai-sources", "ai-status", "#ai-trades-table tbody"]) {
  assert.ok(!get(id).innerHTML.includes("<img"), id);
}
assert.ok(!get("ai-sources").innerHTML.includes("javascript:"), "only https sources become links");
assert.ok(!get("ai-sources").innerHTML.includes('" onmouseover'), "source URLs are attribute-escaped");
assert.ok(get("trend-cryptos-grid").innerHTML.includes("<svg"), "chart drawn from the valid candles");
assert.ok(!context.compromised);
console.log("Frontend security checks passed: trend cards, trade history, evaluations, rules.");
