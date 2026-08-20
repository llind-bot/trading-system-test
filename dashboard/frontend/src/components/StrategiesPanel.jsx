import React, { useState, useEffect } from 'react';
import { fetchStrategyEvaluations, fetchStrategiesHistory, fetchWatchlistFull } from '../lib/api.js';
import { formatEasternShort } from '../lib/tz.js';

/* ── Asset class helpers ─────────────────────────────────────── */
function classifyAsset(symbol) {
  return symbol.includes('/') ? 'crypto' : 'stock';
}

// Cache from watchlist so we can look up asset_class reliably
const _classCache = {};
async function loadAssetClasses() {
  try {
    const wl = await fetchWatchlistFull();
    if (wl && Array.isArray(wl)) {
      for (const a of wl) _classCache[a.symbol] = a.asset_class;
    }
  } catch {}
}

/** Determine asset class with fallback (watchlist -> symbol convention). */
function getAssetClass(symbol) {
  return _classCache[symbol] || classifyAsset(symbol);
}

// Lazy-load on first mount
loadAssetClasses();

/* ── Timestamp display ──────────────────────────────────────── */

/**
 * Parse a timestamp string (from DB API) for comparison purposes.
 * The backend convert_timestamps_in_value() converts to Eastern format:
 *   - 12h: "2026-07-01 11:17:00 PM"
 *   - 24h: "2026-07-01 23:17:00"  (less common)
 * Returns a Date object in UTC for comparison.
 */
function parseTimestampForCompare(ts) {
  if (!ts) return new Date(0);
  const s = String(ts);

  // Already 12h format with AM/PM
  const m12h = s.match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2}):(\d{2})\s*([AP]M)/i);
  if (m12h) {
    let h = parseInt(m12h[4]);
    if (m12h[7].toUpperCase() === 'PM' && h < 12) h += 12;
    if (m12h[7].toUpperCase() === 'AM' && h === 12) h = 0;
    return new Date(`${m12h[1]}-${m12h[2]}-${m12h[3]}T${String(h).padStart(2, '0')}:${m12h[5]}:${m12h[6]}Z`);
  }

  // 24h format -> treat as UTC
  const m24h = s.match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$/);
  if (m24h) {
    return new Date(`${m24h[1]}-${m24h[2]}-${m24h[3]}T${m24h[4]}:${m24h[5]}:${m24h[6]}Z`);
  }

  // fallback
  try {
    return new Date(s.replace(' ', 'T') + 'Z');
  } catch {
    return new Date(0);
  }
}

/**
 * Format a timestamp for display — delegates to formatEasternShort
 * (single source of truth, always Eastern timezone).
 */
function formatTimestamp(ts) {
  return formatEasternShort(ts);
}

