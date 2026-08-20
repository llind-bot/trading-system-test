import React, { useState, useEffect } from 'react';
import { fetchReportSignalEvaluations } from '../../lib/api.js';

const LIVE_TARGETS = {
  "BTC/USD": 0.75, "ETH/USD": 0.80, "SOL/USD": 0.85,
  "AVAX/USD": 0.80, "LINK/USD": 0.85, "UNI/USD": 0.80,
  "LTC/USD": 0.85, "DOGE/USD": 0.85, "XRP/USD": 0.70,
};

// Strategy groups that currently have evaluation data in the DB
const AVAILABLE_STRATEGIES = [
  { value: 'all', label: 'All Strategies' },
  { value: 'CryptoSwingDaily', label: 'CryptoSwingDaily' },
];

export default function SignalEvaluationsReport() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedStrategy, setSelectedStrategy] = useState('all');

  useEffect(() => {
    fetchReportSignalEvaluations().then(setData).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading signal evaluations...</div>;
  if (!data) return <div style={{ padding:24, textAlign:'center', color:'var(--muted)' }}>No data available</div>;

  // --- Merged live/closed positions from the positions table (authoritative source) ---
  const livePositions = (data.live_positions || []).filter(p => p.position.qty > 0);
  const closedPosData = data.closed_positions_data || [];

  // Filter evals by strategy (only affects evaluation-based metrics)
  const filteredEvals = selectedStrategy === 'all'
    ? (data.crypto_swing_daily || [])
    : (data.crypto_swing_daily || []).filter(e => e.strategy_group === selectedStrategy);

  // Unique open positions with real entry values (dedup from evals only)
  const openEvalPositions = Object.values(
    Object.fromEntries(
      filteredEvals
        .filter(e => e.status === 'open' && Number(e.entry_price) > 0)
        .map(e => [`${e.symbol}_${e.signal_timestamp}`, e])
    )
  );

  // Unique closed trades (dedup by symbol + cycle_id or signal_timestamp)
  const closeKey = (e) => e.cycle_id ? `${e.symbol}_${e.cycle_id}` : `${e.symbol}_${e.signal_timestamp}`;
  const uniqueEvals = Object.values(
    Object.fromEntries(filteredEvals.map(e => [closeKey(e), e]))
  );

  // Collect all symbols we have data for (evals + live positions + closed)
  const symbolSet = new Set();
  uniqueEvals.forEach(e => symbolSet.add(e.symbol));
  livePositions.forEach(p => symbolSet.add(p.display_symbol));
  closedPosData.forEach(p => symbolSet.add(p.display_symbol));

  // --- Helpers ---
  function assetMetrics(symbol) {
    const evals = uniqueEvals.filter(e => e.symbol === symbol && e.status === 'closed');
    if (!evals.length) return null;
    
    const pnlPcts = evals.map(e => Number(e.realized_pnl_pct)).filter(v => v != null);
    const wins = pnlPcts.filter(p => p > 0).length;
    const winRate = pnlPcts.length ? (wins / pnlPcts.length) : 0;
    const avgPnl = pnlPcts.length ? pnlPcts.reduce((a,b)=>a+b,0)/pnlPcts.length : 0;
    const totalUsd = evals.reduce((s,e) => s + Number(e.realized_pnl_usd||0), 0);
    
    const holds = evals.map(e=>Number(e.holding_bars)).filter(v=>v!=null);
    const avgHold = holds.length ? holds.reduce((a,b)=>a+b,0)/holds.length : 0;
    
    return { trades: evals.length, winRate: (winRate*100).toFixed(1), avgPnl: avgPnl.toFixed(2), totalUsd: totalUsd.toFixed(2), avgHold: avgHold.toFixed(0) };
  }

  function getAssetStatus(symbol) {
    // Check if this symbol has live open positions first (authoritative)
    const livePos = livePositions.find(p => p.display_symbol === symbol);
    const hasLive = !!livePos && livePos.position.qty > 0;
    
    // Check if it has eval data (closed + metrics or open)
    const m = assetMetrics(symbol);
    const openEval = openEvalPositions.find(e => e.symbol === symbol);
    const hasOpenEvals = openEval && Number(openEval.entry_price) > 0;

    if (hasLive) {
      const pos = livePos.position;
      return {
        hasLive: true,
        qty: pos.qty,
        cost: pos.avg_cost,
        currentPrice: pos.current_price,
        unrealizedPnl: pos.unrealized_pnl || 0,
        totalInvested: pos.total_invested || (pos.qty * pos.avg_cost),
        hasEvals: m || hasOpenEvals, // whether it also has eval data
      };
    }
    if (m && m.trades) return m;
    if (hasOpenEvals) {
      return { ...m, trades: null, winRate: null, hasOpen: true };
    }
    return null;
  }

  // --- Get the active strategy name for display ---
  const activeStrategyLabel = AVAILABLE_STRATEGIES.find(s => s.value === selectedStrategy)?.label || 'All Strategies';

  return (
    <>
      {/* Header with Strategy Dropdown */}
      <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:16 }}>
        <h3 style={{ fontSize:16,margin:0 }}>Strategy Success Metrics</h3>
        <select
          value={selectedStrategy}
          onChange={e => setSelectedStrategy(e.target.value)}
          style={{
            padding:'4px 10px',fontSize:12,borderRadius:4,
            border:'1px solid var(--border)',background:'var(--surface)',color:'var(--text)'
          }}
        >
          {AVAILABLE_STRATEGIES.map(s => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
      </div>

      {/* ── LIVE OPEN POSITIONS (from positions table, the authoritative source) ── */}
      {livePositions.length > 0 && (
        <div style={{ marginBottom:20 }}>
          <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)',textTransform:'uppercase',letterSpacing:1 }}>
            Live Open Positions ({activeStrategyLabel})
          </h4>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12 }}>
              <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
                {['Asset','Qty','Cost ($)','Current ($)','Unrealized P&L','Entry Evals'].map(h => (
                  <th key={h} style={{ padding:'6px 10px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
              </tr></thead>
              <tbody>
                {livePositions.map((p,i) => {
                  const pos = p.position;
                  const qty = parseFloat(pos.qty);
                  if (qty <= 0) return null;
                  const cost = parseFloat(pos.avg_cost);
                  const price = parseFloat(pos.current_price);
                  const invested = qty * cost;
                  const unrealized = parseFloat(pos.unrealized_pnl || 0);
                  const unrealizedPct = invested > 0 ? (unrealized / invested * 100) : 0;
                  const hasEval = p.has_eval_record;

                  return (
                    <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                      <td style={{ padding:'6px 10px',fontWeight:600 }}>{p.display_symbol}</td>
                      <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>
                        {qty > 1000 ? qty.toLocaleString(undefined, {maximumFractionDigits:2}) : qty.toFixed(4)}
                      </td>
                      <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>
                        ${invested.toFixed(2)}
                      </td>
                      <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>
                        {price < 0.01 ? price.toExponential(4) : price.toFixed(4)}
                      </td>
                      <td style={{ padding:'6px 10px',fontFamily:'monospace', color: unrealized >= 0 ? 'var(--green)' : '#e74c3c' }}>
                        {unrealized >= 0 ? '+' : ''}${unrealized.toFixed(2)} ({unrealizedPct >= 0 ? '+' : ''}{unrealizedPct.toFixed(1)}%)
                      </td>
                      <td style={{ padding:'6px 10px' }}>
                        {hasEval ? (
                          <span style={{padding:'2px 6px',borderRadius:3,fontSize:10,color:'var(--green)',background:'rgba(46,204,113,0.1)'}}>✅ tracked</span>
                        ) : (
                          <span style={{padding:'2px 6px',borderRadius:3,fontSize:10,color:'#f39c12',background:'rgba(243,156,18,0.1)'}}>⚠️ no eval</span>
                        )}
                      </td>
                    </tr>);
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── CLOSING POSITIONS FROM DB (positions table is_closed) ── */}
      {closedPosData.length > 0 && (
        <div style={{ marginBottom:20 }}>
          <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)',textTransform:'uppercase',letterSpacing:1 }}>Closed Positions from DB</h4>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12 }}>
              <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
                {['Asset','Qty Close Price','Cost','Close Date','Eval Records'].map(h => (
                  <th key={h} style={{ padding:'6px 10px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
              </tr></thead>
              <tbody>
                {closedPosData.map((p,i) => (
                  <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                    <td style={{ padding:'6px 10px',fontWeight:600 }}>{p.display_symbol}</td>
                    <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>{p.position.qty} @ {p.position.close_price?.toFixed(4) || '—'}</td>
                    <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>${(parseFloat(p.position.qty)*parseFloat(p.position.avg_cost)).toFixed(2)}</td>
                    <td style={{ padding:'6px 10px',color:'var(--muted)' }}>{p.position.close_date || '—'}</td>
                    <td style={{ padding:'6px 10px' }}>
                      {p.eval_records.length > 0 ? `${p.eval_records.length} record(s)` : (
                        <span style={{padding:'2px 6px',borderRadius:3,fontSize:10,color:'#f39c12',background:'rgba(243,156,18,0.1)'}}>⚠️ no eval</span>
                      )}
                    </td>
                  </tr>))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── All-Evaluations Table — grouped by strategy_group ── */}
      {data.strategy_groups && data.strategy_groups.length > 0 ? (
        <div style={{ marginBottom:20 }}>
          <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)',textTransform:'uppercase',letterSpacing:1 }}>Evaluation Summary by Strategy</h4>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12 }}>
              <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
                {['Strategy Group','Total Evals','Closed','Wins','Win Rate'].map(h => (
                  <th key={h} style={{ padding:'6px 10px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
              </tr></thead>
              <tbody>
                {data.strategy_groups.map((sg,i) => (
                  <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                    <td style={{ padding:'6px 10px',fontWeight:600 }}>{sg.strategy_group}</td>
                    <td style={{ padding:'6px 10px' }}>{sg.total_evals}</td>
                    <td style={{ padding:'6px 10px' }}>{sg.closed_trades}</td>
                    <td style={{ padding:'6px 10px',color: sg.win_trades === sg.closed_trades && sg.closed_trades > 0 ? 'var(--green)' : sg.closed_trades > 0 ? '#e74c3c' : 'var(--muted)' }}>
                      {sg.win_trades}
                    </td>
                    <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>
                      {sg.closed_trades ? (sg.win_trades/sg.closed_trades*100).toFixed(1)+'%' : '—'}
                    </td>
                  </tr>))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* ── Per-Asset Metrics — now includes assets from live positions even without eval data ── */}
      {symbolSet.size > 0 && (
        <div style={{ marginBottom:20 }}>
          <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)',textTransform:'uppercase',letterSpacing:1 }}>Per-Asset Metrics ({activeStrategyLabel})</h4>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12 }}>
              <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
                {['Asset','Trades','WR%','Avg P&L','$ Total','Hold(bars)','Live Qty','Status'].map(h => (
                  <th key={h} style={{ padding:'6px 10px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
              </tr></thead>
              <tbody>
                {Array.from(symbolSet).sort().map(asset => {
                  const info = getAssetStatus(asset);
                  if (!info) return null;

                  // Case 1: Has live open position (from positions table, authoritative)
                  if (info.hasLive) {
                    const pos = livePositions.find(p => p.display_symbol === asset).position;
                    const qty = parseFloat(pos.qty);
                    const hasEvals = info.hasEvals;
                    return (
                      <tr key={asset} style={{ borderBottom:'1px solid var(--border)' }}>
                        <td style={{ padding:'6px 10px',fontWeight:600 }}>{asset}</td>
                        <td style={{ padding:'6px 10px' }}>—</td>
                        <td style={{ padding:'6px 10px',color:'var(--muted)' }}>—</td>
                        <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>—</td>
                        <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>${(qty * parseFloat(pos.avg_cost)).toFixed(2)}</td>
                        <td style={{ padding:'6px 10px',color:'var(--muted)' }}>pending</td>
                        <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>{qty > 1000 ? qty.toLocaleString(undefined, {maximumFractionDigits:2}) : qty.toFixed(4)}</td>
                        <td style={{ padding:'6px 10px' }}>
                          {hasEvals
                            ? <span style={{padding:'2px 6px',borderRadius:3,fontSize:10,color:'var(--green)',background:'rgba(46,204,113,0.1)'}}>✅ tracked</span>
                            : <span style={{padding:'2px 6px',borderRadius:3,fontSize:10,color:'#f39c12',background:'rgba(243,156,18,0.1)'}}>⚠️ live only</span>
                          }
                        </td>
                      </tr>);
                  }

                  // Case 2: Has eval data with closed trades and metrics
                  const m = info;
                  if (m.trades) {
                    const lt = LIVE_TARGETS[asset] || 0.80;
                    let statusColor = 'var(--green)';
                    if ((m.winRate/100) < lt) statusColor = '#e74c3c';
                    const concern = m.trades >= 3 && (m.winRate/100) < lt;

                    return (
                      <tr key={asset} style={{ borderBottom:'1px solid var(--border)', background: concern ? 'rgba(231,76,60,0.05)' : '' }}>
                        <td style={{ padding:'6px 10px',fontWeight:600 }}>{asset}</td>
                        <td style={{ padding:'6px 10px' }}>{m.trades}</td>
                        <td style={{ padding:'6px 10px',color:statusColor,fontWeight:500 }}>{m.winRate}%</td>
                        <td style={{ padding:'6px 10px',fontFamily:'monospace',fontWeight:500,color:(Number(m.avgPnl)>=0?'var(--green)':'#e74c3c') }}>
                          {m.avgPnl >= 0 ? '+' : ''}{m.avgPnl}%
                        </td>
                        <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>${Number(m.totalUsd).toLocaleString()}</td>
                        <td style={{ padding:'6px 10px',color:'var(--muted)' }}>{m.avgHold}</td>
                        <td style={{ padding:'6px 10px',color:'var(--muted)' }}>—</td>
                        <td style={{ padding:'6px 10px' }}>
                          <span style={{padding:'2px 6px',borderRadius:3,fontSize:10,color:statusColor,fontWeight:600}}>
                            {concern ? '⚠️ concern' : `${m.winRate}%`}
                          </span>
                        </td>
                      </tr>);
                  }

                  // Case 3: Only has open eval (no closed trades yet) — no live position in positions table
                  if (info.hasOpen) {
                    const op = openEvalPositions.find(e => e.symbol === asset && Number(e.entry_price) > 0);
                    const entry = op ? Number(op.entry_price) : 0;
                    return (
                      <tr key={asset} style={{ borderBottom:'1px solid var(--border)' }}>
                        <td style={{ padding:'6px 10px',fontWeight:600 }}>{asset}</td>
                        <td style={{ padding:'6px 10px' }}><span style={{color:'#f39c12',fontSize:10}}>⏳ open</span></td>
                        <td style={{ padding:'6px 10px',color:'var(--muted)' }}>—</td>
                        <td style={{ padding:'6px 10px',fontFamily:'monospace' }}>${(entry * Number(op.entry_qty)).toFixed(2)}</td>
                        <td style={{ padding:'6px 10px',color:'var(--muted)' }}>pending</td>
                        <td style={{ padding:'6px 10px',color:'var(--muted)' }}>—</td>
                        <td style={{ padding:'6px 10px' }}>
                          <span style={{padding:'2px 6px',borderRadius:3,fontSize:10,color:'#f39c12'}}>⏳ open</span>
                        </td>
                      </tr>);
                  }

                  return null;
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Kill Switch Status */}
      <div style={{ marginBottom:20 }}>
        <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)',textTransform:'uppercase',letterSpacing:1 }}>Kill Switch (Live Data)</h4>
        {(() => {
          const k = data.kill_switch || {};
          const cswTrades = selectedStrategy === 'CryptoSwingDaily' ? k.csw_trades || 0 : 0;
          const tradesOk = cswTrades >= 30 ? 'OK' : `${cswTrades}/30`;
          
          const liveClosed = uniqueEvals.filter(e => e.realized_pnl_usd != null);
          const wins = liveClosed.filter(e => Number(e.realized_pnl_usd) > 0).length;
          const wr = liveClosed.length ? `${(wins/liveClosed.length*100).toFixed(1)}%` : 'N/A';
          
          return (
            <div style={{ display:'flex',gap:16,flexWrap:'wrap' }}>
              <CardMetric label="Total Trades" value={cswTrades} target={'30+'} status={cswTrades >= 5 ? 'OK' : `${tradesOk}`} />
              <CardMetric label="Overall Win Rate" value={wr} target={'>50%'} status={wr === 'N/A' ? 'PENDING' : (Number(wr) > 50 ? 'OK' : 'WARNING')} />
              <CardMetric label="Worst Single Loss" value={k.csw_min_pnl_pct != null ? `$${k.csw_min_pnl_pct.toFixed(2)}` : 'N/A'} target={'>$500 threshold'} status={(k.csw_min_pnl_pct || 999) > -500 ? 'OK' : 'WARNING'} />
            </div>
          );
        })()}
      </div>

      {/* Hypothesis Validation */}
      <div style={{ marginBottom:8 }}>
        <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)',textTransform:'uppercase',letterSpacing:1 }}>Hypothesis Validation</h4>
        {uniqueEvals.filter(e => e.status === 'closed').length ? (
          <div style={{ padding:12,borderRadius:8,border:'1px solid var(--border)',fontSize:12,background:'var(--surface)' }}>
            <div style={{ marginBottom:6 }}>
              <strong>Penetration → Bounce Rate:</strong> Testing whether deeper BB penetrations correlate with higher bounce rates...
            </div>
            <div>
              <strong>Bounce Rate Target:</strong> {uniqueEvals.filter(e => e.status === 'closed').length} signals evaluated. Need ≥85% for strategy to pass validation.
            </div>
          </div>
        ) : (
          <div style={{ padding:12,borderRadius:8,border:'1px solid var(--border)',fontSize:12,color:'var(--muted)' }}>
            No closed evaluations to analyze yet. {livePositions.length > 0 ? `${livePositions.length} live position${livePositions.length>1?'s':''} in progress — results will populate once TP ladder or SL fires.` : 'Waiting for trades to complete.'}
          </div>
        )}
      </div>
    </>
  );
}

function CardMetric({ label, value, target, status }) {
  const isOk = status === 'OK';
  const isWarn = status === 'WARNING';
  return (
    <div style={{ 
      flex:'1 1 180px', padding:12, borderRadius:8, border:'1px solid var(--border)',
      background:'var(--surface)'
    }}>
      <div style={{ fontSize:11,color:'var(--muted)',marginBottom:4 }}>{label} (target: {target})</div>
      <div style={{ display:'flex',justifyContent:'space-between',alignItems:'center' }}>
        <span style={{ fontWeight:600,fontSize:15 }}>{value}</span>
        <span style={{ 
          padding:'2px 8px',borderRadius:3,fontSize:10,fontWeight:600,
          background:isOk?'rgba(46,204,113,0.1)':isWarn?'rgba(231,76,60,0.1)':'rgba(241,196,15,0.1)',
          color:isOk?'#2ecc71':isWarn?'#e74c3c':'#f39c12'
        }}>{status}</span>
      </div>
    </div>
  );
}
