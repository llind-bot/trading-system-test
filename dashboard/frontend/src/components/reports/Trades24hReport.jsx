import React, { useState, useEffect } from 'react';
import { fetchReport24hTrades } from '../../lib/api.js';
import { formatEasternShort } from '../../lib/tz.js';

export default function Trades24hReport() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchReport24hTrades().then(setData).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading last 24h trades...</div>;
  if (!data?.trades?.length) return <div style={{ padding:24, textAlign:'center', color:'var(--muted)' }}>No trades in the last 24 hours</div>;

  // P&L is displayed from the stored values in trades.db.
  // Computation happens ONLY at fill time in src/persistence/transaction_logger.py
  // (compute_and_store_pnl / compute_and_store_pnl_by_algo).
  // The frontend never recomputes or guesses P&L.

  const totalPnl = data.trades.reduce((s, t) => {
    if (t.side === 'sell') return s + parseFloat(t._pnl_dollars ?? 0);
    return s;
  }, 0);

  function fmtTotalAmount(t) {
    if (t.side === 'buy' && t.amount_type === 'dollar') {
      return '$' + parseFloat(t.total_cost || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    if (t.side === 'buy') {
      return '$' + parseFloat(t.qty || 0) * parseFloat(t.price || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    const rev = parseFloat(t.notional || 0);
    if (rev > 0) return '$' + rev.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const fallback = parseFloat(t.qty || 0) * parseFloat(t.price || 0);
    return '$' + (fallback || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function fmtPct(v) {
    if (v == null) return '-';
    return (v >= 0 ? '+' : '') + v.toFixed(3) + '%';
  }

  return (
    <>
      <h3 style={{ fontSize:16,marginBottom:8 }}>Last 24 Hours Trades ({data.trades.length})</h3>
      {totalPnl !== 0 && (
        <div style={{ marginBottom:16,padding:'12px 16px',borderRadius:8 }}>
          <span style={{ color:totalPnl>=0?'var(--green)':'var(--red)',fontWeight:600 }}>Total P&L: ${Math.abs(totalPnl).toFixed(2)}</span>
        </div>
      )}

      <div style={{ overflowX:'auto',background:'var(--surface)',padding:16,borderRadius:8,border:'1px solid var(--border)' }}>
        <table style={{ width:'100%',borderCollapse:'collapse',fontSize:13 }}>
          <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
            {['Symbol','Side','Qty','Price (entry)','Cost Basis ($)','Total Buy/Sell Amount ($)','P&L ($)','P&L (%)'].map(h => (
              <th key={h} style={{ padding:'8px 12px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
          </tr></thead>
          <tbody>
            {data.trades.map((t,i) => {
              const side = t.side || '';
              const isBuy = side === 'buy';
              // Display stored P&L directly — never recompute
              const pnlDollar = t._pnl_dollars ?? 0;
              const pnlPct = t._pnl_pct != null ? parseFloat(t._pnl_pct) : null;

              return (
                <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                  <td style={{ padding:'8px 12',fontWeight:600 }}>{t.symbol}</td>
                  <td style={{ padding:'8px 12',color:side==='buy'?'var(--green)':'var(--red)' }}>{side.toUpperCase()}</td>
                  <td style={{ padding:'8px 12' }}>{(parseFloat(t.qty||0)).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
                  <td style={{ padding:'8px 12' }}>${(t.price!=null ? parseFloat(t.price).toLocaleString(undefined,{maximumFractionDigits:6}) : '-')}</td>
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
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
