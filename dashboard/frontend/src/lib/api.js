/* ── REST API helpers for the dashboard ───────────────────────── */
// Absolute paths so fetch isn't affected by Vite's base: '/static/'
const BASE = window.location.origin;

export async function fetchPositions() {
  const r = await fetch(`${BASE}/api/positions`);
  return r.json();
}

export async function fetchEquitySnapshot() {
  const r = await fetch(`${BASE}/api/equity/snapshot`);
  return r.json();
}

export async function fetchEngineStatus() {
  const r = await fetch(`${BASE}/api/engine/status`);
  return r.json();
}

export async function fetchEquityCurve(limit = 500) {
  const r = await fetch(`${BASE}/api/equity/curve?limit=${limit}`);
  return r.json();
}

export async function fetchEquityReport() {
  const r = await fetch(`${BASE}/api/equity/report`);
  return r.json();
}

export async function fetchTrades(limit = 100, offset = 0, symbol = null) {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (symbol) params.set('symbol', symbol);
  const r = await fetch(`${BASE}/api/trades?${params}`);
  return r.json();
}

export async function fetchStrategyEvaluations() {
  const r = await fetch(`${BASE}/api/strategies/evaluations`);
  return r.json();
}

export async function fetchStrategiesHistory({ symbol = null, limit = 500, latest_per_symbol = false } = {}) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (symbol) params.set('symbol', symbol);
  if (latest_per_symbol) params.set('latest_per_symbol', 'true');
  const r = await fetch(`${BASE}/api/strategies/history?${params}`);
  return r.json();
}

export async function fetchRecentEvents(limit = 50) {
  const r = await fetch(`${BASE}/api/recent-events?limit=${limit}`);
  return r.json();
}

export async function fetchCycleHistory(limit = 20) {
  const r = await fetch(`${BASE}/api/engine/cycle-history?limit=${limit}`);
  return r.json();
}

export async function fetchWatchlistFull() {
  const r = await fetch(`${BASE}/api/watchlist-full`);
  return r.json();
}

