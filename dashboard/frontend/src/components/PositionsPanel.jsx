import React, { useState, useEffect } from 'react';
import { fetchPositions } from '../lib/api.js';

export default function PositionsPanel() {
  const [positions, setPositions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [assetFilter, setAssetFilter] = useState('all');

  useEffect(() => {
    fetchPositions().then(setPositions).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading positions...</div>;
  if (!positions?.length) return <div style={{ padding:24, textAlign:'center', color:'var(--muted)' }}>No open positions</div>;

  const filtered = assetFilter === 'all'
    ? positions
    : positions.filter(p => p.asset_class === assetFilter);
  
  function fNum(v, opts = {}) {
    const defaults = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
    return v == null ? '-' : '$' + v.toLocaleString(undefined, { ...defaults, ...opts });
  }
  function fQty(v) { return v == null ? '-' : v.toLocaleString(undefined, { maximumFractionDigits: 4 }); }
  function fPct(v) { return v != null ? v.toFixed(2)+'%' : '-'; }

  const sorted = [...filtered].sort((a,b) => (b.unrealized_pnl_pct||0) - (a.unrealized_pnl_pct||0));

  // Portfolio totals (of filtered set)
  const totalQty = sorted.reduce((s,p)=>s+p.qty,0);
  const totalCostBasis = sorted.reduce((s,p)=>s+(p.cost_basis||0),0);
  const totalCurrentValue = sorted.reduce((s,p)=>s+(p.current_value||0),0);
  const totalUnrealizedPnl = sorted.reduce((s,p)=>s+(p.unrealized_pnl||0),0);

  return (
    <>
      <div style={{ display:'flex', alignItems:'center', gap:12, marginBottom:16 }}>
        <h2 style={{ margin:0,fontSize:18 }}>Positions</h2>
        <select
          value={assetFilter}
          onChange={e => setAssetFilter(e.target.value)}
          style={{
            padding:'4px 10px',fontSize:12,borderRadius:4,
            border:'1px solid var(--border)',background:'var(--surface)',color:'var(--text)'
          }}
        >
          <option value="all">All Asset Types</option>
          <option value="stock">Stocks Only</option>
          <option value="crypto">Crypto Only</option>
        </select>
      </div>

      <div style={{ overflowX:'auto' }}>
        <table style={{ width:'100%',borderCollapse:'collapse',fontSize:13 }}>
          <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
            {['Asset','Type','Current Price','Qty','Avg Entry Price','Total Cost Basis','Current Total Market Value','Total P&L ($)','Total P&L (%)'].map(h => (
              <th key={h} style={{ padding:'8px 12px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
          </tr></thead>
          <tbody>
            {sorted.map(p => (
              <tr key={p.symbol} style={{ borderBottom:'1px solid var(--border)' }}>
                <td style={{ padding:'8px 12',fontWeight:600 }}>{p.symbol}</td>
                <td style={{ padding:'8px 12' }}>
                  <span style={{
                    padding:'2px 6px',borderRadius:3,fontSize:10,fontWeight:600,
                    background:p.asset_class==='crypto'?'rgba(245,171,53,0.15)':'rgba(99,102,241,0.15)',
                    color:p.asset_class==='crypto'?'#f5ab35':'#6366f1',textTransform:'uppercase'
                  }}>{p.asset_class}</span>
                </td>
                <td style={{ padding:'8px 12' }}>{fNum(p.current_price)}</td>
                <td style={{ padding:'8px 12' }}>{fQty(p.qty)}</td>
                <td style={{ padding:'8px 12' }}>{fNum(p.avg_cost)}</td>
                <td style={{ padding:'8px 12',color:'var(--muted)' }}>{fNum(p.cost_basis)}</td>
                <td style={{ padding:'8px 12',color:'var(--muted)' }}>{fNum(p.current_value)}</td>
                <td style={{ padding:'8px 12',color:p.unrealized_pnl>=0?'var(--green)':'var(--red)' }}>
                  {p.unrealized_pnl != null ? '$'+(p.unrealized_pnl>=0?'+':'')+(Math.abs(p.unrealized_pnl).toFixed(2)) : '-'}
                </td>
                <td style={{ padding:'8px 12',color:p.unrealized_pnl_pct>=0?'var(--green)':'var(--red)',fontWeight:600 }}>
                  {fPct(p.unrealized_pnl_pct)}
                </td>
              </tr>))}
            {/* Totals row — inside tbody */}
            <tr style={{ borderTop:'2px solid var(--border)', fontWeight:700, background:'var(--surface)' }}>
              <td colSpan={6} style={{ padding:'10px 12px',color:'var(--muted)' }}>{assetFilter === 'all' ? 'TOTAL PORTFOLIO' : `TOTAL ${assetFilter.toUpperCase()}`}</td>
              <td style={{ padding:'10px 12',color:'var(--muted)' }}>{fNum(totalCurrentValue)}</td>
              <td style={{ padding:'10px 12',color:totalUnrealizedPnl>=0?'var(--green)':'var(--red)' }}>
                {totalUnrealizedPnl != null ? (totalUnrealizedPnl>=0?'+':'')+'$'+Math.abs(totalUnrealizedPnl).toFixed(2) : '-'}
              </td>
              <td style={{ padding:'10px 12',color:totalUnrealizedPnl!=0 && totalUnrealizedPnl>=0?'var(--green)':'var(--red)' }}>
                {totalCostBasis > 0 ? (totalUnrealizedPnl/totalCostBasis*100).toFixed(2)+'%' : '-'}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Summary pills */}
      <div style={{ display:'flex',gap:12,marginTop:16,fontSize:12 }}>
        {['all','stock','crypto'].map(type => {
          const count = type === 'all' ? positions.length : positions.filter(p => p.asset_class === type).length;
          const pnl = type === 'all' ? positions.reduce((s,p)=>s+(p.unrealized_pnl||0),0) : positions.filter(p => p.asset_class === type).reduce((s,p)=>s+(p.unrealized_pnl||0),0);
          return (
            <span key={type} style={{
              padding:'4px 10px',borderRadius:4,border:'1px solid var(--border)',
              color:pnl>=0 ? 'var(--green)' : 'var(--red)'
            }}>{count} {type}{count !== 1 ? 's' : ''} · P&L {pnl<0?'-':''}${Math.abs(pnl).toFixed(2)}</span>
          );
        })}
      </div>
    </>
  );
}
