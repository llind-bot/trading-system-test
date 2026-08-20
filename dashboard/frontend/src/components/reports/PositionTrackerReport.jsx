import React, { useState, useEffect } from 'react';
import { fetchPositions } from '../../lib/api.js';

function StatCard({ label, value, color }) {
  return (
    <div style={{ background:'var(--surface)',padding:'14px 16px',borderRadius:8,border:'1px solid var(--border)' }}>
      <div style={{ fontSize:11,color:'var(--muted)',marginBottom:4 }}>{label}</div>
      <div style={{ fontSize:20,fontWeight:700,color:color||'var(--text)' }}>{value}</div>
    </div>
  );
}

export default function PositionTrackerReport() {
  const [positions, setPositions] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filterAsset, setFilterAsset] = useState('all');
  const [filterSymbol, setFilterSymbol] = useState('');

  useEffect(() => {
    fetchPositions().then(setPositions).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading position tracker...</div>;
  if (!positions?.length) return <div style={{ padding:24, textAlign:'center', color:'var(--muted)' }}>No positions</div>;

  // Derive available asset classes
  const assetClasses = [...new Set(positions.map(p => p.asset_class || 'unknown'))];

  // Apply filters
  let filtered = positions;
  if (filterAsset !== 'all') {
    filtered = filtered.filter(p => (p.asset_class || '').toLowerCase().replace(/_/g,' ') === filterAsset.toLowerCase());
  }
  if (filterSymbol.trim()) {
    const q = filterSymbol.trim().toUpperCase();
    filtered = filtered.filter(p => p.symbol.toUpperCase().includes(q));
  }

  // Compute totals from filtered data
  const totalCostBasis = filtered.reduce((s,p)=>s+(p.cost_basis||0),0);
  const totalCurrentValue = filtered.reduce((s,p)=>s+(p.current_value||0),0);
  const totalUnrealized = filtered.reduce((s,p)=>s+(p.unrealized_pnl||0),0);

  function fNum(v, opts = {}) {
    const defaults = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
    return v == null ? '-' : '$' + v.toLocaleString(undefined, { ...defaults, ...opts });
  }
  function fQty(v) { return v == null ? '-' : v.toLocaleString(undefined, { maximumFractionDigits: 4 }); }

  const hasFilters = filterAsset !== 'all' || filterSymbol.trim();

  return (
    <>
      <h3 style={{ fontSize:16,marginBottom:8 }}>Position Tracker</h3>
      <div style={{ display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:12,marginBottom:20 }}>
        <StatCard label="Total Portfolio Cost Basis" value={'$'+totalCostBasis.toLocaleString(undefined,{maximumFractionDigits:2})}/>
        <StatCard label="Total Portfolio Current Value" value={'$'+totalCurrentValue.toLocaleString(undefined,{maximumFractionDigits:2})}/>
        <StatCard label="Total Unrealized P&L" value={'$'+totalUnrealized.toFixed(2)} color={totalUnrealized>=0?'var(--green)':'var(--red)'}/>
      </div>

      {/* Filters */}
      <div style={{ display:'flex',gap:12,marginBottom:16,flexWrap:'wrap',alignItems:'center' }}>
        <label style={{ fontSize:12,color:'var(--muted)' }}>Asset:
          <select value={filterAsset} onChange={e => setFilterAsset(e.target.value)}
            style={{ marginLeft:6,padding:'4px 8px',borderRadius:4,border:'1px solid var(--border)',fontSize:12 }}>
            <option value="all">All</option>
            {assetClasses.map(a => (
              <option key={a} value={a}>{a.charAt(0).toUpperCase() + a.slice(1).replace(/_/g,' ')}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize:12,color:'var(--muted)' }}>Search symbol:
          <input type="text" value={filterSymbol} onChange={e => setFilterSymbol(e.target.value)}
            placeholder="e.g. SOL" style={{ marginLeft:6,padding:'4px 8px',borderRadius:4,border:'1px solid var(--border)',fontSize:12,width:140 }}/>
        </label>
        {hasFilters && (
          <button onClick={() => { setFilterAsset('all'); setFilterSymbol(''); }}
            style={{ padding:'4px 10px',borderRadius:4,border:'1px solid var(--border)',background:'transparent',fontSize:12,cursor:'pointer' }}>
            Clear filters
          </button>
        )}
        <span style={{ fontSize:12,color:'var(--muted)' }}>{filtered.length} of {positions.length} positions</span>
      </div>

      {/* Detail table */}
      <div style={{ overflowX:'auto' }}>
        <table style={{ width:'100%',borderCollapse:'collapse',fontSize:13 }}>
          <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
            {['Asset','Current Price','Qty','Avg Entry Price','Total Cost Basis','Current Total Market Value','Total P&L ($)','Total P&L (%)'].map(h => (
              <th key={h} style={{ padding:'8px 12px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
          </tr></thead>
          <tbody>
            {filtered.map(p => (
              <tr key={p.symbol} style={{ borderBottom:'1px solid var(--border)' }}>
                <td style={{ padding:'8px 12',fontWeight:600 }}>{p.symbol}</td>
                <td style={{ padding:'8px 12' }}>{fNum(p.current_price)}</td>
                <td style={{ padding:'8px 12' }}>{fQty(p.qty)}</td>
                <td style={{ padding:'8px 12' }}>{fNum(p.avg_cost)}</td>
                <td style={{ padding:'8px 12',color:'var(--muted)' }}>{fNum(p.cost_basis)}</td>
                <td style={{ padding:'8px 12',color:'var(--muted)' }}>{fNum(p.current_value)}</td>
                <td style={{ padding:'8px 12',color:p.unrealized_pnl>=0?'var(--green)':'var(--red)' }}>
                  {p.unrealized_pnl != null ? (p.unrealized_pnl>=0?'+':'')+'$'+Math.abs(p.unrealized_pnl).toFixed(2) : '-'}
                </td>
                <td style={{ padding:'8px 12',color:p.unrealized_pnl_pct>=0?'var(--green)':'var(--red)',fontWeight:600 }}>
                  {p.unrealized_pnl_pct != null ? p.unrealized_pnl_pct.toFixed(2)+'%' : '-'}
                </td>
              </tr>))}
            {/* Totals row for filtered data */}
            <tr style={{ borderTop:'2px solid var(--border)', fontWeight:700, background:'var(--surface)' }}>
              <td colSpan={5} style={{ padding:'10px 12px',color:'var(--muted)' }}>TOTAL PORTFOLIO</td>
              <td style={{ padding:'10px 12',color:'var(--muted)' }}>{fNum(totalCurrentValue)}</td>
              <td style={{ padding:'10px 12',color:totalUnrealized>=0?'var(--green)':'var(--red)' }}>
                {totalUnrealized != null ? (totalUnrealized>=0?'+':'')+'$'+Math.abs(totalUnrealized).toFixed(2) : '-'}
              </td>
              <td style={{ padding:'10px 12',color:totalUnrealized!=0 && totalUnrealized>=0?'var(--green)':'var(--red)' }}>
                {totalCostBasis > 0 ? (totalUnrealized/totalCostBasis*100).toFixed(2)+'%' : '-'}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </>
  );
}
