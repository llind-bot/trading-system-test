import React, { useState, useEffect } from 'react';
import { fetchReportStrategyHistory } from '../../lib/api.js';

function StatCard({ label, value, color }) {
  return (
    <div style={{ background:'var(--surface)',padding:'14px 16px',borderRadius:8,border:'1px solid var(--border)' }}>
      <div style={{ fontSize:11,color:'var(--muted)',marginBottom:4 }}>{label}</div>
      <div style={{ fontSize:20,fontWeight:700,color:color||'var(--text)' }}>{value}</div>
    </div>
  );
}

export default function StrategyHistoryReport() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchReportStrategyHistory().then(setData).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading strategy history...</div>;
  if (!data?.totals?.length) return <div style={{ padding:24, textAlign:'center', color:'var(--muted)' }}>No strategy data</div>;

  return (
    <>
      <h3 style={{ fontSize:16,marginBottom:8 }}>Strategy Evaluation Summary</h3>
      <div style={{ display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:12,marginBottom:24 }}>
        {data.totals.map(t => (
          <StatCard key={t.vote_result} label={t.vote_result+' Signals'} value={t.cnt}
            color={t.vote_result==='BUY'?'var(--green)':t.vote_result==='SELL'?'var(--red)':'var(--muted)'}/>))}
      </div>

      {data.by_symbol?.length > 0 && (
        <>
          <h4 style={{ fontSize:13,marginBottom:8,color:'var(--muted)' }}>Per-Symbol Breakdown</h4>
          <div style={{ overflowX:'auto' }}>
            <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12 }}>
              <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
                {['Symbol','BUY Count','SELL Count','HOLD Count'].map(h => (
                  <th key={h} style={{ padding:'6px 10px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
              </tr></thead>
              <tbody>
                {(() => {
                  const rows = {};
                  data.by_symbol.forEach(b => {
                    if (!rows[b.symbol]) rows[b.symbol] = { symbol: b.symbol, BUY: 0, SELL: 0, HOLD: 0 };
                    const key = (b.vote_result || '').toUpperCase();
                    if (rows[b.symbol].hasOwnProperty(key)) {
                      rows[b.symbol][key] = b.cnt;
                    }
                  });
                  return Object.values(rows).map(r => (
                    <tr key={r.symbol} style={{ borderBottom:'1px solid var(--border)' }}>
                      <td style={{ padding:'6px 10px',fontWeight:600 }}>{r.symbol}</td>
                      <td style={{ padding:'6px 10px',color:'var(--green)' }}>{r.BUY}</td>
                      <td style={{ padding:'6px 10px',color:'var(--red)' }}>{r.SELL}</td>
                      <td style={{ padding:'6px 10px',color:'var(--muted)' }}>{r.HOLD}</td>
                    </tr>));
                })()}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