export async function saveWatchlist(watchlistData) {
  // watchlistData = { assets, defaults }
  const r = await fetch(`${BASE}/api/watchlist`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(watchlistData),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function fetchStrategiesFull() {
  const r = await fetch(`${BASE}/api/strategies/full`);
  return r.json();
}

export async function fetchReportComprehensive() {
  const r = await fetch(`${BASE}/api/reports/comprehensive`);
  return r.json();
}

export async function fetchReportComprehensiveDetailed({ assetClass = null, symbol = null } = {}) {
  const params = new URLSearchParams();
  if (assetClass) params.set('asset_class', assetClass);
  if (symbol) params.set('symbol', symbol);
  const r = await fetch(`${BASE}/api/reports/comprehensive/detailed?${params}`);
  return r.json();
}

export async function fetchReport24hTrades() {
  const r = await fetch(`${BASE}/api/reports/24h-trades`);
  return r.json();
}

export async function fetchReportStrategyHistory() {
  const r = await fetch(`${BASE}/api/reports/strategy-all`);
  return r.json();
}

export async function fetchReportBars() {
  const r = await fetch(`${BASE}/api/reports/bars`);
  return r.json();
}

export async function fetchReportSignalEvaluations() {
  const r = await fetch(`${BASE}/api/reports/signal-evaluations`);
  return r.json();
}

export async function restartEngine() {
  const r = await fetch(`${BASE}/api/engine/restart`, { method: 'POST' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.text();
}

/* ── Simulation Lab API helpers ─────────────────────────────── */

export async function fetchWatchlistForSimulation() {
  const r = await fetch(`${BASE}/api/simulation/watchlist`);
  return r.json();
}

export async function fetchAvailableStrategies() {
  const r = await fetch(`${BASE}/api/simulation/strategies`);
  return r.json();
}

export async function runTuning(params) {
  const r = await fetch(`${BASE}/api/simulation/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function getTuningProgress(runId) {
  const r = await fetch(`${BASE}/api/simulation/progress/${runId}`);
  return r.json();
}

export async function listSimulationResults(params = {}) {
  const qp = new URLSearchParams();
  if (params.symbol) qp.set('symbol', params.symbol);
  if (params.strategy) qp.set('strategy', params.strategy);
  if (params.page) qp.set('page', String(params.page));
  const r = await fetch(`${BASE}/api/simulation/list?${qp}`);
  return r.json();
}

export async function getSimulationResultDetail(runId) {
  const r = await fetch(`${BASE}/api/simulation/results/${runId}`);
  return r.json();
}

export async function compareSimulationResults(id1, id2) {
  const r = await fetch(`${BASE}/api/simulation/compare?first=${id1}&second=${id2}`);
  return r.json();
}

export async function runInteranalysis(params) {
  const r = await fetch(`${BASE}/api/simulation/interact`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function deleteSimulationResult(runId) {
  const r = await fetch(`${BASE}/api/simulation/delete/${runId}`, {
    method: 'DELETE',
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function getBestConfigForPush(runId, strategyName, symbol) {
  // If runId looks like a timestamp (run_id), use legacy mode
  if (/^[0-9]+T/.test(runId)) {
    const r = await fetch(`${BASE}/api/simulation/best-config/${runId}`);
    return r.json();
  }
  // Otherwise it's the strategy name (Inject tab passes symbol as first arg)
  // Handle both calling patterns:
  //   - Inject: getBestConfigForPush(selectedSymbol, stratName) — need symbol param
  //   - Tune/Results: getBestConfigForPush(selectedSymbol, s) — same pattern, just with strategy names
  const strategy = strategyName || runId;  // prefer second arg if present
  let url = `${BASE}/api/simulation/best-config?strategy=${encodeURIComponent(strategy)}`;
  // If a third arg (symbol) was explicitly passed, use it.
  // Otherwise, the first arg IS the symbol and we can't infer it.
  // The callers always pass exactly two args: getBestConfigForPush(selectedSymbol, stratName)
  // We need to distinguish: is runId a symbol or a strategy name?
  // Strategy names never look like timestamps, but symbols (BTC/USD) also don't.
  // However: if we're in "Inject" mode, the first arg IS the selectedSymbol and
  // the second arg IS the strategy. But the function signature expects runId as first arg.
  // So for Inject calls where BOTH args are strings that aren't timestamps,
  // we treat them as (symbol, strategyName).
  // The caller passes (selectedSymbol, stratName) which maps to (runId=SYMBOL, strategyName=STRAT).
  // We need the symbol param — that's runId when it isn't a timestamp!
  if (!/^[0-9]+T/.test(runId)) {
    // This is an Inject-style call: first arg IS the symbol
    url += `&symbol=${encodeURIComponent(runId)}`;
  }
  const r = await fetch(url);
  return r.json();
}

export async function getWatchlistDetails() {
  const r = await fetch(`${BASE}/api/simulation/watchlist/details`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function pushDryRunDiff(symbol, params, removeStrategies = [], replaceAll = false) {
  const body = { symbol, params, remove_strategies: removeStrategies, replace_all_strategies: replaceAll };
  // Don't send strategy field at all for multi-strat — backend detects nested params format
  const r = await fetch(`${BASE}/api/simulation/preview-push`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function pushLiveConfig(symbol, params, removeStrategies = [], replaceAll = false) {
  const body = { symbol, params, commit: true, remove_strategies: removeStrategies, replace_all_strategies: replaceAll };
  // Don't send strategy field — backend detects nested params format
  const r = await fetch(`${BASE}/api/simulation/push`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/* ── Task 4+5: Visual Tuner + Ranking API helpers ───────────── */

export async function runVisualTuner(symbol, strategy, options = {}) {
  const body = {
    symbol,
    strategy,
    days: options.days ?? 365,
    timeframe: options.timeframe || null,
    chart_window_days: options.chartWindowDays ?? 90,
    trials: options.trials ?? 20,
  };
  const r = await fetch(`${BASE}/api/simulation/run-visual`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function runRanking(symbol, options = {}) {
  const body = {
    symbol,
    days: options.days ?? 365,
    timeframe: options.timeframe || null,
    window_bars: options.windowBars ?? 10,
  };
  const r = await fetch(`${BASE}/api/simulation/rank`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/* ── Log files ───────────────────────────────────────────── */
export async function fetchLogFiles() {
  const r = await fetch(`${BASE}/api/logs/list`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function fetchLogLines(file, lines = 100) {
  const r = await fetch(`${BASE}/api/logs/last?file=${encodeURIComponent(file)}&lines=${lines}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
