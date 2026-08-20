import React, { useState, useEffect } from 'react';
import { fetchTrades } from '../lib/api.js';
import { formatEasternShort } from '../lib/tz.js';

export default function TradesPanel() {
  const [tradesData, setTradesData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [symbolFilter, setSymbolFilter] = useState('');
  const [sideFilter, setSideFilter] = useState('');

  const [classFilter, setClassFilter] = useState('');

  useEffect(() => {
    fetchTrades(500).then(setTradesData).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading trades...</div>;

  const allTrades = tradesData?.trades || [];

  // P&L is displayed from the stored values in trades.db.
  // Computation happens ONLY at fill time in src/persistence/transaction_logger.py
  // (compute_and_store_pnl / compute_and_store_pnl_by_algo).
  // The frontend never recomputes or guesses P&L.

  function totalPnlFromTrades() {
    return allTrades.reduce((s, t) => {
      if (t.side === 'sell') {
        const pnl = t._pnl_dollars ?? 0;
        return s + parseFloat(pnl);
      }
      return s;
    }, 0);
  }

  function fmtTotalAmount(t) {
    let v;
    if (t.side === 'buy') {
      v = parseFloat(t.total_cost || 0);
      if (v <= 0 && parseFloat(t.qty || 0) > 0 && parseFloat(t.price || 0) > 0) {
        v = parseFloat(t.qty) * parseFloat(t.price);
      }
    } else {
      v = parseFloat(t.notional || 0);
      if (v <= 0 && parseFloat(t.qty || 0) > 0 && parseFloat(t.price || 0) > 0) {
        v = parseFloat(t.qty) * parseFloat(t.price);
      }
    }
    return '$' + (v || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtPct(v) {
    if (v == null) return '-';
    return (v >= 0 ? '+' : '') + v.toFixed(3) + '%';
  }

  const totalPnl = totalPnlFromTrades();

  const filtered = allTrades
    .filter(t => {
      if (symbolFilter && !t.symbol?.toLowerCase().includes(symbolFilter.toLowerCase())) return false;
      if (sideFilter && t.side !== sideFilter) return false;
      if (classFilter && t.asset_class !== classFilter) return false;
      return true;
    })
    .reverse();

  return (
    <>
      <h2 style={{ margin:'0 0 16px',fontSize:18 }}>Recent Trades ({tradesData?.total ?? 0})</h2>

      {/* Filters */}
      <div style={{ display:'flex', gap:8, marginBottom:16 }}>
        <input value={symbolFilter} onChange={e=>setSymbolFilter(e.target.value)} placeholder="Filter by symbol..."
          style={{ padding:'6px 12px', background:'var(--surface)', border:'1px solid var(--border)', borderRadius:6, color:'var(--text)' }} />
        <select value={sideFilter} onChange={e=>setSideFilter(e.target.value)} style={{ padding:'6px 12px', background:'var(--surface)', border:'1px solid var(--border)', borderRadius:6, color:'var(--text)' }}>
          <option value="">All Sides</option><option value="buy">Buy</option><option value="sell">Sell</option>
        </select>
        <select value={classFilter} onChange={e=>setClassFilter(e.target.value)} style={{ padding:'6px 12px', background:'var(--surface)', border:'1px solid var(--border)', borderRadius:6, color:'var(--text)' }}>
          <option value="">All Assets</option><option value="stock">Stocks</option><option value="crypto">Crypto</option>
        </select>
        <div style={{ marginLeft:'auto', fontSize:13, color: totalPnl>=0?'var(--green)':'var(--red)', fontWeight:600 }}>
          Total P&L: ${Math.abs(totalPnl).toFixed(2)}{totalPnl >= 0 ? '+' : '-'}
        </div>
      </div>

      <div style={{ overflowX:'auto' }}>
        <table style={{ width:'100%',borderCollapse:'collapse',fontSize:13 }}>
          <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
            {['Symbol','Side','Qty','Price (entry)','Cost Basis ($)','Total Buy/Sell Amount ($)','P&L ($)','P&L (%)','Status','Timestamp'].map(h => (
              <th key={h} style={{ padding:'8px 12px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
              <th style={{ padding:'8px 12px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>Type</th>
          </tr></thead>
          <tbody>
            {filtered.slice(0,100).map((t,i) => {
              const side = t.side || '';
              const isBuy = side === 'buy';
              // Display stored P&L directly — never recompute
              const pnlDollar = t._pnl_dollars ?? 0;
              const pnlPct = t._pnl_pct != null ? parseFloat(t._pnl_pct) : null;

              return (
                <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                  <td style={{ padding:'8px 12',fontWeight:600 }}>
                    {t.symbol}
                    {t.asset_class === 'crypto' && (
                      <span style={{ marginLeft:6, fontSize:10, background:'#7c3aed22', color:'#a78bfa', borderRadius:3, padding:'1px 4px', fontWeight:600 }}>CRYPTO</span>
                    )}
                  </td>
                  <td style={{ padding:'8px 12',color:side==='buy'?'var(--green)':'var(--red)' }}>{side.toUpperCase()}</td>
                  <td style={{ padding:'8px 12' }}>{(parseFloat(t.qty||0)).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
                  <td style={{ padding:'8px 12' }}>${(t.price != null ? parseFloat(t.price).toLocaleString(undefined,{maximumFractionDigits:6}) : '-')}</td>
                  {/* Cost Basis */}
                  <td style={{ padding:'8px 12' }}>
                    {isBuy
                      ? (parseFloat(t.total_cost || 0) > 0 ? '$' + parseFloat(t.total_cost).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}) : '$-')
                      : (t._cost_basis_per_share != null ? '$' + (parseFloat(t._cost_basis_per_share) * parseFloat(t.qty || 0)).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}) : '$-')}
                  </td>
                  {/* Total Buy/Sell Amount */}
                  <td style={{ padding:'8px 12' }}>{fmtTotalAmount(t)}</td>
                  {/* P&L $ */}
                  <td style={{ padding:'8px 12', color: isBuy ? 'var(--muted)' : (pnlDollar>=0?'var(--green)':'var(--red)') }}>
                    {isBuy ? '—' : `${pnlDollar >= 0 ? '+' : '-'}$${Math.abs(pnlDollar).toFixed(2)}`}
                  </td>
                  {/* P&L % */}
                  <td style={{ padding:'8px 12', color: isBuy ? 'var(--muted)' : (pnlPct!=null && pnlPct>=0?'var(--green)':'var(--red)') }}>
                    {isBuy ? '—' : (pnlPct != null ? fmtPct(pnlPct) : '—')}
                  </td>
                  <td style={{ padding:'8px 12',color:'var(--muted)' }}>{t.status || '-'}</td>
                  <td style={{ padding:'8px 12',color:'var(--muted)',fontSize:12 }}>{formatEasternShort(t.timestamp)}</td>
                  <td style={{ padding:'8px 12' }}>
                    {t.asset_class === 'crypto'
                      ? <span style={{ color:'#a78bfa', fontWeight:600, fontSize:12 }}>₿ Crypto</span>
                      : <span style={{ color:'#60a5fa', fontWeight:600, fontSize:12 }}>◻ Stock</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
