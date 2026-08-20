import React, { useState, useEffect, useCallback, useContext, useRef } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer,
  ScatterChart, Scatter, AreaChart, Area,
} from 'recharts';
import {
  fetchWatchlistForSimulation,
  fetchAvailableStrategies,
  runTuning,
  getTuningProgress,
  listSimulationResults,
  deleteSimulationResult,
  getSimulationResultDetail,
  compareSimulationResults,
  runInteranalysis,
  getBestConfigForPush,
  pushDryRunDiff,
  pushLiveConfig,
  getWatchlistDetails,
  runVisualTuner,
  runRanking,
} from '../lib/api.js';

/* ═══════════════════════════════════════════════════════════════ */
/*  SimConfigContext — shared tuning config across all tabs       */
/* ═══════════════════════════════════════════════════════════════ */

const SimConfigContext = React.createContext();

function SimConfigProvider({ children }) {
  const [config, setConfig] = useState({
    symbol: '',
    strategy: '',
    days: 30,
    trials: 10,
    timeframe: 'auto',
    tuneDays: 90,
    tuneTrials: 20,
  });
  return (
    <SimConfigContext.Provider value={{ config, setConfig }}>
      {children}
    </SimConfigContext.Provider>
  );
}

function useSimConfig() {
  const ctx = useContext(SimConfigContext);
  if (!ctx) throw new Error('useSimConfig must be used within SimConfigProvider');
  return ctx;
}

/**
 * MIP Phase 8 — Unified progress parser.
 * Normalizes /progress API responses from both Tune (sim_*) and Visual (vt_*) paths
 * so frontend tabs share identical parsing logic instead of each tab hand-rolling it.
 */
function parseProgress(raw, totalTrials) {
  if (!raw || typeof raw !== 'object') return null;
  const completed = Math.min(raw.completed || 0, totalTrials);
  const pct = totalTrials > 0 ? Math.round((completed / totalTrials) * 100) : 0;
  return {
    status: raw.status || 'running',
    completed,
    pct: Math.min(100, pct),
    leaderboard: Array.isArray(raw.leaderboard) ? raw.leaderboard : [],
    chart_url: raw.chart_url || null,
    equity_chart_url: raw.equity_chart_url || null,
    trades: Array.isArray(raw.trades) && raw.trades.length > 0 ? raw.trades : [],
    best_trades_count: raw.best_trades_count || raw.trades?.length || 0,
    error: raw.error || (raw.status === 'error' ? raw.error : ''),
    stdout: raw.stdout || null,
  };
}

/** Poll /progress with unified parser; calls onProgress(progress) for each update.
 * Returns a cleanup function that stops polling. */
function usePollProgress(runId, totalTrials, isVisual) {
  const [progress, setProgress] = useState(null);

  /* Reset progress when runId changes — prevents stale completion state from blocking new runs */
  useEffect(() => {
    if (runId) setProgress(null);
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      try {
        const raw = await getTuningProgress(runId);
        const p = parseProgress(raw, totalTrials);
        if (!cancelled) setProgress(p);
      } catch { /* keep polling */ }
    }, isVisual ? 2000 : 1500);

    // Safety kill
    const safetyMs = isVisual
      ? 7_200_000   // Visual: 2h
      : Math.max(360_000, totalTrials * 120_000); // Tune: proportional
    const timer = setTimeout(() => {
      clearInterval(interval);
      if (!cancelled) setProgress(prev => prev ? { ...prev, status: 'stopped' } : null);
    }, safetyMs);

    return () => { cancelled = true; clearInterval(interval); clearTimeout(timer); };
  }, [runId, totalTrials, isVisual]);

  return progress;
}

const TABS = ['Tune', 'Visual', 'Results', 'Inject'];
const SEVERITY_COLORS = { none: 'var(--green)', low: 'var(--blue)', medium: 'var(--amber)', high: 'var(--red)' };

