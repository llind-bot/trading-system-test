import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer } from 'recharts';
import { formatEasternShort } from '../../lib/tz.js';

/* ── Helpers ─────────────────────────────────────────────────────── */

function StatCard({ label, value, sub, color }) {
  return (
    <div style={{ background:'var(--surface)',padding:'12px 16px',borderRadius:8,border:'1px solid var(--border)' }}>
      <div style={{ fontSize:11,color:'var(--muted)',marginBottom:4 }}>{label}</div>
      <div style={{ fontSize:18,fontWeight:700,color:color||'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize:11,color:'var(--muted)',marginTop:2 }}>{sub}</div>}
    </div>
  );

}

function fmt$(v) {
  if (v == null) return '-';
  return '$' + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/* ── Main Component ─────────────────────────────────────────────── */

export default function EquityCurveReport() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetch('/api/equity/report')
      .then(r => r.json())
      .then(d => { if (!cancelled) setData(d); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading equity curve report...</div>;
  if (!data?.curve?.length) return <div style={{ padding:24, textAlign:'center', color:'var(--muted)' }}>No equity curve data available</div>;

  const { stats, daily } = data;

  // ── Chart data ───────────────────────────────────────────
  const chartData = data.curve.map(pt => ({
    x: pt.timestamp,
    equity: pt.total_equity,
    cash: pt.cash,
    positions_value: pt.positions_value,
  }));

  // Downsample for chart: ~600 points is fine; more than that gets noisy
  const maxPoints = 1200;
  const sampled = chartData.length > maxPoints
    ? chartData.filter((_, i) => i % Math.ceil(chartData.length / maxPoints) === 0)
    : chartData;

  const startEq = stats?.start_equity || 0;
  const endEq = stats?.end_equity || 0;
  const netChange = endEq - startEq;
  const netPct = startEq > 0 ? (netChange / startEq * 100) : 0;

  // ── Renderers ────────────────────────────────────────────

  const renderChart = () => (
    <div style={{ height:340, marginBottom:24 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={sampled} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="x"
            type="category"
            tick={{ fontSize: 10, fill: 'var(--muted)' }}
            tickFormatter={(v) => v.slice(5)} // MM-DD
            minTickGap={30}
          />
          <YAxis
            domain={['auto', 'auto']}
            tick={{ fontSize: 11, fill: 'var(--muted)' }}
            tickFormatter={(v) => '$' + (v >= 1000 ? (v/1000).toFixed(0) + 'k' : v.toFixed(0))}
            width={60}
          />
          <Tooltip
            contentStyle={{ background:'var(--surface)',border:'1px solid var(--border)',borderRadius:8,fontSize:12 }}
            labelFormatter={(v) => `📅 ${formatEasternShort(v)}`}
            formatter={(value, name) => {
              const isMoney = ['equity','cash','positions_value'].includes(name);
              return [fmt$(value), name === 'equity' ? 'Portfolio Equity' : name === 'cash' ? 'Cash' : 'Positions Value'];
            }}
          />
          <ReferenceLine y={startEq} label="Start" stroke="var(--muted)" strokeDasharray="4 4" strokeWidth={1} />
          {/* Net line */}
          {netPct >= 0 ? (
            <Line type="monotone" dataKey="equity" stroke="var(--green)" dot={false} strokeWidth={2} name="Portfolio Equity" />
          ) : (
            <Line type="monotone" dataKey="equity" stroke="var(--red)" dot={false} strokeWidth={2} name="Portfolio Equity" />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );

  const renderDailyTable = () => {
    if (!daily?.length) return null;
    // Reverse so newest first
    const rows = [...daily].reverse();
    const totalGainDays = daily.filter(d => d.net > 0).length;
    const totalLossDays = daily.filter(d => d.net < 0).length;

    return (
      <>
        <h4 style={{ fontSize:13,margin:'16px 0 8px',color:'var(--muted)' }}>Daily Performance ({totalGainDays} gain / {totalLossDays} loss days)</h4>
        <div style={{ overflowX:'auto', maxHeight:360, overflowY:'auto' }}>
          <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12 }}>
            <thead style={{ position:'sticky',top:0,zIndex:1 }}>
              <tr style={{ borderBottom:'1px solid var(--border)',background:'var(--surface)' }}>
                {['Date','Open','High','Low','Close','Net Change','Range'].map(h => (
                  <th key={h} style={{ padding:'6px 8px',textAlign:'right',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
              </tr>
            </thead>
            <tbody>
              {rows.map((d, i) => (
                <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                  <td style={{ padding:'6px 8px',fontWeight:500,color:'var(--muted)',whiteSpace:'nowrap' }}>{d.day}</td>
                  <td style={{ padding:'6px 8px',textAlign:'right' }}>{fmt$(d.open)}</td>
                  <td style={{ padding:'6px 8px',textAlign:'right' }}>{fmt$(d.high)}</td>
                  <td style={{ padding:'6px 8px',textAlign:'right' }}>{fmt$(d.low)}</td>
                  <td style={{ padding:'6px 8px',textAlign:'right',fontWeight:600 }}>{fmt$(d.close)}</td>
                  <td style={{ padding:'6px 8px',textAlign:'right',color:d.net>=0?'var(--green)':'var(--red)',fontWeight:500 }}>
                    {d.net >= 0 ? '+' : ''}{fmt$(d.net)}
                  </td>
                  <td style={{ padding:'6px 8px',textAlign:'right',color:'var(--muted)' }}>{fmt$(d.range)}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </>
    );
  };

  const renderCumulativePnLChart = () => {
    // Compute cumulative P&L from start
    const pnlData = data.curve.map((pt, i) => ({
      x: pt.timestamp,
      pnl: pt.total_equity - startEq,
    }));

    const maxPnlPts = 1200;
    const sampledPnl = pnlData.length > maxPnlPts
      ? pnlData.filter((_, i) => i % Math.ceil(pnlData.length / maxPnlPts) === 0)
      : pnlData;

    return (
      <div style={{ height:200, marginBottom:16 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={sampledPnl} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
            <XAxis
              dataKey="x"
              tick={{ fontSize: 10, fill: 'var(--muted)' }}
              tickFormatter={(v) => v.slice(5)}
              minTickGap={30}
            />
            <YAxis
              tick={{ fontSize: 11, fill: 'var(--muted)' }}
              tickFormatter={(v) => (v >= 0 ? '+' : '') + '$' + v.toFixed(0)}
              width={55}
            />
            <ReferenceLine y={0} stroke="var(--muted)" strokeDasharray="3 3" />
            <Tooltip
              contentStyle={{ background:'var(--surface)',border:'1px solid var(--border)',borderRadius:8,fontSize:12 }}
              formatter={(value) => [fmt$(value), 'Cumulative P&L']}
              labelFormatter={(v) => `📅 ${formatEasternShort(v)}`}
            />
            <Line
              type="monotone"
              dataKey="pnl"
              stroke={netPct >= 0 ? 'var(--green)' : 'var(--red)'}
              dot={false}
              strokeWidth={2}
              name="Cumulative P&L"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  };

  return (
    <>
      <h3 style={{ fontSize:16,marginBottom:8 }}>Equity Curve Report</h3>

      {/* Stats row */}
      <div style={{ display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(170px,1fr))',gap:12,marginBottom:20 }}>
        <StatCard label="Starting Equity" value={fmt$(startEq)} />
        <StatCard label="Ending Equity" value={fmt$(endEq)} />
        <StatCard
          label="Net P&L"
          value={`${netPct >= 0 ? '+' : ''}${fmt$(netChange)}`}
          sub={`${netPct >= 0 ? '+' : ''}${netPct.toFixed(3)}%`}
          color={netPct >= 0 ? 'var(--green)' : 'var(--red)'}
        />
        <StatCard label="Peak Equity" value={fmt$(stats?.peak_equity || 0)} />
        <StatCard
          label="Worst Drawdown"
          value={fmt$(stats?.worst_drawdown || 0)}
          sub={stats?.worst_dd_date ? `on ${stats.worst_dd_date.slice(0,10)}` : ''}
          color="var(--red)"
        />
        <StatCard label="Period" value={`${stats?.period_days || 0} days`} sub={`${stats?.total_points || 0} data points`} />
      </div>

      {/* Main equity curve */}
      {renderChart()}

      {/* Cumulative P&L */}
      <h4 style={{ fontSize:13,margin:'8px 0 8px',color:'var(--muted)' }}>Cumulative P&L from Start</h4>
      {renderCumulativePnLChart()}

      {/* Daily breakdown */}
      {renderDailyTable()}
    </>
  );
}