/* ── Main panel ─────────────────────────────────────────────── */
export default function StrategiesPanel() {
  const [evals, setEvals] = useState(null);
  const [historyData, setHistoryData] = useState(null);
  const [loading, setLoading] = useState(true);

  const [activeTab, setActiveTab] = useState('grid');     // 'grid' | 'history'
  const [symbolFilter, setSymbolFilter] = useState('');
  const [assetFilter, setAssetFilter] = useState('all');   // 'all' | 'stock' | 'crypto'

  useEffect(() => {
    Promise.all([
      fetchStrategyEvaluations(),
      fetchStrategiesHistory({ limit: 2000, latest_per_symbol: true }),,
    ]).then(([e, h]) => {
      setEvals(e);
      setHistoryData(h);
    }).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading strategies...</div>;

  // Build symbol list from evals (all-time, comprehensive) rather than history
  // (history fetch only gets latest N records which may be skewed to one asset class)
  const allSymbolsFromEvals = [...new Set((evals || []).map(e => e.symbol))];
  // Also pick up any symbols that appear in history but not evals
  const allHistorySymbols = historyData
    ? historyData.map(h => h.symbol).filter(s => !allSymbolsFromEvals.includes(s)) : [];
  const allAssetOptions = [...allSymbolsFromEvals, ...allHistorySymbols].sort();

  // Build asset-class-aware symbol list (for the filter)
  const assetSymbols = allAssetOptions.map(s => ({
    symbol: s,
    assetClass: getAssetClass(s),
  }));

  // Apply asset class filter to symbols
  const filteredAssetSymbols = assetFilter === 'all'
    ? assetSymbols
    : assetSymbols.filter(s => s.assetClass === assetFilter);
  const filteredSymbolsSet = new Set(filteredAssetSymbols.map(s => s.symbol));

  const voteColor = v => v==='BUY' ? 'var(--green)' : v==='SELL' ? 'var(--red)' : 'var(--muted)';

  return (
    <>
      <h2 style={{ margin:'0 0 16px',fontSize:18 }}>Strategy Evaluations</h2>

      {/* Sub-tabs */}
      <div style={{ display:'flex', gap:4, marginBottom:12, borderBottom:'1px solid var(--border)' }}>
        {[{id:'grid',label:'Evaluation Grid'},{id:'history',label:'Signal History'}].map(t => (
          <button key={t.id} onClick={()=>setActiveTab(t.id)} style={{
            padding:'6px 14px', background:activeTab===t.id?'var(--blue)':'transparent',
            color:activeTab===t.id?'#fff':'var(--muted)', border:'none',
            borderBottom:activeTab===t.id?'2px solid var(--blue)':'2px solid transparent',
            cursor:'pointer', fontSize:12, fontWeight:activeTab===t.id?600:400, borderRadius:'4px 4px 0 0'
          }}>{t.label}</button>))}
      </div>

      {/* Asset class filter + search row */}
      <div style={{ display:'flex', gap:8, marginBottom:16, alignItems:'center', flexWrap:'wrap' }}>
        {/* Asset class toggle */}
        <div style={{ display:'flex', borderRadius:6, overflow:'hidden', border:'1px solid var(--border)' }}>
          {[
            { id: 'all', label: 'All' },
            { id: 'stock', label: 'Stocks' },
            { id: 'crypto', label: 'Crypto' },
          ].map(f => (
            <button key={f.id} onClick={() => setAssetFilter(f.id)} style={{
              padding:'4px 12px', fontSize:11, fontWeight: assetFilter===f.id ? 600 : 400,
              background: assetFilter===f.id ? 'var(--blue)' : 'transparent',
              color: assetFilter===f.id ? '#fff' : 'var(--muted)',
              border:'none', cursor:'pointer',
            }}>{f.label}</button>))}
        </div>

        {/* Symbol search */}
        <input
          value={symbolFilter}
          onChange={e => setSymbolFilter(e.target.value)}
          placeholder="Search asset..."
          style={{
            padding:'4px 10px', background:'var(--surface)',
            border:'1px solid var(--border)', borderRadius:6, color:'var(--text)',
            width:160, fontSize:12
          }}
        />
      </div>

      {activeTab === 'grid' && (
        <EvaluationGrid
          evals={evals || []}
          voteColor={voteColor}
          assetSymbols={filteredAssetSymbols}
          symbolFilter={symbolFilter}
        />
      )}
      {activeTab === 'history' && (
        <SignalHistory
          historyData={historyData || []}
          voteColor={voteColor}
          assetSymbols={filteredAssetSymbols}
          filteredSymbolsSet={filteredSymbolsSet}
          symbolFilter={symbolFilter}
        />
      )}
    </>
  );
}

/* ── Evaluation Grid sub-tab ─────────────────────────────────── */
function EvaluationGrid({ evals, voteColor, assetSymbols, symbolFilter }) {
  const groups = [...new Set(evals.map(e => e.strategy))];
  const symbols = Object.groupBy ? Object.groupBy(evals, e => e.symbol) : (() => {
    const g = {}; evals.forEach(e => { if(!g[e.symbol])g[e.symbol]=[]; g[e.symbol].push(e); }); return g;
  })();

  // Apply symbol search filter within the asset-filtered set
  const filteredList = assetSymbols.filter(s => {
    if (!symbolFilter) return true;
    return s.symbol.toLowerCase().includes(symbolFilter.toLowerCase());
  });

  return (
    <div style={{ overflowX:'auto' }}>
      <table style={{ width:'100%',borderCollapse:'collapse',fontSize:13 }}>
        <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
          <th style={{ padding:'8px 12px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>Symbol</th>
          {groups.map(g => <th key={g} style={{ padding:'8px 12px',textAlign:'center',color:'var(--muted)',fontWeight:500 }}>{g}</th>)}
        </tr></thead>
        <tbody>
          {filteredList.length > 0 ? filteredList.map(({ symbol: sym }) => (
            <tr key={sym} style={{ borderBottom:'1px solid var(--border)' }}>
              <td style={{ padding:'8px 12',fontWeight:600 }}>{sym}</td>
              {groups.map(g => {
                const ev = symbols[sym]?.find(x=>x.strategy===g);
                return <td key={g} style={{ padding:'8px 12',textAlign:'center' }}>
                  <span style={{ color:voteColor(ev?.vote_result),fontWeight:600 }}>{ev ? ev.vote_result : '-'}</span>
                  {ev?.confidence !== undefined && <div style={{ fontSize:9,color:'var(--muted)' }}>{(ev.confidence*100).toFixed(0)}%</div>}
                </td>;})}
            </tr>)) : (
            <tr><td colSpan={groups.length + 1} style={{ padding:24,textAlign:'center',color:'var(--muted)' }}>No assets match the current filters.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/* ── Signal History sub-tab ──────────────────────────────────── */
function SignalHistory({ historyData, voteColor, assetSymbols, filteredSymbolsSet, symbolFilter }) {
  // Group signals by (symbol) to find the latest per symbol across ALL groups
  const latestPerSymbol = {};
  historyData.forEach(h => {
    if (!filteredSymbolsSet.has(h.symbol)) return; // respect asset filter
    const key = h.symbol;
    const tsA = latestPerSymbol[key]?.timestamp ? parseTimestampForCompare(latestPerSymbol[key].timestamp) : new Date(0);
    const tsB = parseTimestampForCompare(h.timestamp || '0');
    if (!latestPerSymbol[key] || tsB > tsA) {
      latestPerSymbol[key] = h;
    }
  });

  // Filter the full history -- respect asset filter + symbol search
  const filteredHistory = historyData
    .filter(h => {
      if (!filteredSymbolsSet.has(h.symbol)) return false;
      if (symbolFilter && !h.symbol.toLowerCase().includes(symbolFilter.toLowerCase())) return false;
      return true;
    })
    .slice(0, 200);

  // Also filter latestPerSymbol for the summary table
  const filteredLatest = {};
  for (const sym of Object.keys(latestPerSymbol)) {
    if (!filteredSymbolsSet.has(sym)) continue;
    if (symbolFilter && !sym.toLowerCase().includes(symbolFilter.toLowerCase())) continue;
    filteredLatest[sym] = latestPerSymbol[sym];
  }

  return (
    <div style={{ overflowX:'auto' }}>

      {/* Final evaluation per symbol summary */}
      {Object.keys(filteredLatest).length > 0 && (
        <div style={{ marginBottom:20 }}>
          <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)' }}>Latest Evaluation Per Symbol</h4>
          <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12,marginBottom:16 }}>
            <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
              {['Symbol','Final Vote','Confidence','Timestamp'].map(h => (
                <th key={h} style={{ padding:'6px 10px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
            </tr></thead>
            <tbody>
              {Object.entries(filteredLatest)
                .sort((a,b) => parseTimestampForCompare(b[1].timestamp) - parseTimestampForCompare(a[1].timestamp))
                .map(([sym, h]) => (
                  <tr key={sym} style={{ borderBottom:'1px solid var(--border)' }}>
                    <td style={{ padding:'6px 10px',fontWeight:600 }}>{sym}</td>
                    <td style={{ padding:'6px 10px',color:voteColor(h.vote_result),fontWeight:600 }}>{h.vote_result}</td>
                    <td style={{ padding:'6px 10px' }}>{h.confidence != null ? (h.confidence*100).toFixed(0)+'%' : '-'}</td>
                    <td style={{ padding:'6px 10px',color:'var(--muted)',fontSize:11 }}>{formatTimestamp(h.timestamp)}</td>
                  </tr>))}
            </tbody>
          </table>
        </div>
      )}

      {/* Full signal history table */}
      <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)' }}>Signal History (last {filteredHistory.length})</h4>
      <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12 }}>
        <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
          {['Timestamp','Symbol','Group','Vote','Confidence'].map(h => (
            <th key={h} style={{ padding:'6px 10px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
        </tr></thead>
        <tbody>
          {filteredHistory.length > 0 ? filteredHistory.map((e,i) => (
            <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
              <td style={{ padding:'6px 10px',color:'var(--muted)',fontSize:11 }}>{formatTimestamp(e.timestamp)}</td>
              <td style={{ padding:'6px 10px',fontWeight:600 }}>{e.symbol}</td>
              <td style={{ padding:'6px 10px' }}>{e.strategy}</td>
              <td style={{ padding:'6px 10px',color:voteColor(e.vote_result),fontWeight:600 }}>{e.vote_result}</td>
              <td style={{ padding:'6px 10px' }}>{e.confidence != null ? (e.confidence*100).toFixed(0)+'%' : '-'}</td>
            </tr>)) : (
            <tr><td colSpan={5} style={{ padding:24,textAlign:'center',color:'var(--muted)' }}>No signals match the current filters.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