function fmtMoney(v) {
  if (v == null) return '-';
  const sign = v >= 0 ? '+' : '';
  return `${sign}$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
/* win_rate from ResultStore is raw percent (e.g. 50 = 50%), not decimal */
function pctDisplay(v) {
  if (v == null || isNaN(v)) return '-';
  /* If value looks like a fraction (< 1), multiply by 100 to get percent */
  const raw = Math.abs(v) < 1 ? v * 100 : v;
  return `${raw >= 0 ? '+' : ''}${raw.toFixed(2)}%`;
}

/* ── reusable UI atoms ───────────────────────────────────────── */

/** Inline help tooltip for each tab. */
function HowTo({ title, children }) {
  return (
    <div style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px', fontSize: 12, lineHeight: 1.65 }}>
      <div style={{ fontWeight: 600, color: 'var(--blue)', marginBottom: 6, fontSize: 13 }}>ℹ️ {title}</div>
      <div style={{ color: 'var(--muted)' }}>{children}</div>
    </div>
  );
}

function Panel({ title, children }) {
  return (
    <div style={{ background: 'var(--surface)', borderRadius: 8, border: '1px solid var(--border)', padding: 16 }}>
      {title && <h3 style={{ margin: '0 0 12px', fontSize: 14 }}>{title}</h3>}
      {children}
    </div>
  );
}

function Button({ children, onClick, disabled, primary, small }) {
  const bg = primary ? 'var(--blue)' : 'transparent';
  const border = primary ? 'none' : '1px solid var(--border)';
  const color = primary ? '#fff' : 'var(--text)';
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        padding: small ? '4px 10px' : '8px 16px',
        background: bg,
        color,
        border,
        borderRadius: 6,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.5 : 1,
        fontSize: 13,
        fontWeight: primary ? 600 : 400,
        transition: 'opacity .15s',
      }}
    >
      {children}
    </button>
  );
}

function Select({ value, onChange, options, label, searchable, style }) {
  const [filter, setFilter] = useState('');
  const filtered = searchable
    ? (options || []).filter(o => {
        const text = typeof o === 'string' ? o : (o.label ?? o.value ?? '');
        return text.toLowerCase().includes(filter.toLowerCase());
      })
    : options;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, ...style }}>
      {label && <label style={{ fontSize: 11, color: 'var(--muted)' }}>{label}</label>}
      {searchable && filter !== '' && (
        <input
          placeholder="Type to filter..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
          onClick={e => e.stopPropagation()}
          style={{
            background: 'var(--bg)',
            color: 'var(--text)',
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: '4px 8px',
            fontSize: 12,
            outline: 'none',
          }}
        />
      )}
      <select
        value={value || ''}
        onChange={e => { onChange(e.target.value); if (searchable) setFilter(''); }}
        style={{
          background: 'var(--bg)',
          color: 'var(--text)',
          border: '1px solid var(--border)',
          borderRadius: 6,
          padding: '8px 10px',
          fontSize: 13,
          outline: 'none',
        }}
      >
        <option value="">— Select —</option>
        {filtered.map(o => (
          <option key={o.value ?? o} value={o.value ?? o}>{typeof o === 'object' ? o.label : o}</option>
        ))}
      </select>
    </div>
  );
}

/** Generic search input — usable in any tab to type an asset name and auto-filter the dropdown or list. */
function SearchInput({ value, onChange, placeholder = 'Search assets…', width }) {
  return (
    <input
      value={value || ''}
      onChange={e => onChange(e.target.value)}
      placeholder={placeholder}
      style={{
        background: 'var(--bg)',
        color: 'var(--text)',
        border: '1px solid var(--border)',
        borderRadius: 6,
        padding: '8px 12px',
        fontSize: 13,
        outline: 'none',
        width: width || '100%',
      }}
    />
  );
}

function TextInput({ value, onChange, label, type = 'text', small }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      {label && <label style={{ fontSize: 11, color: 'var(--muted)' }}>{label}</label>}
      <input
        type={type}
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        style={{
          background: 'var(--bg)',
          color: 'var(--text)',
          border: '1px solid var(--border)',
          borderRadius: 6,
          padding: small ? '4px 8px' : '8px 10px',
          fontSize: small ? 12 : 13,
          outline: 'none',
          width: small ? '70px' : '100%',
        }}
      />
    </div>
  );
}

function Slider({ value, onChange, min = 1, max = 100, step = 1 }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        style={{ flex: 1, accentColor: 'var(--blue)' }}
      />
      <span style={{ fontSize: 12, color: 'var(--muted)', minWidth: 36, textAlign: 'right' }}>{value}</span>
    </div>
  );
}

function ProgressBar({ pct, height = 8 }) {
  return (
    <div style={{ background: 'var(--bg)', borderRadius: 4, overflow: 'hidden', height }}>
      <div
        style={{
          width: `${Math.min(100, Math.max(0, pct))}%`,
          height: '100%',
          background: 'linear-gradient(90deg, var(--blue), #60a5fa)',
          borderRadius: 4,
          transition: 'width .3s ease',
        }}
      />
    </div>
  );
}

function Badge({ label, color }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 99,
      background: `${color}22`,
      color,
      fontSize: 11,
      fontWeight: 600,
    }}>
      {label}
    </span>
  );
}

function EmptyState({ message }) {
  return (
    <div style={{ padding: '32px 16px', textAlign: 'center', color: 'var(--muted)', fontSize: 13 }}>
      {message}
    </div>
  );
}

function LoadingText() {
  return <span style={{ color: 'var(--blue)' }}>Loading…</span>;
}

/** Shared asset filter state — keeps all tabs in sync when Lance types an asset name. */
function AssetFilterBar({ assets, onSelectAsset, selectedAsset, onClear }) {
  const [search, setSearch] = useState('');
  const filtered = search
    ? (assets || []).filter(a => String(a).toLowerCase().includes(search.toLowerCase()))
    : [];

  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
      <SearchInput value={search} onChange={setSearch} placeholder="Filter assets…" width="180px" />
      {selectedAsset && (
        <>
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>Selected:</span>
          <span style={{ padding: '2px 10px', background: 'rgba(59,130,246,0.15)', borderRadius: 6, fontSize: 12, fontWeight: 600, color: 'var(--blue)' }}>{selectedAsset}</span>
          <button onClick={onClear} style={{ padding: '2px 8px', fontSize: 11, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--muted)', cursor: 'pointer' }}>✕ Clear</button>
        </>
      )}
      {/* Inline quick-select dropdown when filtered */}
      {filtered.length > 0 && (
        <select
          value={selectedAsset || ''}
          onChange={e => e.target.value && onSelectAsset(e.target.value)}
          style={{ background: 'var(--bg)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: 6, padding: '4px 8px', fontSize: 12, outline: 'none', minWidth: 120 }}
        >
          <option value="">— pick —</option>
          {filtered.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
/*  TAB 1 — TUNE                                                 */
/* ═══════════════════════════════════════════════════════════════ */
function TuneTab() {
  const { config, setConfig } = useSimConfig();

  // Initialize local state from shared context on mount/tab-switch
  const [watchlist, setWatchlist] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [symbol, setSymbol] = useState(config.symbol);
  const [strategy, setStrategy] = useState(config.strategy);
  const [assetFilter, setAssetFilter] = useState('');
  const [days, setDays] = useState(config.days > 0 ? config.days : 30);
  const [trials, setTrials] = useState(config.trials > 0 ? config.trials : 10);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [runAllStrategies, setRunAllStrategies] = useState(false);
  const [allRuns, setAllRuns] = useState([]);
  const [selectedDeleteIds, setSelectedDeleteIds] = useState([]);
  const [leaderboard, setLeaderboard] = useState([]);

  /* ── Advanced Tuning State (Phase 4) ── */
  const [advTimeframe, setAdvTimeframe] = useState(config.timeframe || 'auto');
  const [advTpMode, setAdvTpMode] = useState('auto');
  const [tpLevelsManual, setTpLevelsManual] = useState([0.01, 0.02, 0.03]);
  const [tpSplitManual, setTpSplitManual] = useState([0.3, 0.4, 0.3]);
  const [advParamOverrides, setAdvParamOverrides] = useState({});
  const [advExpanded, setAdvExpanded] = useState(false);
  const [stratParamsCache, setStratParamsCache] = useState({});
  const [runId, setRunId] = useState(null);

  /* ── Error state for inline feedback ── */
  const [error, setError] = useState('');

  /* ── Progress counter used by runTune before the hook returns ── */
  const [progressVal, setProgressVal] = useState(0);

  /* ── Load accumulated tuning runs from backend ── */
  const loadAllRuns = useCallback(async () => {
    try {
      const res = await listSimulationResults();
      const runs = Array.isArray(res) ? res : (res?.results ?? res?.runs ?? []);
      setAllRuns(runs);
    } catch {
      setAllRuns([]);
    }
  }, []);

  /* ── Sync shared config when Tune tab edits happen ── */
  const syncConfig = useCallback((field, value) => {
    setConfig(prev => ({ ...prev, [field]: value }));
  }, [setConfig]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [w, s] = await Promise.all([
          fetchWatchlistForSimulation().catch(() => []),
          fetchAvailableStrategies().catch(() => []),
        ]);
        // Normalize: backend may return {symbols:[...]} or plain [...]
        const wArr = Array.isArray(w) ? w : (w?.symbols ?? []);
        const sArr = Array.isArray(s)
          ? (s.length > 0 && typeof s[0] === 'string' ? s : s.map(x => x.name))
          : (s?.strategies ?? []).map(x => x.name);
        
        if (!cancelled) { 
          setWatchlist(wArr); 
          setStrategies(sArr); 
          // Auto-select first strategy if none is selected yet
          if (!strategy && sArr.length > 0) setStrategy(typeof sArr[0] === 'string' ? sArr[0] : (sArr[0]?.name || ''));
          setLoading(false); 
        }
      } catch (e) { setError(e.message); setLoading(false); }
    })();

    /* ── Param descriptors fetch (Phase 4) — separate effect to avoid stale closure ── */
  }, []);

  useEffect(() => {
    if (!strategies?.length || !symbol) return;
    let cancelled = false;
    const params = {};
    for (const s of strategies) {
      try {
        const cfg = getBestConfigForPush(symbol, s);
        if (cfg && cfg.params) params[s] = cfg.params;
      } catch {}
    }
    if (!cancelled) setStratParamsCache(params);
  }, [symbol, strategies]);

  const runTune = useCallback(async () => {
    console.log('[TuneTab] === runTune STARTED ===');
    // Capture the real checkbox state at this exact moment (bypasses React async batching)

    const cbEl = document.querySelector('#run-all-strategies-checkbox');

    const actualRunAll = cbEl ? cbEl.checked : false;

    console.log('[TuneTab] runAllStrategies=', actualRunAll, 'strategies.length=', strategies.length, 'reactState=', runAllStrategies, 'strategy=', JSON.stringify(strategy));

    // Also check if the element is checked in the DOM as a diagnostic
    const cb = document.querySelector('input[type=checkbox][accentColor]');
    console.log('[TuneTab] checkbox DOM checked=', cb ? cb.checked : 'not found');
    if (!symbol) { setError('Select a symbol'); return; }
    if (actualRunAll && !strategies.length) { setError('No strategies available'); return; }
    // If NOT running all, require an explicit strategy selection
    if (!actualRunAll && (!strategy || !strategy.trim())) { setError('Select a strategy'); return; }
    

    // If running all strategies, ensure the list is loaded first
    let activeStrategies = strategies;
    if (actualRunAll && !activeStrategies.length) {
      // Strategies may not have loaded yet — fetch them inline
      try {
        const sArr = await fetchAvailableStrategies();
        activeStrategies = Array.isArray(sArr)
          ? (sArr.length > 0 && typeof sArr[0] === 'string' ? sArr : sArr.map(x => x.name))
          : (sArr?.strategies ?? []).map(x => x.name);
      
      } catch {
        setError('Could not load available strategies');
        return;
      }
    }

    const strategiesToRun = (actualRunAll ? activeStrategies : [strategy]).filter(s => s && typeof s === 'string' && s.trim());
    if (!strategiesToRun.length) {
      setError(actualRunAll ? 'No strategies available to run' : 'Select a strategy');
      return;
    }
    
    setRunning(true);
    setError('');
    setLeaderboard([]);
    setProgressVal(0);

    /* ── Build baseParams from advanced state ── */
    const makeParams = (strat) => ({
      symbol, strategy: strat, method: 'meta', days, trials,
      timeframe: advTimeframe, tp_mode: advTpMode,
    });
    if (advTpMode === 'manual') {
      const bp = makeParams(strategiesToRun[0]);
      bp.tp_levels_override = tpLevelsManual;
      bp.tp_split_override = tpSplitManual;
    }
    if (Object.keys(advParamOverrides).length > 0) {
      const bp = makeParams(strategiesToRun[0]);
      bp.param_grid_override = advParamOverrides;
    }

    /* Launch all strategies sequentially, ~500ms between each to avoid resource contention */
    let results = [];
        console.log('[TuneTab] Starting loop, count=', strategiesToRun.length, 'names=', strategiesToRun);
    for (let i = 0; i < strategiesToRun.length; i++) {
      const s = strategiesToRun[i];
      // Skip empty/falsy strategy names
      if (!s || typeof s !== 'string') { console.warn('[TuneTab] Skipping invalid strategy:', s); continue; }
      const baseParams = makeParams(s);
      if (advTpMode === 'manual') {
        baseParams.tp_levels_override = tpLevelsManual;
        baseParams.tp_split_override = tpSplitManual;
      }
      if (Object.keys(advParamOverrides).length > 0) {
        baseParams.param_grid_override = advParamOverrides;
      }

      try {
        const res = await runTuning(baseParams);
        results.push({ strategy: s, runId: res.run_id });
        if (i === 0) setRunId(res.run_id);
        console.log('[TuneTab] Run', i+1, 'of', strategiesToRun.length, 'completed:', s, 'runId=', res.run_id);
        setError(`Running ${i+1}/${strategiesToRun.length}: ${s}...`);
      } catch (e) {
        setError(e.message);
        results.push({ strategy: s, error: e.message });
        setRunning(false);
        return;
      }

      /* Stagger runs to avoid resource contention */
      console.log('[TuneTab] Staggering 500ms before next strategy');
        if (i < strategiesToRun.length - 1) {
        await new Promise(resolve => setTimeout(resolve, 500));
      }
    }

        setRunning(false);
    // Clear error/status bar on successful completion of all strategies
    setError('');
  }, [symbol, strategy, days, trials, advTimeframe, advTpMode, tpLevelsManual, tpSplitManual, advParamOverrides]);

  const progress = usePollProgress(runId, trials, false);

  // Track whether this run has already triggered loadAllRuns to avoid double-calls.
  const lastLoadedRunRef = useRef(null);

  useEffect(() => {
    if (!progress || !runId) return;
    // Reset loaded flag when a new run starts.
    if (lastLoadedRunRef.current === runId) {
      lastLoadedRunRef.current = null;
    }
    // Always refresh the runs list whenever progress updates.
    // For fast completions, this catches the null>completed transition in one shot.
    if (lastLoadedRunRef.current !== runId) {
      loadAllRuns();
      lastLoadedRunRef.current = runId;
    }
    // Clear running state and error message when any progress update shows completed/error.
    if (progress.status === 'completed' || progress.status === 'error') {
      setRunning(false);
      if (progress.status === 'completed') setError('');
    }
  }, [progress, runId]);

  const displayedLeaderboard = progress ? progress.leaderboard : leaderboard;

  const deleteRun = useCallback(async (runIdToDelete) => {
    if (!window.confirm(`Delete tuning run ${runIdToDelete}? This cannot be undone.`)) return;
    try {
      await deleteSimulationResult(runIdToDelete);
      loadAllRuns();
    } catch (e) { setError(e.message); }
  }, [loadAllRuns]);

  const deleteSelected = useCallback(async () => {
    if (!selectedDeleteIds.length) return;
    if (!window.confirm(`Delete ${selectedDeleteIds.length} tuning run(s)? This cannot be undone.`)) return;
    let ok = 0, fail = 0;
    for (const id of selectedDeleteIds) {
      try { await deleteSimulationResult(id); ok++; } catch { fail++; }
    }
    if (!fail) setSelectedDeleteIds([]);
    loadAllRuns();
    if (ok > 0) setError(`Deleted ${ok} run(s).${fail ? ` ${fail} failed.` : ''}`);
  }, [selectedDeleteIds, deleteSimulationResult, loadAllRuns]);

  const toggleSelected = (id) => {
    setSelectedDeleteIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };
  const selectAll = () => setSelectedDeleteIds(filteredAllRuns.map(r => r.run_id));
  const clearSelection = () => setSelectedDeleteIds([]);

  /* Filter allRuns by asset filter AND/or selected symbol */
  const filteredAllRuns = (() => {
    let result = allRuns;
    if (assetFilter) {
      result = result.filter(r => String(r.symbol).toLowerCase().includes(assetFilter.toLowerCase()));
    }
    if (symbol) {
      result = result.filter(r => String(r.symbol).toLowerCase() === symbol.toLowerCase());
    }
    return result;
  })();

  if (loading) return <LoadingText />;

  // progress = completed count from /progress API; leaderboard.length is the same data
  // Don't add them — use only the completed count for the denominator
  const total = Math.max(progress, leaderboard.length);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Combined collapsed info box at top */}
      <details style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <summary style={{ padding: '8px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: 'var(--blue)', background: 'rgba(59,130,246,0.06)' }}>
          ℹ️ Tune & Parameters Info ▼
        </summary>
        <div style={{ padding: '12px 16px', fontSize: 12, lineHeight: 1.65, color: 'var(--muted)' }}>
          <div style={{ fontWeight: 600, color: 'var(--blue)', marginBottom: 6, fontSize: 13 }}>ℹ️ Tune — Auto-Parameter Optimization</div>
          Find near-optimal parameters for a strategy on a given symbol. The simulator runs a grid search over parameter space,
          then ranks results by Sharpe ratio. Each trial is an independent vectorized backtest (no live trading).
          <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '10px 0' }} />
          <div style={{ fontWeight: 600, color: 'var(--blue)', marginBottom: 4, fontSize: 13 }}>Parameters</div>
          <b>Symbol</b> — Which asset to optimize (from your watchlist).<br/>
          <b>Strategy</b> — Which strategy group's parameters to tune.<br/>
          <b>Lookback Days</b> — How many past days of bar data to use. More = more reliable but slower.<br/>
          <b>Trials</b> — Number of parameter combinations to test. Higher gives finer search but takes longer.
        </div>
      </details>

      {/* Asset filter bar */}
      {watchlist.length > 0 && (
        <AssetFilterBar assets={watchlist} selectedAsset={assetFilter || ''} onSelectAsset={(v) => { setAssetFilter(v); if (v) { setSymbol(v); syncConfig('symbol', v); } }} onClear={() => { setAssetFilter(''); setSymbol(''); syncConfig('symbol', ''); }} />
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
        <Select value={symbol} onChange={(v) => { setSymbol(v); syncConfig('symbol', v); }} options={watchlist.map(s => ({ value: s, label: s }))} label="Symbol" searchable />
        <Select value={strategy} onChange={(v) => { setStrategy(v); syncConfig('strategy', v); }} options={strategies.map(s => ({ value: s, label: s }))} label="Strategy" />
      </div>

      {/* Run all strategies checkbox */}
      {strategies.length > 0 && (
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: runAllStrategies ? 'var(--blue)' : 'var(--text)', paddingTop: 4 }}>
          <input id="run-all-strategies-checkbox" type="checkbox" checked={runAllStrategies} onChange={(e) => setRunAllStrategies(e.target.checked)} style={{ accentColor: 'var(--blue)', width: 16, height: 16 }} />
          <span>Run all {strategies.length} strategies for this asset</span>
        </label>
      )}

      {/* ── Advanced Tuning (Phase 4) ── */}
      <details open={advExpanded} onToggle={(e) => setAdvExpanded(e.target.open)} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <summary style={{ padding: '10px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: 'var(--blue)', background: 'rgba(59,130,246,0.06)' }}>
          ⚙️ Advanced Tuning {advExpanded ? '▲' : '▼'}
        </summary>
        <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>

          {/* Timeframe selector */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--muted)', minWidth: 80 }}>Timeframe:</label>
            <Select value={advTimeframe} onChange={(v) => { setAdvTimeframe(v); syncConfig('timeframe', v); }}
              options={[
                { value: 'auto', label: 'Auto (default)' },
                { value: '5m', label: '5 min' },
                { value: '15m', label: '15 min' },
                { value: '30m', label: '30 min' },
                { value: '1h', label: '1 hour' },
                { value: '4h', label: '4 hours' },
                { value: '1d', label: '1 day' },
              ]}
              style={{ flex: 1 }} />
          </div>

          {/* TP Mode toggle */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <label style={{ fontSize: 12, color: 'var(--muted)', minWidth: 80 }}>TP Mode:</label>
            <div style={{ display: 'flex', gap: 4 }}>
              <button onClick={() => setAdvTpMode('auto')}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12,
                  background: advTpMode === 'auto' ? 'var(--blue)' : 'transparent',
                  color: advTpMode === 'auto' ? '#fff' : 'var(--text)',
                  border: advTpMode === 'auto' ? 'none' : '1px solid var(--border)',
                  cursor: 'pointer' }}>
                Auto
              </button>
              <button onClick={() => setAdvTpMode('manual')}
                style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12,
                  background: advTpMode === 'manual' ? 'var(--blue)' : 'transparent',
                  color: advTpMode === 'manual' ? '#fff' : 'var(--text)',
                  border: advTpMode === 'manual' ? 'none' : '1px solid var(--border)',
                  cursor: 'pointer' }}>
                Manual
              </button>
            </div>
          </div>

          {/* Manual TP Levels */}
          {advTpMode === 'manual' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <label style={{ fontSize: 12, color: 'var(--muted)' }}>TP Levels (%)</label>
              <div style={{ display: 'flex', gap: 8 }}>
                {tpLevelsManual.map((v, i) => (
                  <input key={i} type="number" step="0.001" min="0"
                    value={v}
                    onChange={(e) => setTpLevelsManual(prev => { const n = [...prev]; n[i] = Number(e.target.value); return n; })}
                    style={{ flex: 1, background: 'var(--bg)', color:'var(--text)', border:'1px solid var(--border)', borderRadius: 6, padding: '6px 10px', fontSize: 12, outline: 'none' }} />
                ))}
              </div>
              <label style={{ fontSize: 12, color: 'var(--muted)' }}>TP Splits (%)</label>
              <div style={{ display: 'flex', gap: 8 }}>
                {tpSplitManual.map((v, i) => (
                  <input key={i} type="number" step="0.01" min="0" max="1"
                    value={v}
                    onChange={(e) => setTpSplitManual(prev => { const n = [...prev]; n[i] = Number(e.target.value); return n; })}
                    style={{ flex: 1, background: 'var(--bg)', color:'var(--text)', border:'1px solid var(--border)', borderRadius: 6, padding: '6px 10px', fontSize: 12, outline: 'none' }} />
                ))}
              </div>
            </div>
          )}

          {/* Param Grid Override */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <label style={{ fontSize: 12, color: 'var(--muted)' }}>Param Overrides (JSON)</label>
            <textarea value={JSON.stringify(advParamOverrides, null, 2)}
              onChange={(e) => { try { setAdvParamOverrides(JSON.parse(e.target.value)); } catch {} }}
              placeholder='{"sma_fast": 10, "rsi_period": 14}'
              style={{ width: '100%', minHeight: 80, background: 'var(--bg)', color:'var(--text)', border:'1px solid var(--border)', borderRadius: 6, padding: '8px 10px', fontSize: 12, fontFamily: 'monospace', outline: 'none', resize: 'vertical' }} />
          </div>
        </div>
      </details>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px', gap: 12, alignItems: 'end' }}>
        <div>
          <label style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4, display: 'block' }}>Lookback Days</label>
          <Slider value={days} onChange={(v) => { setDays(v); syncConfig('days', v); }} min={7} max={365} step={1} />
        </div>
        <TextInput value={trials} onChange={(v) => { setTrials(Number(v) || 0); syncConfig('trials', Number(v) || 0); }} label="Trials" type="number" small />
      </div>

      <Button onClick={runTune} primary disabled={running}>
        {running ? 'Running…' : 'Run Tuning'}
      </Button>

      {running && (
        <Panel title={`Progress${runId ? ` · ${runId}` : ''}`}>
          <ProgressBar pct={progress?.pct ?? 0} height={10} />
          <div style={{ fontSize: 12, color: 'var(--muted)', marginTop: 6 }}>
            {progress?.completed ?? 0} / {trials} trials complete ({progress?.pct ?? 0}%)
          </div>
        </Panel>
      )}

      {displayedLeaderboard.length > 0 && (
        <Panel title="Leaderboard">
          {symbol && strategy && !running && (
            <div style={{ fontSize: 12, color: 'var(--blue)', marginBottom: 8 }}>
              Filtering by {symbol} / {strategy}
            </div>
          )}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Rank','Trial ID','Sharpe','Win Rate','Total Return','Max DD'].map(h => (
                    <th key={h} style={{ padding:'6px 10px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {displayedLeaderboard.map((row, i) => (
                  <tr key={row.trial_id || i} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding:'6px 10' }}>{i + 1}</td>
                    <td style={{ padding:'6px 10', fontFamily:'monospace' }}>{row.trial_id || '-'}</td>
                    <td style={{ padding:'6px 10', color: row.sharpe >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {row.sharpe != null ? row.sharpe.toFixed(2) : '-'}
                    </td>
                    <td style={{ padding:'6px 10' }}>{pctDisplay(row.win_rate)}</td>
                    <td style={{ padding:'6px 10', color: row.total_return >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {fmtMoney(typeof row.total_return === 'number' && !isNaN(row.total_return) ? row.total_return * 100 : null)}
                    </td>
                    <td style={{ padding:'6px 10', color: row.max_drawdown != null ? 'var(--red)' : 'var(--muted)' }}>
                      {row.max_drawdown != null ? `${(row.max_drawdown * 100).toFixed(2)}%` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {/* Accumulated leaderboard from all completed runs */}
      {allRuns.length > 0 && (
        <Panel key={`runs-${allRuns.length}-${Date.now()}`} title={`All Tuning Runs (${filteredAllRuns.length}${assetFilter ? ' / ' + filteredAllRuns.length : ''})`}>
          {/* Selection controls */}
          {selectedDeleteIds.length > 0 && (
            <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: 'var(--blue)' }}>{selectedDeleteIds.length} selected</span>
              <Button onClick={selectAll} small>Select All (shown)</Button>
              <Button onClick={clearSelection} small>Deselect All</Button>
              <Button onClick={deleteSelected} disabled={false} style={{ background: 'rgba(239,68,68,0.15)', color: 'var(--red)', border: '1px solid var(--red)' }}>Delete Selected ({selectedDeleteIds.length})</Button>
            </div>
          )}

          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500, fontSize:12, width:32 }}>
                    <input type="checkbox" checked={selectedDeleteIds.length === filteredAllRuns.length && filteredAllRuns.length > 0} onChange={(e) => e.target.checked ? selectAll() : clearSelection()} />
                  </th>
                  {['Run','Strategy','Days','Trials','Trial ID','Sharpe','Win Rate','Total Return','Max DD'].map(h => (
                    <th key={h} style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500, fontSize:12 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredAllRuns.map((row, i) => (
                  <tr key={`${row.run_id}_${i}`} style={{ borderBottom: '1px solid var(--border)', background: selectedDeleteIds.includes(row.run_id) ? 'rgba(59,130,246,0.08)' : 'transparent' }}>
                    <td style={{ padding:'6px 8' }}>
                      <input type="checkbox" checked={selectedDeleteIds.includes(row.run_id)} onChange={() => toggleSelected(row.run_id)} />
                    </td>
                    <td style={{ padding:'6px 8', fontFamily:'monospace', fontSize:11 }}>{(row.run_id||'').slice(0,24)}</td>
                    <td style={{ padding:'6px 8' }}>{row.symbol}</td>
                    <td style={{ padding:'6px 8' }}>{row.days}</td>
                    <td style={{ padding:'6px 8' }}>{row.trials}</td>
                    <td style={{ padding:'6px 8', fontFamily:'monospace', fontSize:12 }}>{row.trial_id || '-'}</td>
                    <td style={{ padding:'6px 8', color: row.sharpe >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {row.sharpe != null ? row.sharpe.toFixed(2) : '-'}
                    </td>
                    <td style={{ padding:'6px 8' }}>{pctDisplay(row.win_rate)}</td>
                    <td style={{ padding:'6px 8', color: row.total_return >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {fmtMoney(typeof row.total_return === 'number' && !isNaN(row.total_return) ? row.total_return * 100 : null)}
                    </td>
                    <td style={{ padding:'6px 8', color: row.max_drawdown != null ? 'var(--red)' : 'var(--muted)' }}>
                      {row.max_drawdown != null ? `${(row.max_drawdown * 100).toFixed(2)}%` : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {error && <div style={{ padding: 8, background: 'var(--red)', color: '#fff', borderRadius: 6, fontSize: 13 }}>{error}</div>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
/*  TAB 2 — RESULTS                                              */
/* ═══════════════════════════════════════════════════════════════ */
function ResultsTab() {
  const [watchlist, setWatchlist] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [symbolFilter, setSymbolFilter] = useState('');
  const [strategyFilter, setStrategyFilter] = useState('');
  const [runs, setRuns] = useState([]);
  const [selectedIds, setSelectedIds] = useState([]);
  const [assetFilter, setAssetFilter] = useState('');
  const [detail, setDetail] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [showChartComparison, setShowChartComparison] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [w, s] = await Promise.all([
          fetchWatchlistForSimulation().catch(() => []),
          fetchAvailableStrategies().catch(() => []),
        ]);
        // Normalize: backend may return {symbols:[...]} or plain [...]
        const wArr = Array.isArray(w) ? w : (w?.symbols ?? []);
        const sArr = Array.isArray(s)
          ? (s.length > 0 && typeof s[0] === 'string' ? s : s.map(x => x.name))
          : (s?.strategies ?? []).map(x => x.name);
        if (!cancelled) setWatchlist(wArr);
        if (!cancelled) setStrategies(sArr);
      } catch {}
    })();
    return () => { cancelled = true; };
  }, []);

  const loadRuns = useCallback(async () => {
    setLoading(true); setError('');
    try {
      const params = {};
      if (symbolFilter) params.symbol = symbolFilter;
      if (strategyFilter) params.strategy = strategyFilter;
      const data = await listSimulationResults(params);
      setRuns(data.results || data.runs || data);
    } catch (e) { setError(e.message); }
    setLoading(false);
  }, [symbolFilter, strategyFilter]);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  /* Apply asset filter client-side on top of server filters */
  const displayedRuns = assetFilter
    ? runs.filter(r => String(r.symbol).toLowerCase().includes(assetFilter.toLowerCase()))
    : runs;

  const toggleSelect = (id) => {
    setSelectedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };
  const selectAll = () => setSelectedIds(displayedRuns.map(r => r.run_id));
  const clearSelection = () => setSelectedIds([]);

  const deleteSelected = useCallback(async () => {
    if (!selectedIds.length) return;
    if (!window.confirm(`Delete ${selectedIds.length} result(s)? This cannot be undone.`)) return;
    let ok = 0, fail = 0;
    for (const id of selectedIds) {
      try { await deleteSimulationResult(id); ok++; } catch { fail++; }
    }
    if (!fail) setSelectedIds([]);
    loadRuns();
    if (ok > 0) setError(`Deleted ${ok} result(s).${fail ? ` ${fail} failed.` : ''}`);
  }, [selectedIds, deleteSimulationResult, loadRuns]);

  const handleRowClick = async (run) => {
    if (selectedIds.length === 0 || selectedIds.find(x => x === run.run_id)) {
      // Show detail if not comparing
      if (comparison) setComparison(null);
      try { const d = await getSimulationResultDetail(run.run_id); setDetail(d); } catch (e) { setError(e.message); }
    }
  };

  const handleCompare = async () => {
    if (selectedIds.length !== 2) return;
    setComparison(null); setDetail(null);
    try {
      // Use run_id from selection array directly
      const [firstId, secondId] = selectedIds;
      const c = await compareSimulationResults(firstId, secondId);
      setComparison(c);
    } catch (e) { setError(e.message); }
  };

  const isSelected = id => selectedIds.includes(id);

  if (loading && runs.length === 0) return <LoadingText />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Collapsed info box at top */}
      <details style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <summary style={{ padding: '8px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: 'var(--blue)', background: 'rgba(59,130,246,0.06)' }}>
          ℹ️ Results — Tuning History ▼
        </summary>
        <div style={{ padding: '12px 16px', fontSize: 12, lineHeight: 1.65, color: 'var(--muted)' }}>
          Browse all simulation runs, filtered by symbol or strategy. Click a row to see detailed metrics
          (Sharpe, win rate, return, max drawdown, best parameters). Select two rows and compare side-by-side.
          Results come from completed tuning runs stored in the strategy-lab ResultStore.
        </div>
      </details>

      {/* Asset filter bar */}
      {watchlist.length > 0 && (
        <AssetFilterBar assets={watchlist} selectedAsset={assetFilter || ''} onSelectAsset={(v) => { setAssetFilter(v); if (v) setSymbolFilter(v); }} onClear={() => { setAssetFilter(''); setSymbolFilter(''); }} />
      )}

      {/* Filters */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 12 }}>
        <Select value={symbolFilter} onChange={setSymbolFilter} options={[{ value: '', label: 'All symbols' }, ...watchlist.map(s => ({ value: s, label: s }))]} label="Symbol" />
        <Select value={strategyFilter} onChange={setStrategyFilter} options={[{ value: '', label: 'All strategies' }, ...strategies.map(s => ({ value: s, label: s }))]} label="Strategy" />
      </div>

      {runs.length === 0 ? (
        <EmptyState message="No simulation results found. Run a tuning to populate results." />
      ) : (
        <>
          <Panel title={`Results (${displayedRuns.length}${assetFilter ? ' shown' : ''})`}>
            {/* Selection controls */}
            {selectedIds.length > 0 && (
              <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: 'var(--blue)' }}>{selectedIds.length} selected</span>
                <Button onClick={selectAll} small>Select All (shown)</Button>
                <Button onClick={clearSelection} small>Deselect All</Button>
                <Button onClick={deleteSelected} disabled={false} style={{ background: 'rgba(239,68,68,0.15)', color: 'var(--red)', border: '1px solid var(--red)' }}>Delete Selected ({selectedIds.length})</Button>
              </div>
            )}

            {/* Comparison banner */}
            {selectedIds.length > 0 && selectedIds.length < 2 && (
              <div style={{ fontSize: 12, color: 'var(--blue)', marginBottom: 8 }}>
                {selectedIds.length} selected — click another to compare.
              </div>
            )}

            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    <th style={{ padding:'6px 10', width:32 }}>
                      <input type="checkbox" checked={selectedIds.length === displayedRuns.length && displayedRuns.length > 0} onChange={(e) => e.target.checked ? selectAll() : clearSelection()} />
                    </th>
                    {['Symbol','Strategy','Days','Timeframe','TP Mode','TP Levels','TP Split','Created','Sharpe','Win %','Return'].map(h => (
                      <th key={h} style={{ padding:'6px 10', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {displayedRuns.map(run => (
                    <tr
                      key={run.run_id}
                      onClick={(e) => e.stopPropagation() && handleRowClick(run)}
                      style={{
                        borderBottom: '1px solid var(--border)',
                        background: isSelected(run.run_id) ? 'var(--blue)11' : 'transparent',
                        cursor: 'pointer',
                      }}
                    >
                      <td style={{ padding:'6px 10', width:32 }}>
                        <input type="checkbox" checked={isSelected(run.run_id)} onChange={(e) => { e.stopPropagation(); toggleSelect(run.run_id); }} />
                      </td>
                      <td style={{ padding:'6px 10', fontWeight:600 }}>{run.symbol || '-'}</td>
                      <td style={{ padding:'6px 10' }}>{run.strategy || '-'}</td>
                      <td style={{ padding:'6px 10' }}>{run.days ?? '-'}</td>
                      <td style={{ padding:'6px 10', fontSize:12, color:'var(--muted)' }}>{run.timeframe || '-'}</td>
                      <td style={{ padding:'6px 10', fontSize:12, color:'var(--muted)' }}>{run.tp_mode || '-'}</td>
                      <td style={{ padding:'6px 10', fontSize:12, color:'var(--muted)' }}>
                        {run.tp_levels ? (Array.isArray(run.tp_levels) ? run.tp_levels.join(', ') : String(run.tp_levels)) : '-'}
                      </td>
                      <td style={{ padding:'6px 10', fontSize:12, color:'var(--muted)' }}>
                        {run.tp_split ? (Array.isArray(run.tp_split) ? run.tp_split.join(', ') : String(run.tp_split)) : '-'}
                      </td>
                      <td style={{ padding:'6px 10', color:'var(--muted)', fontSize:12 }}>
                        {run.created_at ? new Date(run.created_at).toLocaleDateString() : '-'}
                      </td>
                      <td style={{ padding:'6px 10', color:(run.sharpe||0)>=0?'var(--green)':'var(--red)' }}>
                        {run.sharpe != null ? run.sharpe.toFixed(2) : '-'}
                      </td>
                      <td style={{ padding:'6px 10' }}>{pctDisplay(run.win_rate)}</td>
                      <td style={{ padding:'6px 10', color:(run.total_return||0)>=0?'var(--green)':'var(--red)' }}>
                        {fmtMoney(typeof run.total_return === 'number' && !isNaN(run.total_return) ? run.total_return * 100 : null)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {selectedIds.length === 2 && (
              <Button onClick={handleCompare} primary style={{ marginTop: 8 }}>Compare Selected</Button>
            )}
          </Panel>

          {/* Detail panel */}
          {detail && !comparison && (
            <Panel title={`Details — ${detail.symbol || ''} / ${detail.strategy || ''}`}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 8 }}>
                {[
                  ['ID', detail.run_id],
                  ['Symbol', detail.symbol],
                  ['Strategy', detail.strategy],
                  ['Days', detail.days],
                  ['Trials', '-'],
                  ['Timeframe', detail.timeframe || '-'],
                  ['TP Mode', detail.tp_mode || '-'],
                  ['TP Levels', detail.tp_levels ? (Array.isArray(detail.tp_levels) ? detail.tp_levels.join(', ') : String(detail.tp_levels)) : '-'],
                  ['TP Split', detail.tp_split ? (Array.isArray(detail.tp_split) ? detail.tp_split.join(', ') : String(detail.tp_split)) : '-'],
                  ['Sharpe Ratio', detail.metrics?.sharpe != null ? detail.metrics.sharpe.toFixed(4) : '-'],
                  ['Win Rate', detail.metrics?.win_rate != null ? pctDisplay(detail.metrics.win_rate) : '-'],
                  ['Total Return', fmtMoney(typeof detail.metrics?.total_return === 'number' && !isNaN(detail.metrics.total_return) ? detail.metrics.total_return * 100 : null)],
                  ['Max Drawdown', detail.metrics?.max_drawdown != null ? `${(detail.metrics.max_drawdown * 100).toFixed(2)}%` : '-'],
                  ['Best Params', JSON.stringify(detail.params || {}).slice(0, 120)],
                ].map(([k, v]) => (
                  <div key={k} style={{ fontSize: 12 }}>
                    <span style={{ color: 'var(--muted)' }}>{k}: </span>
                    <span>{v || '-'}</span>
                  </div>
                ))}
              </div>
              {detail.error && (
                <div style={{ marginTop: 8, fontSize: 12, color: 'var(--red)' }}>Error: {detail.error}</div>
              )}
            </Panel>
          )}

          {/* Comparison panel — delta analysis */}
          {comparison && (() => {
            const diff = comparison.diff || {};
            // Flatten metric_diffs (dict keyed by name → {a, b}) to array
            const metricArr = Object.entries(diff.metric_diffs || {}).map(([name, vals]) => ({
              metric: name,
              a_value: vals?.a ?? null,
              b_value: vals?.b ?? null,
              delta: typeof vals?.a === 'number' && typeof vals?.b === 'number'
                ? vals.b - vals.a : null,
            }));
            // Flatten param_diffs similarly
            const paramArr = Object.entries(diff.param_diffs || {}).map(([name, vals]) => ({
              param: name,
              a_value: vals?.a ?? null,
              b_value: vals?.b ?? null,
            }));
            // Which metrics should be "higher is better" vs "lower is better"
            const LOWER_IS_BETTER = new Set(['max_drawdown','avg_drawdown','avg_loss']);
            const isBetter = (m, delta) => {
              if (delta == null || delta === 0) return false;
              if (LOWER_IS_BETTER.has(m)) return delta < 0;
              return delta > 0;
            };

            // Find a rough "winner" by counting metric wins
            let firstWins = 0, secondWins = 0;
            metricArr.forEach(m => {
              if (m.delta == null) return;
              if (isBetter(m.metric, m.delta)) secondWins++;
              else if (isBetter(m.metric, -m.delta)) firstWins++;
            });
            const winner = firstWins > secondWins ? 'first' : secondWins > firstWins ? 'second' : null;

            return (
              <Panel title={`Comparison: ${comparison.first.symbol||''}/${comparison.first.strategy||''} vs ${comparison.second.symbol||''}/${comparison.second.strategy||''}`}>
                {/* Quick verdict */}
                {winner && (
                  <div style={{ padding:'10px 14px', background: winner==='first' ? 'rgba(59,130,246,0.1)' : 'rgba(251,191,36,0.1)', borderRadius: 6, fontSize: 13, marginBottom: 12 }}>
                    <b>Verdict:</b>{' '}{winner === 'first' ? comparison.first.symbol+'/'+comparison.first.strategy : comparison.second.symbol+'/'+comparison.second.strategy} wins on {Math.max(firstWins,secondWins)} of {firstWins+secondWins > 0 ? firstWins+secondWins : metricArr.length} metrics.
                  </div>
                )}

                {/* Metric deltas table */}
                {metricArr.length > 0 && (
                  <div style={{ marginBottom: 16 }}>
                    <h4 style={{ margin:'0 0 8px', fontSize:13, color:'var(--muted)' }}>Metric Delta (B − A)</h4>
                    <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                      <thead>
                        <tr style={{ borderBottom:'1px solid var(--border)' }}>
                          <th style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)' }}>Metric</th>
                          <th style={{ padding:'6px 8px', textAlign:'right', color:'var(--blue)' }}>{comparison.first.strategy||'A'}</th>
                          <th style={{ padding:'6px 8px', textAlign:'right', color:'var(--amber)' }}>{comparison.second.strategy||'B'}</th>
                          <th style={{ padding:'6px 8px', textAlign:'center', color:'var(--muted)', fontSize:11 }}>Δ</th>
                        </tr>
                      </thead>
                      <tbody>
                        {metricArr.map(m => {
                          const delta = m.delta;
                          const better = isBetter(m.metric, delta);
                          const worse = delta != null && !better && delta !== 0;
                          return (
                            <tr key={m.metric} style={{ borderBottom:'1px solid var(--border)' }}>
                              <td style={{ padding:'6px 8', fontFamily:'monospace' }}>{m.metric}</td>
                              <td style={{ padding:'6px 8', textAlign:'right', fontFamily:'monospace' }}>{typeof m.a_value === 'number' ? m.a_value.toFixed(4) : String(m.a_value ?? '-')}</td>
                              <td style={{ padding:'6px 8', textAlign:'right', fontFamily:'monospace' }}>{typeof m.b_value === 'number' ? m.b_value.toFixed(4) : String(m.b_value ?? '-')}</td>
                              <td style={{ padding:'6px 8', textAlign:'center', fontFamily:'monospace', fontWeight:600,
                                  color: delta == null ? 'var(--muted)' : better ? 'var(--green)' : worse ? 'var(--red)' : 'var(--muted)'
                              }}>
                                {delta != null ? `${delta >= 0 ? '+' : ''}${delta.toFixed(4)}` : '-'}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Parameter differences */}
                {paramArr.length > 0 && (
                  <div style={{ marginBottom: 16 }}>
                    <h4 style={{ margin:'0 0 8px', fontSize:13, color:'var(--muted)' }}>Parameter Differences</h4>
                    <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                      <thead>
                        <tr style={{ borderBottom:'1px solid var(--border)' }}>
                          <th style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)' }}>Parameter</th>
                          <th style={{ padding:'6px 8px', textAlign:'right', color:'var(--blue)' }}>{comparison.first.strategy||'A'}</th>
                          <th style={{ padding:'6px 8px', textAlign:'right', color:'var(--amber)' }}>{comparison.second.strategy||'B'}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {paramArr.map(p => (
                          <tr key={p.param} style={{ borderBottom:'1px solid var(--border)' }}>
                            <td style={{ padding:'6px 8', fontFamily:'monospace' }}>{p.param}</td>
                            <td style={{ padding:'6px 8', textAlign:'right', fontFamily:'monospace' }}>{typeof p.a_value === 'object' ? JSON.stringify(p.a_value) : String(p.a_value ?? '-')}</td>
                            <td style={{ padding:'6px 8', textAlign:'right', fontFamily:'monospace', fontWeight: String(p.a_value) !== String(p.b_value) ? 600 : 400, color: String(p.a_value) !== String(p.b_value) ? 'var(--blue)' : 'var(--text)' }}>
                              {typeof p.b_value === 'object' ? JSON.stringify(p.b_value) : String(p.b_value ?? '-')}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* Out-of-Sample (OOS) results */}
                {(comparison.first.oos_result || comparison.second.oos_result) && (
                  <div style={{ marginBottom: 16 }}>
                    <h4 style={{ margin:'0 0 8px', fontSize:13, color:'var(--muted)' }}>Out-of-Sample (OOS)</h4>
                    <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                      <thead>
                        <tr style={{ borderBottom:'1px solid var(--border)' }}>
                          <th style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)' }}>Run</th>
                          <th style={{ padding:'6px 8px', textAlign:'right', color:'var(--muted)' }}>Sharpe</th>
                          <th style={{ padding:'6px 8px', textAlign:'right', color:'var(--muted)' }}>Return</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[{ label: comparison.first.strategy||'A', data: comparison.first.oos_result }, { label: comparison.second.strategy||'B', data: comparison.second.oos_result }].map((row, i) => (
                          <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                            <td style={{ padding:'6px 8', fontFamily:'monospace' }}>{row.label}</td>
                            <td style={{ padding:'6px 8', textAlign:'right', fontFamily:'monospace', color: row.data?.sharpe != null ? (row.data.sharpe >= 0 ? 'var(--green)' : 'var(--red)' ) : 'var(--muted)' }}>
                              {row.data?.sharpe != null ? row.data.sharpe.toFixed(4) : '-'}
                            </td>
                            <td style={{ padding:'6px 8', textAlign:'right', fontFamily:'monospace', color: row.data?.total_return != null ? (row.data.total_return >= 0 ? 'var(--green)' : 'var(--red)' ) : 'var(--muted)' }}>
                              {row.data?.total_return != null ? `$${(row.data.total_return * 100).toFixed(2)}` : '-'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {/* WalkForward results */}
                {(comparison.first.wf_result || comparison.second.wf_result) && (
                  <div>
                    <h4 style={{ margin:'0 0 8px', fontSize:13, color:'var(--muted)' }}>Walk-Forward Analysis</h4>
                    <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                      <thead>
                        <tr style={{ borderBottom:'1px solid var(--border)' }}>
                          <th style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)' }}>Run</th>
                          <th style={{ padding:'6px 8px', textAlign:'right', color:'var(--muted)' }}>Best WF Sharpe</th>
                          <th style={{ padding:'6px 8px', textAlign:'right', color:'var(--muted)' }}>Avg Window Sharpe</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[{ label: comparison.first.strategy||'A', data: comparison.first.wf_result }, { label: comparison.second.strategy||'B', data: comparison.second.wf_result }].map((row, i) => (
                          <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                            <td style={{ padding:'6px 8', fontFamily:'monospace' }}>{row.label}</td>
                            <td style={{ padding:'6px 8', textAlign:'right', fontFamily:'monospace', color: row.data?.best_sharpe != null ? (row.data.best_sharpe >= 0 ? 'var(--green)' : 'var(--red)' ) : 'var(--muted)' }}>
                              {row.data?.best_sharpe != null ? row.data.best_sharpe.toFixed(4) : '-'}
                            </td>
                            <td style={{ padding:'6px 8', textAlign:'right', fontFamily:'monospace', color: row.data?.avg_sharpe != null ? (row.data.avg_sharpe >= 0 ? 'var(--green)' : 'var(--red)' ) : 'var(--muted)' }}>
                              {row.data?.avg_sharpe != null ? row.data.avg_sharpe.toFixed(4) : '-'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Panel>
            );
          })()}

          {/* Compare on Charts — renders price charts with trade markers for both runs side-by-side */}
          {comparison && (
            <div style={{ marginTop: 12 }}>
              <Button onClick={() => setShowChartComparison(true)} style={{ background: 'rgba(139,92,246,0.15)', color: '#8b5cf6', border: '1px solid #8b5cf6' }}>
                📊 Compare on Charts
              </Button>
            </div>
          )}

          {error && <div style={{ padding: 8, background: 'var(--red)', color: '#fff', borderRadius: 6, fontSize: 13 }}>{error}</div>}
        </>
      )}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
/*  TAB 3 — INTERACT                                             */
/* ═══════════════════════════════════════════════════════════════ */
function InteractTab() {
  const [watchlist, setWatchlist] = useState([]);
  const [symbol, setSymbol] = useState('');
  const [days, setDays] = useState(30);
  const [windowSize, setWindowSize] = useState(10);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchWatchlistForSimulation().then(w => { const wArr = Array.isArray(w) ? w : (w?.symbols ?? []); setWatchlist(wArr); }).catch(() => {});
    setLoading(false);
  }, []);

  const runInteract = useCallback(async () => {
    if (!symbol) return;
    setRunning(true); setError(''); setResult(null);
    try {
      const res = await runInteranalysis({ symbol, days, window_size: windowSize });
      setResult(res);
    } catch (e) { setError(e.message); }
    setRunning(false);
  }, [symbol, days, windowSize]);

  if (loading) return <LoadingText />;

  const sev = result?.severity || 'none';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Collapsed info box at top */}
      <details style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <summary style={{ padding: '8px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600, color: 'var(--blue)', background: 'rgba(59,130,246,0.06)' }}>
          ℹ️ Interaction Analysis — Strategy Conflict Detection ▼
        </summary>
        <div style={{ padding: '12px 16px', fontSize: 12, lineHeight: 1.65, color: 'var(--muted)' }}>
          Detects when two or more strategies in your group produce conflicting signals (e.g. one says BUY,
          another SELL) on the same symbol within a given window. High-conflict pairs may dilute your edge.
          Run this before deploying multiple strategies on the same asset to spot overlaps early.
        </div>
      </details>

      {/* Asset filter bar */}
      {watchlist.length > 0 && (
        <AssetFilterBar assets={watchlist} selectedAsset={symbol || ''} onSelectAsset={(v) => setSymbol(v)} onClear={() => setSymbol('')} />
      )}

      {/* Symbol selector */}
      <Select value={symbol} onChange={setSymbol} options={watchlist.map(s => ({ value: s, label: s }))} label="Symbol" searchable />

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <TextInput value={days} onChange={setDays} label="Days" type="number" small />
        <div>
          <label style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 4, display: 'block' }}>Analysis Window</label>
          <Slider value={windowSize} onChange={setWindowSize} min={2} max={50} step={1} />
        </div>
      </div>

      <Button onClick={runInteract} primary disabled={running || !symbol}>
        {running ? 'Analyzing…' : 'Run Interaction Analysis'}
      </Button>

      {!result && !running && !error && (
        <EmptyState message="Select a symbol and click Run to analyze strategy interactions." />
      )}

      {result && (
        <>
          <Panel title={`Interaction Results — ${result.symbol || symbol}`}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
              <span style={{ fontSize: 13 }}>Total Conflicts:</span>
              <Badge label={String(result.total_conflicts ?? '-')} color="var(--text)" />
              <span style={{ marginLeft: 8 }}>Severity:</span>
              <Badge label={(result.severity || 'none').toUpperCase()} color={SEVERITY_COLORS[result.severity] || 'var(--muted)'} />
            </div>

            {result.top_conflicts?.length > 0 && (
              <Panel title="Top Conflicting Pairs">
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      {['#','Strategy A','Strategy B','Conflict Score','Impact'].map(h => (
                        <th key={h} style={{ padding:'6px 10', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.top_conflicts.map((c, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding:'6px 10', color:'var(--muted)' }}>{i + 1}</td>
                        <td style={{ padding:'6px 10' }}>{c.strategy_a || '-'}</td>
                        <td style={{ padding:'6px 10' }}>{c.strategy_b || '-'}</td>
                        <td style={{ padding:'6px 10', fontWeight:600, color: c.score >= 0.7 ? 'var(--red)' : c.score >= 0.4 ? 'var(--amber)' : 'var(--green)' }}>
                          {c.score != null ? (c.score * 100).toFixed(1) + '%' : '-'}
                        </td>
                        <td style={{ padding:'6px 10' }}>{c.impact || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>
            )}

            {result.message && (
              <div style={{ marginTop: 12, fontSize: 13, color: 'var(--muted)' }}>{result.message}</div>
            )}
          </Panel>
        </>
      )}

      {error && <div style={{ padding: 8, background: 'var(--red)', color: '#fff', borderRadius: 6, fontSize: 13 }}>{error}</div>}
    </div>
  );
}
/* ═══════════════════════════════════════════════════════════════ */
/*  TAB 4 — INJECT                                               */
/* ═══════════════════════════════════════════════════════════════ */

/** All registered strategy groups with their component strategies. */
const ALL_GROUPS = {
  momentum_1: ['SMA_Crossover', 'MACD_Volume'],
  mean_reversion_1: ['RSI_MeanReversion', 'Bollinger_Squeeze'],
  breakout_1: ['ORB', 'Donchian_Breakout'],
  pullback_1: ['RSI_Pullback'],
  vwap_mean_rev: ['VWAP_Reversion'],
};

/** Invert ALL_GROUPS → individual strategy name → group key (for lookup). */
const STRATEGY_TO_GROUP = (() => {
  const map = {};
  for (const [grp, members] of Object.entries(ALL_GROUPS)) {
    for (const m of members) {
      if (!map[m]) map[m] = [];
      map[m].push(grp);
    }
  }
  return map;
})();

/** Get all individual strategy names across all groups. */
const ALL_INDIVIDUAL_STRATS = (() => {
  const set = new Set();
  for (const members of Object.values(ALL_GROUPS)) {
    for (const m of members) set.add(m);
  }
  return Array.from(set).sort();
})();

/* ═══════════════════════════════════════════════════════════════ */
/*  TAB — VISUAL TUNER + RANKING (Task 5)                        */
/* ═══════════════════════════════════════════════════════════════ */

function RankView({ symbol, strategies, onLoad }) {
  const [rankResult, setRankResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const runRank = useCallback(async () => {
    if (!symbol) return;
    setLoading(true); setError('');
    try {
      const res = await runRanking(symbol, { days: 365 });
      setRankResult(res);
      onLoad && onLoad(res);
    } catch (e) { setError(e.message); }
    setLoading(false);
  }, [symbol]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {!rankResult && !loading && (
        <Button onClick={runRank} primary disabled={!symbol}>Run Ranking for {symbol || 'selected asset'}</Button>
      )}

      {loading && <span style={{ color:'var(--blue)' }}>Ranking strategies…</span>}

      {error && <div style={{ padding: 8, background: 'var(--red)', color: '#fff', borderRadius: 6, fontSize: 13 }}>{error}</div>}

      {rankResult && (
        <>
          {/* Leaderboard */}
          {rankResult.leaderboard && rankResult.leaderboard.length > 0 && (
            <Panel title={`Leaderboard (${rankResult.strategies_count || rankResult.leaderboard.length} strategies)`}>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse', fontSize:13 }}>
                  <thead>
                    <tr style={{ borderBottom:'1px solid var(--border)' }}>
                      {['Rank','Strategy','Sharpe','Win Rate','Return','Max DD','Trades','Score'].map(h => (
                        <th key={h} style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rankResult.leaderboard.map((row, i) => (
                      <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                        <td style={{ padding:'6px 8' }}>{i+1}</td>
                        <td style={{ padding:'6px 8', fontWeight:600 }}>{row.strategy || '-'}</td>
                        <td style={{ padding:'6px 8', color:(row.sharpe||0)>=0?'var(--green)':'var(--red)' }}>
                          {row.sharpe != null ? row.sharpe.toFixed(2) : '-'}
                        </td>
                        <td style={{ padding:'6px 8' }}>{pctDisplay(row.win_rate)}</td>
                        <td style={{ padding:'6px 8', color:(row.total_return||0)>=0?'var(--green)':'var(--red)' }}>
                          {typeof row.total_return === 'number' ? (row.total_return >= 0 ? '+' : '') + (row.total_return * 100).toFixed(2) + '%' : '-'}
                        </td>
                        <td style={{ padding:'6px 8', color:'var(--red)' }}>
                          {row.max_drawdown != null ? (row.max_drawdown * 100).toFixed(2) + '%' : '-'}
                        </td>
                        <td style={{ padding:'6px 8' }}>{row.total_trades ?? '-'}</td>
                        <td style={{ padding:'6px 8', color:'var(--blue)' }}>
                          {row.score != null ? row.score.toFixed(2) : '-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}

          {/* Conflict pairs */}
          {rankResult.conflicts && rankResult.conflicts.length > 0 && (
            <Panel title={`Conflicts (${rankResult.conflict_count || rankResult.conflicts.length} pairs)`}>
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                  <thead>
                    <tr style={{ borderBottom:'1px solid var(--border)' }}>
                      {['Pair','Conflict Type','Score','Severity','Recommendation'].map(h => (
                        <th key={h} style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rankResult.conflicts.map((c, i) => {
                      const sev = c.severity || 'low';
                      return (
                        <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                          <td style={{ padding:'6px 8' }}>{c.strategy_a || '-'} / {c.strategy_b || '-'}</td>
                          <td style={{ padding:'6px 8', color:'var(--muted)' }}>{c.type || c.conflict_type || 'DIRECT_CONFLICT'}</td>
                          <td style={{ padding:'6px 8', fontWeight:600, color:c.score>=0.7?'var(--red)':c.score>=0.4?'var(--amber)':'var(--green)' }}>
                            {c.score != null ? (c.score * 100).toFixed(1) + '%' : '-'}
                          </td>
                          <td style={{ padding:'6px 8' }}>
                            <Badge label={sev.toUpperCase()} color={SEVERITY_COLORS[sev] || 'var(--muted)'}/>
                          </td>
                          <td style={{ padding:'6px 8', color:'var(--muted)' }}>{c.recommendation || '-'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Verdict */}
              {rankResult.verdict && (
                <div style={{ marginTop:12, padding:'10px 14px', background:'rgba(59,130,246,0.1)', borderRadius:6, fontSize:13 }}>
                  <b>Verdict:</b>{' '}{rankResult.verdict}
                </div>
              )}
            </Panel>
          )}

          {/* No conflict badge */}
          {rankResult.conflicts && rankResult.conflicts.length === 0 && (
            <Panel title="Conflicts">
              <EmptyState message="No significant conflicts detected between strategies on this asset." />
            </Panel>
          )}
        </>
      )}
    </div>
  );
}

function TuneVisualTab() {
  const { config, setConfig } = useSimConfig();

  // Initialize local state from shared context on mount/tab-switch
  const [watchlist, setWatchlist] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [symbol, setSymbol] = useState(config.symbol);
  const [strategy, setStrategy] = useState(config.strategy);
  const [assetFilter, setAssetFilter] = useState('');
  const [loading, setLoading] = useState(true);

  /* ── Sync shared config for Visual tab fields ── */
  const syncConfigVisual = useCallback((field, value) => {
    setConfig(prev => ({ ...prev, [field]: value }));
  }, [setConfig]);

  /* ── Visual Tuner State ── */
  const [tuning, setTuning] = useState(false);
  const [runId, setRunId] = useState(null);
  const [leaderboard, setLeaderboard] = useState([]);
  // Price chart data — populated from the best trial's trades
const [tradePoints, setTradePoints] = useState([]);


  const [error, setError] = useState('');
  const [tradesData, setTradesData] = useState([]);
  const [bestTradesCount, setBestTradesCount] = useState(0);

  /* ── Ranking State ── */
  const [rankingResult, setRankingResult] = useState(null);
  const [showRankView, setShowRankView] = useState(false);

  /* ── Params overlay ── */
  const [selectedTrial, setSelectedTrial] = useState(null);

  /* ── Tuning config ── */
  const [tuneDays, setTuneDays] = useState(config.tuneDays > 0 ? config.tuneDays : 90);
  const [tuneTrials, setTuneTrials] = useState(config.tuneTrials > 0 ? config.tuneTrials : 10);
  const [tuneTimeframe, setTuneTimeframe] = useState('auto');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [w, s] = await Promise.all([
          fetchWatchlistForSimulation().catch(() => []),
          fetchAvailableStrategies().catch(() => []),
        ]);
        const wArr = Array.isArray(w) ? w : (w?.symbols ?? []);
        const sArr = Array.isArray(s)
          ? (s.length > 0 && typeof s[0] === 'string' ? s : s.map(x => x.name))
          : (s?.strategies ?? []).map(x => x.name);
        
        if (!cancelled) { 
          setWatchlist(wArr); 
          setStrategies(sArr); 
          // Auto-select first strategy if none is selected yet
          if (!strategy && sArr.length > 0) setStrategy(typeof sArr[0] === 'string' ? sArr[0] : (sArr[0]?.name || ''));
          setLoading(false); 
        }
      } catch (e) { setError(e.message); setLoading(false); }
    })();
    return () => { cancelled = true; };
  }, []);

  // Clear stale result state when starting a new run
  const clearResultState = useCallback(() => {
    setLeaderboard([]);
    setSelectedTrial(null);
    
    localStorage.removeItem('vt_run_id');
  }, []);

  const startVisualTuner = useCallback(async () => {
    if (!symbol || !strategy) { setError('Select symbol and strategy'); return; }
    clearResultState();
    setTuning(true); setError('');
    try {
      const res = await runVisualTuner(symbol, strategy, {
        days: tuneDays,
        trials: tuneTrials,
        timeframe: tuneTimeframe,
      });
      setRunId(res.run_id);
    } catch (e) { setError(e.message); setTuning(false); }
  }, [symbol, strategy, tuneDays, tuneTrials, tuneTimeframe, clearResultState]);

    const visualProgress = usePollProgress(runId, tuneTrials, true);

  // Refresh on completion (no separate loadAllRuns for Visual)
  useEffect(() => {
    if (visualProgress && (visualProgress.status === 'completed' || visualProgress.status === 'error')) {
      setTuning(false);
      if (visualProgress.status === 'error') setError(visualProgress.error || 'Tuning failed');
    }
  }, [visualProgress?.status]);

  // Extract data from unified parser result
  useEffect(() => {
    if (!visualProgress) return;
        if (visualProgress.leaderboard.length && visualProgress.leaderboard !== leaderboard) setLeaderboard(visualProgress.leaderboard);
    if (visualProgress.trades.length && JSON.stringify(visualProgress.trades) !== JSON.stringify(tradesData)) {
      setTradesData(visualProgress.trades);
      setBestTradesCount(visualProgress.best_trades_count);
    }
    // Build price chart from the best trial's trades
    if (leaderboard.length > 0 && visualProgress.status === 'completed') {
      const best = leaderboard[0];
      const trades = best.trades || [];
      // Sort trades by timestamp
      trades.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
      setTradePoints(trades.map(t => ({
        time: t.timestamp,
        price: t.price,
        type: t.type, // BUY, SELL, TP, etc.
        label: t.reason || t.type,
        color: t.type === 'BUY' ? 'var(--green)' : (t.type === 'SELL' || t.type.startsWith('TP') ? 'var(--red)' : '#ff9800'),
      })));
    }
  }, [visualProgress]);

  if (loading) return <LoadingText />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Collapsed info box */}
      <details style={{ background:'var(--surface)', border:'1px solid var(--border)', borderRadius:8, overflow:'hidden' }}>
        <summary style={{ padding:'8px 14px', cursor:'pointer', fontSize:13, fontWeight:600, color:'var(--blue)', background:'rgba(59,130,246,0.06)' }}>
          ℹ️ Visual Tuner — Closed-Loop Optimization ▼
        </summary>
        <div style={{ padding:'12px 16px', fontSize:12, lineHeight:1.65, color:'var(--muted)' }}>
          Runs a closed-loop tuner that watches parameters converge on price charts.
          Uses Bayesian-inspired search over parameter space, renders live price overlays
          with buy/sell markers + TP/SL levels, and streams progress back to the dashboard.
        </div>
      </details>

      {/* Asset filter bar */}
      {watchlist.length > 0 && (
        <AssetFilterBar assets={watchlist} selectedAsset={assetFilter || ''} onSelectAsset={(v) => { setAssetFilter(v); if (v) { setSymbol(v); syncConfigVisual('symbol', v); } }} onClear={() => { setAssetFilter(''); setSymbol(''); syncConfigVisual('symbol', ''); }} />
      )}

      {/* Selectors */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))', gap:12 }}>
        <Select value={symbol} onChange={(v) => { setSymbol(v); syncConfigVisual('symbol', v); }} options={watchlist.map(s => ({value:s,label:s}))} label="Symbol" searchable />
        <Select value={strategy} onChange={(v) => { setStrategy(v); syncConfigVisual('strategy', v); }} options={strategies.map(s => ({value:s,label:s}))} label="Strategy" />
      </div>

      {/* Tuning config */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))', gap:12 }}>
        <div>
          <label style={{ fontSize:11, color:'var(--muted)', marginBottom:4, display:'block' }}>Lookback Days</label>
          <Slider value={tuneDays} onChange={(v) => { setTuneDays(v); syncConfigVisual('days', v); }} min={7} max={365} step={1} />
        </div>
        <TextInput value={tuneTrials} onChange={(v) => { setTuneTrials(Number(v) || 0); syncConfigVisual('trials', Number(v) || 0); }} label="Number of Tests" type="number" small />
        <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
          <label style={{ fontSize:11, color:'var(--muted)', marginBottom:2, display:'block' }}>Timeframe</label>
          <Select value={tuneTimeframe} onChange={(v) => { setTuneTimeframe(v); syncConfigVisual('timeframe', v); }}
          options={[
            { value: 'auto', label: 'Auto (default)' },
            { value: '5m', label: '5 min' },
            { value: '15m', label: '15 min' },
            { value: '30m', label: '30 min' },
            { value: '1h', label: '1 hour' },
            { value: '4h', label: '4 hours' },
          ]}
          style={{ flex: 1 }} />
        </div>
      </div>

      {/* Run tuning */}
      <Button onClick={startVisualTuner} primary disabled={tuning || !symbol || !strategy}>
        {tuning ? 'Tuning…' : 'Start Visual Tuning'}
      </Button>

      {/* Progress */}
      {tuning && (
        <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
          <Panel title={`Progress${runId ? ` · ${runId.slice(0,24)}…` : ''}`}>
            <ProgressBar pct={visualProgress?.pct ?? 0} height={10} />
            <div style={{ fontSize:12, color:'var(--muted)', marginTop:6 }}>{visualProgress?.completed ?? 0} / {tuneTrials} trials complete ({visualProgress?.pct ?? 0}%)</div>
          </Panel>
        </div>
      )}

      {/* Leaderboard */}
      {leaderboard.length > 0 && (
        <>
          <Panel title="Tuning Results">
            <div style={{ overflowX:'auto' }}>
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:13 }}>
                <thead>
                  <tr style={{ borderBottom:'1px solid var(--border)' }}>
                    {['Rank','Trial ID','Sharpe','Win Rate','Return','Max DD','Score'].map(h => (
                      <th key={h} style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.map((row, i) => (
                    <tr key={row.trial_id || i} style={{ borderBottom:'1px solid var(--border)', cursor:'pointer', background:selectedTrial === (row.trial_id||i) ? 'rgba(59,130,246,0.1)' : 'transparent' }}
                        onClick={() => setSelectedTrial(row.trial_id || i)}>
                      <td style={{ padding:'6px 8' }}>{i+1}</td>
                      <td style={{ padding:'6px 8', fontFamily:'monospace', fontSize:11 }}>{String(row.trial_id||'').slice(0,24)}</td>
                      <td style={{ padding:'6px 8', color:(row.sharpe||0)>=0?'var(--green)':'var(--red)' }}>{row.sharpe != null ? row.sharpe.toFixed(2) : '-'}</td>
                      <td style={{ padding:'6px 8' }}>{pctDisplay(row.win_rate)}</td>
                      <td style={{ padding:'6px 8', color:(row.total_return||0)>=0?'var(--green)':'var(--red)' }}>
                        {typeof row.total_return === 'number' ? (row.total_return >= 0 ? '+' : '') + (row.total_return * 100).toFixed(2) + '%' : '-'}
                      </td>
                      <td style={{ padding:'6px 8', color:'var(--red)' }}>{row.max_drawdown != null ? (row.max_drawdown*100).toFixed(2)+'%' : '-'}</td>
                      <td style={{ padding:'6px 8', color:'var(--blue)' }}>{row.score != null ? row.score.toFixed(2) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

          {/* Charts — price overlay + equity curve via recharts */}
          {leaderboard.length > 0 && (
            <Panel title="Charts">
              <div style={{ marginBottom:12 }}>
                <div style={{ fontSize:12, color:'var(--muted)', marginBottom:6 }}>
                  Price + Trade Markers — {(leaderboard[0]?.timeframe || '').replace('m','min') || '—'}
                </div>
              </div>

              {/* Equity curve chart */}
              <div style={{ background:'var(--bg)', borderRadius:8, border:'1px solid var(--border)', padding:12, marginBottom:12 }}>
                <div style={{ fontSize:13, fontWeight:600, color:'var(--blue)', marginBottom:8 }}>Equity Curve (Best Config)</div>
                {(() => {
                  const best = leaderboard[0];
                  if (!best) return null;
                  const trades = (best.trades || []).sort((a,b) => new Date(a.timestamp) - new Date(b.timestamp));
                  const initialCash = 10000;
                  const points = trades.map(t => ({
                    time: t.timestamp,
                    equity: initialCash + (t.net_pnl || t.pnl || 0),
                  }));
                  if (points.length > 0) points.unshift({ time: trades[0].timestamp, equity: initialCash });
                  if (!points.length) return <EmptyState message="No equity data available" />;
                  const lastEq = points[points.length - 1]?.equity ?? initialCash;
                  const eqColor = lastEq >= initialCash ? 'var(--green)' : 'var(--red)';
                  // Data-driven Y axis: compress range to data +/- 5% padding
                  const allEquities = points.map(p => p.equity);
                  const yMin = Math.min(...allEquities, initialCash);
                  const yMax = Math.max(...allEquities, initialCash);
                  const yPad = (yMax - yMin) * 0.1 || 100; // at least $100 padding
                  const yAxisMin = Math.floor((yMin - yPad) / 100) * 100;
                  const yAxisMax = Math.ceil((yMax + yPad) / 100) * 100;
                  return (
                    <ResponsiveContainer width="100%" height={280}>
                      <LineChart data={points} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis
                          dataKey="time"
                          tick={{ fontSize: 10, fill: 'var(--muted)' }}
                          tickFormatter={(v) => v.slice(5, 16)}
                          minTickGap={30}
                        />
                        <YAxis
                          domain={[yAxisMin, yAxisMax]}
                          tick={{ fontSize: 11, fill: 'var(--muted)' }}
                          tickFormatter={(v) => '$' + Math.round(v).toLocaleString()}
                          width={70}
                        />
                        <ReferenceLine y={initialCash} stroke="var(--muted)" strokeDasharray="3 3" />
                        <Tooltip
                          contentStyle={{ background:'var(--surface)',border:'1px solid var(--border)',borderRadius:8,fontSize:12 }}
                          labelFormatter={(v) => '📅 ' + v.slice(5,16)}
                          formatter={(value) => ['$' + Math.round(value).toLocaleString(), 'Equity']}
                        />
                        <Line
                          type="monotone"
                          dataKey="equity"
                          stroke={eqColor}
                          dot={false}
                          strokeWidth={2}
                          name="Equity"
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  );
                })()}
              </div>

              {/* Price + trade markers */}
              {tradePoints.length > 0 && (
                <div style={{ background:'var(--bg)', borderRadius:8, border:'1px solid var(--border)', padding:12 }}>
                  <div style={{ fontSize:13, fontWeight:600, color:'var(--blue)', marginBottom:8 }}>Price + Trade Markers</div>
                  <ResponsiveContainer width="100%" height={400}>
                    <LineChart data={tradePoints} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis
                        dataKey="time"
                        tick={{ fontSize: 10, fill: 'var(--muted)' }}
                        tickFormatter={(v) => {
                          // Parse ISO timestamp to format nicely
                          try {
                            const d = new Date(v);
                            const m = (d.getMonth()+1).toString().padStart(2,'0');
                            const day = d.getDate().toString().padStart(2,'0');
                            const h = d.getHours().toString().padStart(2,'0');
                            return m + '/' + day + ' ' + h + ':00';
                          } catch { return String(v).slice(5, 16); }
                        }}
                        minTickGap={30}
                      />
                      {(() => {
                        const prices = tradePoints.map(t => t.price);
                        if (prices.length === 0) return null;
                        const pMin = Math.min(...prices);
                        const pMax = Math.max(...prices);
                        const pPad = (pMax - pMin) * 0.1 || 10;
                        const yMin = Math.floor((pMin - pPad) / 50) * 50;
                        const yMax = Math.ceil((pMax + pPad) / 50) * 50;
                        return (
                          <>
                            <YAxis
                              domain={[yMin, yMax]}
                              tick={{ fontSize: 11, fill: 'var(--muted)' }}
                              tickFormatter={(v) => '$' + Number(v).toFixed(2)}
                              width={80}
                            />
                            <Tooltip
                              contentStyle={{ background:'var(--surface)',border:'1px solid var(--border)',borderRadius:8,fontSize:12 }}
                              labelFormatter={(v) => '📅 ' + v.slice(5,16)}
                              formatter={(value, name) => name === 'price' ? ['$' + Number(value).toFixed(2), 'Price'] : [value, name]}
                            />
                          </>
                        );
                      })()}
                      <Line
                        type="monotone"
                        dataKey="price"
                        stroke="#4caf50"
                        dot={false}
                        strokeWidth={1.5}
                        strokeDasharray="none"
                        name="Price"
                      />
                      {/* Trade markers */}
                      <Scatter data={tradePoints.map((tp, i) => ({ x: tp.time, y: tp.price, type: tp.type, color: tp.color, idx: i }))} fill="#4caf50">
                        {tradePoints.map((tp, i) => (
                          <Scatter key={i} name={tp.type} x={tp.time} y={tp.price} fill={tp.color} />
                        ))}
                      </Scatter>
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </Panel>
          )}

          {/* F3: Trade Details table */}
          {tradesData.length > 0 && (
            <Panel title={`Trade Details (${bestTradesCount} trades)`}>
              {/* Summary metrics */}
              {(() => {
                const wins = tradesData.filter(t => (t.profit || t.pnl || 0) > 0);
                const losses = tradesData.filter(t => (t.profit || t.pnl || 0) <= 0);
                const grossPnl = tradesData.reduce((s, t) => s + (t.profit || t.pnl || 0), 0);
                const bestTrade = tradesData.length > 0 ? Math.max(...tradesData.map(t => t.profit || t.pnl || 0)) : 0;
                const worstTrade = tradesData.length > 0 ? Math.min(...tradesData.map(t => t.profit || t.pnl || 0)) : 0;
                return (
                  <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(140px,1fr))', gap:8, marginBottom:12 }}>
                    <div style={{ fontSize:12 }}>
                      <span style={{ color:'var(--muted)' }}>Total Trades:</span>{' '}
                      <b>{tradesData.length}</b>
                    </div>
                    <div style={{ fontSize:12 }}>
                      <span style={{ color:'var(--muted)' }}>Wins/Losses:</span>{' '}
                      <span style={{ color:'var(--green)', fontWeight:600 }}>{wins.length}</span>/{' '}
                      <span style={{ color:'var(--red)', fontWeight:600 }}>{losses.length}</span>
                    </div>
                    <div style={{ fontSize:12 }}>
                      <span style={{ color:'var(--muted)' }}>Gross P&amp;L:</span>{' '}
                      <b style={{ color: grossPnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                        ${grossPnl.toFixed(2)}
                      </b>
                    </div>
                    <div style={{ fontSize:12 }}>
                      <span style={{ color:'var(--muted)' }}>Best Trade:</span>{' '}
                      <b style={{ color:'var(--green)' }}>${bestTrade.toFixed(2)}</b>
                    </div>
                    <div style={{ fontSize:12 }}>
                      <span style={{ color:'var(--muted)' }}>Worst Trade:</span>{' '}
                      <b style={{ color:'var(--red)' }}>${worstTrade.toFixed(2)}</b>
                    </div>
                  </div>
                );
              })()}

              {/* Trades table */}
              <div style={{ overflowX:'auto', maxHeight:400, overflowY:'auto' }}>
                <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                  <thead>
                    <tr style={{ borderBottom:'1px solid var(--border)', position:'sticky', top:0, background:'var(--surface)' }}>
                      {['#','Type','Price','Qty','Timestamp','P&amp;L','Reason'].map(h => (
                        <th key={h} style={{ padding:'6px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500, fontSize:11 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {tradesData.slice(0, 50).map((t, i) => {
                      const type = (t.type || '').toUpperCase();
                      const pnl = t.profit != null ? t.profit : (t.pnl != null ? t.pnl : 0);
                      const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--muted)';
                      const rowBg = type === 'BUY' ? 'rgba(76,175,80,0.05)' : 'rgba(239,83,80,0.05)';
                      return (
                        <tr key={i} style={{ borderBottom:'1px solid var(--border)', background: rowBg }}>
                          <td style={{ padding:'6px 8', color:'var(--muted)' }}>{i + 1}</td>
                          <td style={{ padding:'6px 8', fontWeight:600, color: type === 'BUY' ? 'var(--green)' : 'var(--red)' }}>
                            {type}
                          </td>
                          <td style={{ padding:'6px 8', fontFamily:'monospace' }}>${(t.price || t.entry_price || 0).toFixed(2)}</td>
                          <td style={{ padding:'6px 8', fontFamily:'monospace' }}>{t.qty || t.size || t.quantity || '-'}</td>
                          <td style={{ padding:'6px 8', color:'var(--muted)', fontSize:11 }}>
                            {t.timestamp ? formatEasternShort(t.timestamp) : '-'}
                          </td>
                          <td style={{ padding:'6px 8', fontFamily:'monospace', fontWeight:600, color: pnlColor }}>
                            {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                          </td>
                          <td style={{ padding:'6px 8', fontSize:11, color:'var(--muted)' }}>{t.reason || '-'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Panel>
          )}
        </>
      )}

      {/* Ranking section */}
      {leaderboard.length > 0 && !tuning && (
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          <Button onClick={() => setShowRankView(!showRankView)} primary>
            {showRankView ? 'Hide Rank View' : 'Rank This Asset'}
          </Button>
        </div>
      )}

      {/* Inline ranking view — appears after tuning */}
      {showRankView && leaderboard.length > 0 && !tuning && (
        <div style={{ marginTop:12 }}>
          <RankView symbol={symbol} strategies={strategies} onLoad={(res) => setRankingResult(res)} />
        </div>
      )}

      {error && <div style={{ padding:8, background:'var(--red)', color:'#fff', borderRadius:6, fontSize:13 }}>{error}</div>}
    </div>
  );
}

function InjectTab() {
  const { config } = useSimConfig();

  // Initialize from shared context
  const [watchlist, setWatchlist] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [selectedSymbol, setSelectedSymbol] = useState(config.symbol || '');
  const [assetFilter, setAssetFilter] = useState('');
  const [runResults, setRunResults] = useState([]);

  const [diffData, setDiffData] = useState(null);
  const [pushed, setPushed] = useState(false);
  const [phase, setPhase] = useState('review'); // review | diff | approve
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [docOpen, setDocOpen] = useState(false);

  // Strategy block state
  const [currentStrategies, setCurrentStrategies] = useState([]);     // individual strategy names currently on this asset
  const [proposedStrategies, setProposedStrategies] = useState([]);   // what will be injected
  const [activeStrategies, setActiveStrategies] = useState(new Set()); // active (not deactivated) strategies
  const [blockState, setBlockState] = useState(null);
  const [replaceAll, setReplaceAll] = useState(true);
  const [paramSections, setParamSections] = useState({});             // { strategyName: { paramKey: value } }
  const [selectedRunId, setSelectedRunId] = useState('');             // which tuning run we're injecting from
  const [injectTPLevels, setInjectTPLevels] = useState(null);
  const [injectTPSplit, setInjectTPSplit] = useState(null);
  const [injectTimeframe, setInjectTimeframe] = useState(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [w, s, data] = await Promise.all([
          fetchWatchlistForSimulation().catch(() => []),
          fetchAvailableStrategies().catch(() => []),
          listSimulationResults({}).catch(() => ({ results: [], runs: [] })),
        ]);
        const wArr = Array.isArray(w) ? w : (w?.symbols ?? []);
        const sArr = Array.isArray(s)
          ? (s.length > 0 && typeof s[0] === 'string' ? s : s.map(x => x.name))
          : (s?.strategies ?? []).map(x => x.name);
        
        if (!cancelled) {
          setWatchlist(wArr);
          setStrategies(sArr);
          const runs = data.results || data.runs || [];
          setRunResults(runs);
          setLoading(false);
        }
      } catch (e) { /* silently ignore */ setLoading(false); setError(e.message); }
    })();
    return () => { cancelled = true; };
  }, []);

  // Performance table: all runs for selected symbol, sorted by Sharpe desc
  const symbolRuns = selectedSymbol
    ? runResults.filter(r => String(r.symbol).toLowerCase() === selectedSymbol.toLowerCase())
    : [];
  const performanceTable = [...symbolRuns]
    .filter(r => r && (r.sharpe != null || r.total_return != null))
    .map(r => ({
      run_id: r.run_id || '',
      symbol: r.symbol || '-',
      strategy: r.strategy || '-',
      sharpe: r.sharpe ?? 0,
      win_rate: r.win_rate != null ? (Math.abs(r.win_rate) < 1 ? r.win_rate * 100 : r.win_rate) : 0,
      total_return: r.total_return ?? 0,
      max_drawdown: r.max_drawdown ?? null,
      created_at: r.created_at || r.timestamp_utc || '',
      tp_levels: r.tp_levels || null,
      tp_split: r.tp_split || null,
      timeframe: r.timeframe || null,
    }))
    .sort((a, b) => (b.sharpe || 0) - (a.sharpe || 0) || (b.total_return || 0) - (a.total_return || 0));

  /* ── Load TP config from a performance-table row click ─────────── */
  const handleRunClick = useCallback((run) => {
    if (!run.run_id) return;
    setSelectedRunId(run.run_id);
    setInjectTPLevels(run.tp_levels || null);
    setInjectTPSplit(run.tp_split || null);
    setInjectTimeframe(run.timeframe || null);
  }, []);

  /* ── Load params from API for each strategy in proposedStrategies ─────────── */
  const loadParamsForProposed = useCallback(async () => {
    if (!selectedSymbol || !proposedStrategies.length) return;
    const sections = {};
    for (const stratName of proposedStrategies) {
      try {
        const cfg = await getBestConfigForPush(selectedSymbol, stratName);
        if (cfg && cfg.params) {
          sections[stratName] = { ...cfg.params };
        }
        // Include TF/TP in the strategy's paramSection for review/edit
        if (cfg?.timeframe != null) {
          sections[stratName] = { ...sections[stratName], _timeframe: cfg.timeframe };
        }
        if (cfg?.tp_levels != null && Array.isArray(cfg.tp_levels)) {
          sections[stratName] = { ...sections[stratName], _tp_levels: JSON.stringify(cfg.tp_levels) };
        }
        if (cfg?.tp_split != null && Array.isArray(cfg.tp_split)) {
          sections[stratName] = { ...sections[stratName], _tp_split: JSON.stringify(cfg.tp_split) };
        }
      } catch { /* strategy not available for this symbol, skip */ }
    }
    setParamSections(sections);
  }, [selectedSymbol, proposedStrategies]);

  useEffect(() => { loadParamsForProposed(); }, [loadParamsForProposed]);

  /* ── Load current individual strategies from watchlist.yaml for this asset ─ */
  const refreshCurrentStrategies = useCallback(async () => {
    if (!selectedSymbol) {
      setCurrentStrategies([]);
      setActiveStrategies(new Set());
      // Don't clear proposedStrategies on symbol change — preserve user's selections
      return;
    }
    try {
      const details = await getWatchlistDetails();
      const asset = details?.[selectedSymbol];
      const groups = Array.isArray(asset?.strategies) ? asset.strategies : [];

      // Expand groups → individual strategy names, ALL checked by default
      const expanded = [];
      for (const g of groups) {
        const members = ALL_GROUPS[g] || [g];
        for (const s of members) {
          if (!expanded.includes(s)) expanded.push(s);
        }
      }
      setCurrentStrategies(expanded);
      // All current strategies are active by default
      setActiveStrategies(prev => {
        const merged = new Set(prev);
        for (const s of expanded) merged.add(s);
        return merged;
      });
      // Merge into proposedStrategies without overwriting user's explicit removals
      setProposedStrategies(prev => {
        const merged = new Set([...prev, ...expanded]);
        return Array.from(merged);
      });
    } catch {
      setCurrentStrategies([]);
      setActiveStrategies(new Set());
    }
  }, [selectedSymbol]);

  useEffect(() => { refreshCurrentStrategies(); }, [selectedSymbol, refreshCurrentStrategies]);

  // Toggle a strategy in "Current" list — uncheck to deactivate (grey out), keep in the box
  const toggleCurrentStrategy = (s) => {
    // Always ensure s is in currentStrategies
    setCurrentStrategies(prev => prev.includes(s) ? prev : [...prev, s]);
    // Toggle active status - this controls whether it shows as green or greyed out
    setActiveStrategies(prev => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s); else next.add(s);
      return next;
    });
    // Also keep in proposedStrategies so it stays visible in the green box
    setProposedStrategies(prev => prev.includes(s) ? prev : [...prev, s]);
  };

  // Toggle a strategy in the "Proposed" list — uncheck to remove it from proposed only
  const toggleProposedStrategy = (s) => {
    setProposedStrategies(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
  };

  // Resolve strategy_name from green box for backend calls
  const resolveStrategyName = () => {
    // Pick the first proposed strategy, look up its parent group
    for (const s of proposedStrategies) {
      const groups = STRATEGY_TO_GROUP[s] || [];
      if (groups.length > 0) return groups[0];
    }
    // Fallback: return first proposed strategy as-is
    return proposedStrategies[0] || '';
  };

  /* ── Review Diff & Push ─────────────────────────────────────── */
  const handleDiff = useCallback(async () => {
    if (!selectedSymbol) return;
    if (proposedStrategies.length === 0) { setError('No strategies selected to inject.'); return; }

    // Validate all parameters are populated
    const validation = validateParams();
    if (!validation.ok) {
      setError(validation.message);
      return;
    }

    try {
      setPhase('diff'); setError('');

      // Determine which groups will be removed (those in current but not in proposed)
      const removeGroups = [];
      for (const g of Object.keys(ALL_GROUPS)) {
        const members = ALL_GROUPS[g] || [g];
        const hasCurrentInGroup = members.some(m => currentStrategies.includes(m));
        const anyProposedInGroup = members.some(m => proposedStrategies.includes(m));
        if (hasCurrentInGroup && !anyProposedInGroup) {
          removeGroups.push(g);
        }
      }

      // DO NOT extract TF/TP as global fields — keep them scoped inside paramSections[stratName]
      // The backend detects nested params format and reads per-strategy meta keys directly
      const d = await pushDryRunDiff(
        selectedSymbol,
        paramSections,  // already nested {stratName: {...}}, backend detects is_nested
        removeGroups,
        replaceAll,
      );
      setDiffData(d);
      // Store state for display and subsequent push
      if (d.state?.new_strategy_list) {
        setBlockState(d.state);
        // NOTE: new_strategy_list contains group names from watchlist.yaml.
        // Do NOT replace proposedStrategies (which has individual strategy names)
        // with group names — that breaks paramSections key lookup in validateParams().
      }
    } catch (e) { setError(e.message); }
  }, [selectedSymbol, proposedStrategies, currentStrategies, paramSections, replaceAll]);

  const handleApprove = useCallback(async () => {
    if (!selectedSymbol) return;

    // Validate all parameters are populated
    const validation = validateParams();
    if (!validation.ok) {
      setError(validation.message);
      return;
    }

    try {
      setPhase('approve'); setError('');

      // Compute removed groups
      const removeGroups = [];
      for (const g of Object.keys(ALL_GROUPS)) {
        const members = ALL_GROUPS[g] || [g];
        const hasCurrentInGroup = members.some(m => currentStrategies.includes(m));
        const anyProposedInGroup = members.some(m => proposedStrategies.includes(m));
        if (hasCurrentInGroup && !anyProposedInGroup) {
          removeGroups.push(g);
        }
      }

      // DO NOT extract TF/TP as global fields — keep them scoped inside paramSections[stratName]
      // The backend detects nested params format and reads per-strategy meta keys directly
      await pushLiveConfig(selectedSymbol, paramSections, removeGroups, replaceAll);
      setPushed(true);

      // Reset screen after successful push
      setTimeout(() => {
        setPhase('review');
        setDiffData(null);
        setPushed(false);
        setCurrentStrategies([]);
        refreshCurrentStrategies();
      }, 1500);
    } catch (e) { setError(e.message); }
  }, [selectedSymbol, proposedStrategies, currentStrategies, paramSections, replaceAll]);

  const handleDecline = () => { setPhase('review'); setDiffData(null); };

  /* ── Validate all strategy parameters are populated (non-empty) ─────────── */
  const validateParams = useCallback(() => {
    for (const stratName of proposedStrategies) {
      const params = paramSections[stratName];
      if (!params || Object.keys(params).length === 0) {
        return { ok: false, message: `${stratName} has no parameters loaded. Select a tuning run or re-load the strategy.` };
      }
      for (const [k, v] of Object.entries(params)) {
        const val = typeof v === 'object' ? JSON.stringify(v) : String(v ?? '');
        if (!val || val.trim() === '' || val === '{}') {
          return { ok: false, message: `${stratName}.${k} is empty. Fill in all parameter values before pushing.` };
        }
      }
    }
    return { ok: true };
  }, [proposedStrategies, paramSections]);

  if (loading) return <LoadingText />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Collapsible Inject Instructions at top */}
      <details open={docOpen} onToggle={(e) => setDocOpen(e.target.open)} style={{ margin: '0 0 8px' }}>
        <summary style={{ cursor:'pointer', padding:'8px 12px', fontSize: 13, fontWeight: 600, color: 'var(--blue)', listStyle: 'none', display: 'flex', alignItems: 'center', gap: 8 }}>
          ℹ️ Inject Instructions {docOpen ? '▲' : '▼'}
        </summary>
        {docOpen && (
          <div style={{ background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8, padding: '12px 16px', fontSize: 12, lineHeight: 1.65, marginTop: -4 }}>
            Take tuned parameters and push them into your trading-system config (watchlist.yaml + strategies.yaml).
            Steps:
            <ol style={{ margin: '8px 0 0 18px', padding: 0 }}>
              <li>Select a symbol from the watchlist dropdown.</li>
              <li>Use Strategy Block Control below to pick which individual strategies apply.</li>
              <li>Review per-strategy parameters and the diff preview.</li>
              <li>Approve &amp; Push — changes are staged as a dry-run first, then committed live.</li>
            </ol>
          </div>
        )}
      </details>

      {/* Asset filter bar */}
      {watchlist.length > 0 && (
        <AssetFilterBar assets={watchlist} selectedAsset={assetFilter || ''} onSelectAsset={(v) => { setAssetFilter(v); if (v) setSelectedSymbol(v); }} onClear={() => { setAssetFilter(''); setSelectedSymbol(''); }} />
      )}

      {/* Symbol selector only — no Strategy dropdown */}
      <Select 
        value={selectedSymbol} 
        onChange={(v) => { 
          setSelectedSymbol(v); 
          setAssetFilter(v || '');
          refreshCurrentStrategies();
        }} 
        options={watchlist.map(s => ({ value: s, label: s }))} 
        label="Symbol" 
        searchable 
      />

      {/* Strategy Performance table (renamed from "Select Strategies to Inject") */}
      {performanceTable.length > 0 && (
        <Panel title="Strategy Performance">
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
            Reference — completed tuning runs sorted by Sharpe ratio.
            Click a row to load its TP config below.
          </div>
          <div style={{ overflowX: 'auto', maxHeight: 240, overflowY: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Strategy','Symbol','Sharpe','Win Rate','Total Return','Max DD','TP Levels','TP Split','Timeframe','Created'].map(h => (
                    <th key={h} style={{ padding:'4px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {performanceTable.map((run, i) => (
                  <tr key={i} style={{ borderBottom:'1px solid var(--border)', cursor:'pointer', background: selectedRunId === run.run_id ? 'rgba(59,130,246,0.08)' : 'transparent' }}
                      onClick={() => handleRunClick(run)}>
                    <td style={{ padding:'4px 8' }}>{run.strategy || '-'}</td>
                    <td style={{ padding:'4px 8', fontWeight:600 }}>{run.symbol || '-'}</td>
                    <td style={{ padding:'4px 8', color:(run.sharpe||0)>=0?'var(--green)':'var(--red)', fontWeight:600 }}>{run.sharpe != null ? run.sharpe.toFixed(2) : '-'}</td>
                    <td style={{ padding:'4px 8' }}>{pctDisplay(run.win_rate)}</td>
                    <td style={{ padding:'4px 8', color:(run.total_return||0)>=0?'var(--green)':'var(--red)' }}>{fmtMoney(typeof run.total_return === 'number' && !isNaN(run.total_return) ? run.total_return * 100 : null)}</td>
                    <td style={{ padding:'4px 8', color:'var(--red)' }}>{run.max_drawdown != null ? `${(run.max_drawdown * 100).toFixed(2)}%` : '-'}</td>
                    <td style={{ padding:'4px 8', fontSize:11, color:'var(--muted)' }}>{run.tp_levels ? run.tp_levels.map(v => (typeof v === 'number' ? (v * 100).toFixed(2) + '%' : String(v))).join(', ') : '-'}</td>
                    <td style={{ padding:'4px 8', fontSize:11, color:'var(--muted)' }}>{run.tp_split ? run.tp_split.join(', ') : '-'}</td>
                    <td style={{ padding:'4px 8', fontSize:11, color:'var(--muted)' }}>{run.timeframe || '-'}</td>
                    <td style={{ padding:'4px 8', color:'var(--muted)', fontSize:11 }}>{run.created_at ? new Date(run.created_at).toLocaleDateString() : '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}

      {/* Tuned TP Config display — shows when a run row is clicked */}
      {selectedRunId && (injectTPLevels || injectTPSplit || injectTimeframe) && phase !== 'approve' && (
        <Panel title={`Tuned TP Config — Run ${selectedRunId.slice(0, 12)}...`} >
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, fontSize: 13 }}>
            <div>
              <span style={{ color: 'var(--muted)', fontSize: 11 }}>TP Levels (pct)</span>
              <div style={{ fontWeight: 600, fontFamily: 'monospace', marginTop: 2 }}>
                {injectTPLevels ? injectTPLevels.map(v => (typeof v === 'number' ? (v * 100).toFixed(2) + '%' : String(v))).join(', ') : '-'}
              </div>
            </div>
            <div>
              <span style={{ color: 'var(--muted)', fontSize: 11 }}>TP Splits</span>
              <div style={{ fontWeight: 600, fontFamily: 'monospace', marginTop: 2 }}>
                {injectTPSplit ? injectTPSplit.join(', ') : '-'}
              </div>
            </div>
            <div>
              <span style={{ color: 'var(--muted)', fontSize: 11 }}>Timeframe</span>
              <div style={{ fontWeight: 600, fontFamily: 'monospace', marginTop: 2 }}>{injectTimeframe || '-'}</div>
            </div>
          </div>
          <div style={{ fontSize: 11, color: 'var(--amber)', marginTop: 8 }}>
            ⚡ This TP config will be written to watchlist.yaml as strategy_configs when you push.
          </div>
        </Panel>
      )}

      {/* Strategy block control — visible in review and diff phases */}
      {(phase === 'review' || phase === 'diff') && (
        <Panel title="Strategy Block Control">
          <div style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 8 }}>
            Manage which strategies apply to {selectedSymbol || 'this asset'}. All current strategies are checked by default.
          </div>

          {/* Replace all toggle */}
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={replaceAll} onChange={e => { setReplaceAll(e.target.checked); setCurrentStrategies([]); }} style={{ accentColor: 'var(--red)' }} />
            <span>Replace all existing strategies with only the injected one</span>
          </label>

          {/* Current strategies on this asset — READ from watchlist.yaml, ALL checked by default */}
          {!replaceAll && (
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--amber)', marginBottom: 6 }}>
                📋 Current Strategies on {selectedSymbol || ''}
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>
                All are checked. Uncheck any to remove it from this asset.
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {currentStrategies.length > 0 ? (
                    currentStrategies.map(s => {
                    const active = activeStrategies.has(s);
                    return (
                      <label key={s} style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '4px 12px', borderRadius: 6,
                        background: active ? 'rgba(255,193,7,0.08)' : 'rgba(128,128,128,0.08)',
                        border: `1px solid ${active ? 'var(--amber)' : 'var(--muted)'}`,
                        cursor: 'pointer', fontSize: 12,
                        opacity: active ? 1 : 0.5,
                      }}>
                        <input type="checkbox" checked={active} onChange={() => {
                          setActiveStrategies(prev => {
                            const next = new Set(prev);
                            if (next.has(s)) next.delete(s); else next.add(s);
                            return next;
                          });
                          setProposedStrategies(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
                        }} onClick={e => e.stopPropagation()} style={{ accentColor: active ? 'var(--amber)' : 'var(--muted)' }} />
                        <span style={{ fontWeight: 600, opacity: active ? 1 : 0.5 }}>{s}</span>
                      </label>
                    );
                  })
                ) : (
                  <span style={{ color: 'var(--muted)', fontStyle: 'italic', fontSize: 12 }}>No strategies configured on this asset.</span>
                )}
              </div>
            </div>
          )}

          {/* Add new individual strategies — list each strategy name directly, not groups */}
          {!replaceAll && (
            <div style={{ marginBottom: 14 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--blue)', marginBottom: 6 }}>
                ➕ Add Individual Strategies
              </div>
              <div style={{ fontSize: 11, color: 'var(--muted)', marginBottom: 8 }}>
                Select individual strategies to add. Each strategy is independent — no grouping.
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {ALL_INDIVIDUAL_STRATS.map(strat => {
                  const isChecked = proposedStrategies.includes(strat);
                  const parentGroups = STRATEGY_TO_GROUP[strat] || [];
                  return (
                    <label key={strat} style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      padding: '4px 12px', borderRadius: 6,
                      background: isChecked ? 'rgba(34,197,94,0.1)' : 'var(--bg)',
                      border: `1px solid ${isChecked ? 'var(--green)' : 'var(--border)'}`,
                      cursor: 'pointer', fontSize: 12,
                    }}>
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => {
                          if (isChecked) {
                            setProposedStrategies(prev => prev.filter(x => x !== strat));
                            setActiveStrategies(prev => { const n = new Set(prev); n.delete(strat); return n; });
                          } else {
                            setProposedStrategies(prev => [...prev, strat]);
                            setActiveStrategies(prev => new Set([...prev, strat]));
                          }
                        }}
                        onClick={e => e.stopPropagation()}
                        style={{ accentColor: isChecked ? 'var(--green)' : 'var(--blue)' }}
                      />
                      <span style={{ fontWeight: 600 }}>{strat}</span>
                      {parentGroups.length > 0 && (
                        <span style={{ color: 'var(--muted)', fontSize: 11 }}>({parentGroups.join(', ')})</span>
                      )}
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          {/* FINAL picture: Strategies that WILL be applied after submit */}
          <div style={{ background: 'rgba(34,197,94,0.06)', border: '2px solid var(--green)', borderRadius: 8, padding: '10px 14px', marginTop: 4 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--green)', marginBottom: 6 }}>
              ✅ Strategies that will be applied to {selectedSymbol || ''} after submit:
            </div>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {proposedStrategies.length > 0 ? (
                proposedStrategies.map(s => {
                  const isActive = activeStrategies.has(s);
                  return (
                    <label key={s} style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      padding: '4px 12px', borderRadius: 6,
                      background: isActive ? 'rgba(34,197,94,0.15)' : 'rgba(128,128,128,0.1)',
                      border: `1px solid ${isActive ? 'var(--green)' : 'var(--muted)'}`,
                      cursor: 'pointer', fontSize: 12,
                      opacity: isActive ? 1 : 0.5,
                    }}>
                      <input type="checkbox" checked={isActive} onChange={() => {
                        setActiveStrategies(prev => {
                          const next = new Set(prev);
                          if (next.has(s)) next.delete(s); else next.add(s);
                          return next;
                        });
                        setProposedStrategies(prev => prev.includes(s) ? prev.filter(x => x !== s) : [...prev, s]);
                      }} onClick={e => e.stopPropagation()} style={{ accentColor: isActive ? 'var(--green)' : 'var(--muted)' }} />
                      <span style={{ fontWeight: 600, opacity: isActive ? 1 : 0.5 }}>{s}</span>
                    </label>
                  );
                })
              ) : (
                <span style={{ color: 'var(--muted)', fontStyle: 'italic', fontSize: 12 }}>No strategies selected.</span>
              )}
            </div>
          </div>
        </Panel>
      )}

      {/* Strategy Parameters — grouped by individual strategy name */}
      {proposedStrategies.length > 0 && phase !== 'approve' && (
        <Panel title="Strategy Parameters">
          {proposedStrategies.map(stratName => {
            const params = paramSections[stratName];
            return (
              <div key={stratName} style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--blue)', marginBottom: 8, paddingBottom: 4, borderBottom: '2px solid var(--border)' }}>
                  ── {stratName} ──
                </div>
                {params && Object.keys(params).length > 0 ? (
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ borderBottom:'1px solid var(--border)' }}>
                        <th style={{ padding:'6px 10', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>Parameter</th>
                        <th style={{ padding:'6px 10', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(params).map(([k, v]) => {
                        const val = typeof v === 'object' ? JSON.stringify(v) : String(v ?? '');
                        const isBlank = !val || val.trim() === '' || val === '{}';
                        const isMeta = k.startsWith('_');
                        const displayLabel = isMeta ? k.replace('_', '').toUpperCase().replace(/_LEVELS/, ' LEVELS').replace(/_SPLIT/, ' SPLIT') : k;
                        
                        return (
                          <tr key={k} style={{ borderBottom:'1px solid var(--border)', background: isBlank ? 'rgba(239,68,68,0.04)' : undefined }}>
                            <td style={{ padding:'6px 10', fontFamily:'monospace', color: isMeta ? 'var(--amber)' : 'inherit' }}>
                              {isMeta && <span style={{ marginRight: 4 }}>'🔧'</span>}{displayLabel}
                            </td>
                            <td style={{ padding:'6px 10' }}>
                              {isMeta && k === '_timeframe' ? (
                                <select
                                  value={val}
                                  onChange={(e) => setParamSections(prev => ({ ...prev, [stratName]: { ...prev[stratName], [k]: e.target.value } }))}
                                  style={{ background: isBlank ? 'rgba(239,68,68,0.08)' : 'var(--bg)', color:'var(--text)', border: isBlank ? '1px solid var(--red)' : '1px solid var(--border)', borderRadius: 4, padding:'4px 8px', fontSize:13, width:'100%', outline:'none', fontFamily:'monospace' }}
                                >
                                  <option value="">— auto —</option>
                                  {['auto','5m','15m','30m','1h','4h','1d'].map(tf => <option key={tf} value={tf}>{tf}</option>)}
                                </select>
                              ) : isMeta && (k === '_tp_levels' || k === '_tp_split') ? (
                                <input type="text" placeholder={k === '_tp_levels' ? 'e.g. 0.01, 0.02, 0.03' : 'e.g. 0.3, 0.4, 0.3'}
                                  value={val}
                                  onChange={(e) => setParamSections(prev => ({ ...prev, [stratName]: { ...prev[stratName], [k]: e.target.value } }))}
                                  style={{ background: isBlank ? 'rgba(239,68,68,0.08)' : 'var(--bg)', color:'var(--text)', border: isBlank ? '1px solid var(--red)' : '1px solid var(--border)', borderRadius: 4, padding:'4px 8px', fontSize:13, width:'100%', outline:'none', fontFamily:'monospace' }}
                                />
                              ) : (
                                <input type="text" value={val}
                                  onChange={(e) => setParamSections(prev => ({ ...prev, [stratName]: { ...prev[stratName], [k]: e.target.value } }))}
                                  style={{ background: isBlank ? 'rgba(239,68,68,0.08)' : 'var(--bg)', color:'var(--text)', border: isBlank ? '1px solid var(--red)' : '1px solid var(--border)', borderRadius: 4, padding:'4px 8px', fontSize:13, width:'100%', outline:'none', fontFamily:'monospace' }}
                                />
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <div style={{ padding: '8px 12px', color:'var(--muted)', fontSize:12 }}>No parameters available for {stratName}.</div>
                )}
              </div>
            );
          })}
        </Panel>
      )}

      {/* Diff viewer */}
      {phase === 'diff' && diffData && (
        <Panel title="Config Diff Preview">
          {/* Section 1: Strategy Block Changes */}
          {blockState && ((blockState.added_groups && blockState.added_groups.length) || (blockState.removed_groups && blockState.removed_groups.length)) && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--blue)', marginBottom: 8 }}>
                Strategy Block Changes
              </div>
              {blockState.added_groups?.map((grp) => (
                <div key={grp} style={{ padding: '6px 10px', background: 'rgba(34,197,94,0.1)', borderLeft: '3px solid var(--green)', borderRadius: 4, marginBottom: 4, fontSize: 12 }}>
                  <span style={{ color: 'var(--green)', fontWeight: 600 }}>+ {grp}</span>
                  {' '}({ALL_GROUPS[grp]?.join(', ') || '-'})
                </div>
              ))}
              {blockState.removed_groups?.map((grp) => (
                <div key={grp} style={{ padding: '6px 10px', background: 'rgba(239,68,68,0.1)', borderLeft: '3px solid var(--red)', borderRadius: 4, marginBottom: 4, fontSize: 12 }}>
                  <span style={{ color: 'var(--red)', fontWeight: 600 }}>− {grp}</span>
                  {' '}({ALL_GROUPS[grp]?.join(', ') || '-'})
                </div>
              ))}
            </div>
          )}

          {/* Section 2: Per-Strategy Parameter Diffs */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--blue)', marginBottom: 8 }}>
              Strategy Parameters by Strategy
            </div>
            {(() => {
              const spd = diffData.state?.strategy_param_diffs || diffData.strategy_param_diffs;
              if (!spd) return null;
              return Object.entries(spd).map(([stratName, diffs]) => (
                <div key={stratName} style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text)', marginBottom: 4, paddingBottom: 2 }}>
                    ── {stratName} ──
                  </div>
                  {(!diffs || diffs.length === 0) ? (
                    <div style={{ fontSize: 12, color: 'var(--muted)', paddingLeft: 8 }}>No parameters for this strategy.</div>
                  ) : (
                    <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12, marginLeft: 8 }}>
                      <thead>
                        <tr style={{ borderBottom:'1px solid var(--border)' }}>
                          <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>Field</th>
                          <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>Old Value</th>
                          <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>New Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {diffs.map((d, i) => {
                          const changed = String(d.old_value ?? '-') !== String(d.new_value ?? '-');
                          return (
                            <tr key={i} style={{ borderBottom:'1px solid var(--border)', background: changed ? 'rgba(34,197,94,0.06)' : undefined }}>
                              <td style={{ padding:'4px 8', fontFamily:'monospace' }}>{d.field || d.field_path || '-'}</td>
                              <td style={{ padding:'4px 8', color: changed ? 'var(--text)' : 'var(--muted)', fontStyle: !changed ? 'italic' : 'normal' }}>
                                {String(d.old_value ?? '-')}
                              </td>
                              <td style={{ padding:'4px 8', fontWeight:600, color: changed ? 'var(--green)' : 'var(--muted)' }}>
                                {String(d.new_value ?? '-')}
                                {!changed && <span style={{ marginLeft: 4, fontSize: 10, opacity: 0.5 }}>unchanged</span>}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  )}
                </div>
              ));
            })()}
          </div>

          {/* Fallback: legacy flat diff table (if no strategy_param_diffs yet) */}
          {(() => {
            const spd = diffData.state?.strategy_param_diffs || diffData.strategy_param_diffs;
            if (spd) return null; // already rendered above
            if (!diffData.diffs || diffData.diffs.length === 0) return null;
            return (
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                <thead>
                  <tr style={{ borderBottom:'1px solid var(--border)' }}>
                    <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--muted)' }}>File</th>
                    <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--muted)' }}>Field</th>
                    <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--red)' }}>Old Value</th>
                    <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--green)' }}>New Value</th>
                  </tr>
                </thead>
                <tbody>
                  {diffData.diffs.map((d, i) => (
                    <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                      <td style={{ padding:'4px 8', fontFamily:'monospace' }}>{d.file || '-'}</td>
                      <td style={{ padding:'4px 8', fontFamily:'monospace' }}>{d.field_path || '-'}</td>
                      <td style={{ padding:'4px 8' }}>{String(d.old_value ?? '-')}</td>
                      <td style={{ padding:'4px 8', fontWeight:600, color:'var(--green)' }}>{String(d.new_value ?? '-')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            );
          })()}

          {/* Empty state */}
          {(() => {
            const spd = diffData.state?.strategy_param_diffs || diffData.strategy_param_diffs;
            if (spd || (diffData.diffs && diffData.diffs.length > 0)) return null;
            return <EmptyState message="No changes detected for the selected configurations." />;
          })()}

          {/* Section 3: Dynamic TP/TF Config (strategy_configs block) */}
          {(injectTPLevels || injectTPSplit || injectTimeframe) && phase === 'diff' && (
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--blue)', marginBottom: 8 }}>
                strategy_configs (dynamic TP/TF) — will be written per-strategy in watchlist.yaml
              </div>
              <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
                <thead>
                  <tr style={{ borderBottom:'1px solid var(--border)' }}>
                    <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--muted)' }}>Field</th>
                    <th style={{ padding:'4px 8px', textAlign:'left', color:'var(--green)' }}>Value</th>
                  </tr>
                </thead>
                <tbody>
                  {injectTPLevels != null && (
                    <tr key="tp_levels" style={{ borderBottom:'1px solid var(--border)' }}>
                      <td style={{ padding:'4px 8', fontFamily:'monospace' }}>tp_levels</td>
                      <td style={{ padding:'4px 8', fontWeight:600, color:'var(--green)' }}>{JSON.stringify(injectTPLevels)}</td>
                    </tr>
                  )}
                  {injectTPSplit != null && (
                    <tr key="tp_split" style={{ borderBottom:'1px solid var(--border)' }}>
                      <td style={{ padding:'4px 8', fontFamily:'monospace' }}>tp_split</td>
                      <td style={{ padding:'4px 8', fontWeight:600, color:'var(--green)' }}>{JSON.stringify(injectTPSplit)}</td>
                    </tr>
                  )}
                  {injectTimeframe != null && (
                    <tr key="timeframe" style={{ borderBottom:'1px solid var(--border)' }}>
                      <td style={{ padding:'4px 8', fontFamily:'monospace' }}>timeframe</td>
                      <td style={{ padding:'4px 8', fontWeight:600, color:'var(--green)' }}>{injectTimeframe}</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          {/* Section 4: Approve / Back buttons */}
          <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
            <Button onClick={handleApprove} primary>Approve &amp; Push</Button>
            <Button onClick={handleDecline}>Back to Review</Button>
          </div>
        </Panel>
      )}

      {phase === 'approve' && (
        <Panel title="Push Status">
          {pushed ? (
            <div style={{ color:'var(--green)', fontWeight:600 }}><span style={{ marginRight: 4 }}>✓</span>Configuration pushed successfully to trading-system.</div>
          ) : (
            <span style={{ color:'var(--blue)' }}>Pushing…</span>
          )}
        </Panel>
      )}

      {/* Action buttons */}
      {phase === 'review' && !pushed && (
        <Button onClick={handleDiff} primary disabled={!selectedSymbol || proposedStrategies.length === 0}>Review Diff &amp; Push</Button>
      )}

      {error && <div style={{ padding: 8, background: 'var(--red)', color: '#fff', borderRadius: 6, fontSize: 13 }}>{error}</div>}
    </div>
  );
}
export default function StrategyLabPanel() {
  const [activeTab, setActiveTab] = useState('Tune');

  return (
    <SimConfigProvider>
      <div style={{ padding: '0 4px' }}>
        {/* Tab bar */}
        <div style={{ display:'flex', gap:4, marginBottom:16, overflowX:'auto' }}>
          {TABS.map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: '8px 18px',
                background: activeTab === tab ? 'var(--blue)' : 'transparent',
                color: activeTab === tab ? '#fff' : 'var(--muted)',
                border: 'none',
                borderRadius: 6,
                cursor: 'pointer',
                fontSize: 13,
                fontWeight: activeTab === tab ? 600 : 400,
                whiteSpace: 'nowrap',
                transition: 'background .15s, color .15s',
              }}
            >
              {tab}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div style={{ maxWidth: 960 }}>
          {activeTab === 'Tune' && <TuneTab />}
          {activeTab === 'Visual' && <TuneVisualTab />}
          {activeTab === 'Results' && <ResultsTab />}
          {activeTab === 'Inject' && <InjectTab />}
        </div>
      </div>
    </SimConfigProvider>
  );
}
