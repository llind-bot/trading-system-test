import React, { useState, useEffect } from 'react';
import { fetchReportBars } from '../../lib/api.js';

export default function BarsCoverageReport() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchReportBars().then(setData).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading bar coverage...</div>;
  const bars = Array.isArray(data) ? data : [];

  if (!bars.length) return <div style={{ padding:24, textAlign:'center', color:'var(--muted)' }}>No bar data available</div>;

  function formatLastSeen(ts) {
    if (!ts) return '-';
    try {
      const d = new Date(ts);
      const mm = String(d.getMonth()+1).padStart(2,'0');
      const dd = String(d.getDate()).padStart(2,'0');
      const yyyy = d.getFullYear();
      let hh = d.getHours();
      const mi = String(d.getMinutes()).padStart(2,'0');
      const ampm = hh >= 12 ? 'PM' : 'AM';
      hh = hh % 12 || 12;
      return `${mm}/${dd}/${yyyy} ${hh}:${mi} ${ampm}`;
    } catch { return ts; }
  }

  function freshnessInfo(bar) {
    const age = bar._freshness_sec ?? bar.freshness_sec ?? null;
    if (age == null) return { label: '—', color: 'var(--muted)', bg: 'transparent' };
    // Trust the API's is_stale — it has the market-hour exemption logic
    const is_stale = bar.is_stale;
    const threshold = bar.stale_threshold_sec || 300;
    if (!is_stale) {
      return { label: `${age}s`, color: '#2ecc71', bg: 'rgba(46,204,113,0.08)' };
    }
    const headroom = Math.max(0, threshold - age);
    // Stale
    return { label: `${age}s ⚠`, color: '#e74c3c', bg: 'rgba(231,76,60,0.1)' };
  }

  return (
    <>
      <h3 style={{ fontSize:16,marginBottom:8 }}>Bar Data Coverage</h3>
      <div style={{ overflowX:'auto',background:'var(--surface)',padding:16,borderRadius:8,border:'1px solid var(--border)' }}>
        <table style={{ width:'100%',borderCollapse:'collapse',fontSize:13 }}>
          <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
            {['Symbol','Timeframe','Bar Count','Price Range','Volume','Last Seen','Freshness'].map(h => (
              <th key={h} style={{ padding:'8px 12px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
          </tr></thead>
          <tbody>
            {bars.map((b,i) => {
              const fx = freshnessInfo(b);
              return (
              <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                <td style={{ padding:'8px 12',fontWeight:600 }}>{b.symbol}</td>
                <td style={{ padding:'8px 12',color:'var(--muted)' }}>{b.timeframe || b.timeframe_str || '-'}</td>
                <td style={{ padding:'8px 12' }}>{(b.bar_count!=null ? b.bar_count.toLocaleString() : '-')}</td>
                <td style={{ padding:'8px 12',color:'var(--muted)' }}>
                  {b.high != null ? '$'+b.high.toFixed(4) : '-'} - {b.low != null ? '$'+b.low.toFixed(4) : '-'}
                </td>
                <td style={{ padding:'8px 12',color:'var(--muted)' }}>{(b.total_volume||b.volume||'-').toLocaleString()}</td>
                <td style={{ padding:'8px 12',color:'var(--muted)',fontFamily:'monospace' }}>{formatLastSeen(b.last_seen)}</td>
                <td style={{ padding:'8px 12', color: fx.color, background: fx.bg, borderRadius:4 }}>
                  {fx.label}
                </td>
              </tr>);})}
          </tbody>
        </table>
      </div>
    </>
  );
}
