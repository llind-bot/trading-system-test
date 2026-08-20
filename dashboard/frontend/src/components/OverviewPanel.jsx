import React, { useState, useEffect } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer } from 'recharts';
import { fetchPositions, fetchEquitySnapshot, fetchEngineStatus, fetchEquityReport } from '../lib/api.js';
import { formatEasternShort } from '../lib/tz.js';

/* ── tiny helpers ─────────────────────────────────────────────── */
function green(v) { return v >= 0 ? 'var(--green)' : 'var(--red)' };

function StatCard({ label, value, color, sub }) {
  return (
    <div style={{ background: 'var(--surface)', padding: '12px 14px', borderRadius: 8, border: '1px solid var(--border)' }}>
      <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</div>
      <div style={{ fontSize: 17, fontWeight: 700, color: color || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 3 }}>{sub}</div>}
    </div>
  );
}

/* ── single engine status tile (for individual engines) ───────── */
function EngineCard({ name, info }) {
  const isRunning = !!info?.running;
  const color = isRunning ? 'var(--green)' : 'var(--red)';
  const sub = info?.pid
    ? `PID ${info.pid}${info.uptime ? ` · ${info.uptime}` : ''}`
    : 'Stopped';

  return (
    <StatCard
      label={name}
      value={isRunning ? 'Running' : 'Stopped'}
      color={color}
      sub={sub}
    />
  );
}

/* ── equity curve chart (Recharts — same as report tab) ─────── */
function MiniEquityCurve({ data, startEquity }) {
  if (!data || !data.length) return null;

  const sorted = [...data].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  const chartData = sorted.map(pt => ({
    time: pt.timestamp,
    pnl: pt.total_equity - startEquity,
  }));

  return (
    <div style={{ background: 'var(--surface)', borderRadius: 8, border: '1px solid var(--border)', padding: '12px 16px' }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--blue)', marginBottom: 8 }}>Cumulative P&L from Start</div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={chartData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="time"
            tick={{ fontSize: 10, fill: 'var(--muted)' }}
            tickFormatter={(v) => v.slice(5)}
            minTickGap={30}
            label={{ value: 'Date', position: 'insideBottomRight', offset: -5, fontSize: 11, fill: 'var(--muted)' }}
          />
          <YAxis
            tick={{ fontSize: 11, fill: 'var(--muted)' }}
            tickFormatter={(v) => (v >= 0 ? '+' : '') + '$' + v.toFixed(0)}
            width={65}
            label={{ value: 'P&L ($)', angle: -90, position: 'insideLeft', offset: 5, fontSize: 11, fill: 'var(--muted)' }}
          />
          <ReferenceLine y={0} stroke="var(--muted)" strokeDasharray="3 3" />
          <Tooltip
            contentStyle={{ background:'var(--surface)',border:'1px solid var(--border)',borderRadius:8,fontSize:12 }}
            labelFormatter={(v) => `📅 ${formatEasternShort(v)}`}
            formatter={(value) => [`$${value.toFixed(2)}`, 'Cumulative P&L']}
          />
          <Line
            type="monotone"
            dataKey="pnl"
            stroke={chartData[chartData.length - 1]?.pnl >= 0 ? 'var(--green)' : 'var(--red)'}
            dot={false}
            strokeWidth={2}
            name="Cumulative P&L"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ── mini positions table (for overview) ──────────────────────── */
function MiniPositionsTable({ positions, green }) {
  if (!positions?.length) return <div style={{ padding: 16, color: 'var(--muted)', textAlign: 'center' }}>No open positions</div>;

  const totalCostBasis = positions.reduce((s, p) => s + (p.cost_basis || 0), 0);
  const totalCurrentValue = positions.reduce((s, p) => s + (p.current_value || 0), 0);
  const totalUnrealizedPnl = positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);

  function fNum(v, opts = {}) {
    const defaults = { minimumFractionDigits: 2, maximumFractionDigits: 2 };
    return v == null ? '-' : '$' + v.toLocaleString(undefined, { ...defaults, ...opts });
  }
  function fQty(v) { return v == null ? '-' : v.toLocaleString(undefined, { maximumFractionDigits: 4 }); }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead><tr style={{ borderBottom: '1px solid var(--border)' }}>
          {['Asset','Current Price','Qty','Avg Entry Price','Total Cost Basis','Current Total Market Value','Total P&L ($)','Total P&L (%)'].map(h => (
            <th key={h} style={{ padding:'8px 12px', textAlign:'left', color:'var(--muted)', fontWeight:500 }}>{h}</th>))}
        </tr></thead>
        <tbody>
          {positions.map(p => (
            <tr key={p.symbol} style={{ borderBottom:'1px solid var(--border)' }}>
              <td style={{ padding:'8px 12', fontWeight:600 }}>{p.symbol}</td>
              <td style={{ padding:'8px 12' }}>{fNum(p.current_price)}</td>
              <td style={{ padding:'8px 12' }}>{fQty(p.qty)}</td>
              <td style={{ padding:'8px 12' }}>{fNum(p.avg_cost)}</td>
              <td style={{ padding:'8px 12',color:'var(--muted)' }}>{fNum(p.cost_basis)}</td>
              <td style={{ padding:'8px 12',color:'var(--muted)' }}>{fNum(p.current_value)}</td>
              <td style={{ padding:'8px 12', color:green(p.unrealized_pnl) }}>{p.unrealized_pnl != null ? (p.unrealized_pnl>=0?'+':'')+'$'+Math.abs(p.unrealized_pnl).toFixed(2) : '-'}</td>
              <td style={{ padding:'8px 12', color:green(p.unrealized_pnl_pct), fontWeight:600 }}>
                {p.unrealized_pnl_pct != null ? p.unrealized_pnl_pct.toFixed(2)+'%' : '-'}
              </td>
            </tr>))}
          {/* Totals row */}
          <tr style={{ borderTop:'2px solid var(--border)', fontWeight:700, background:'var(--surface)' }}>
            <td colSpan={5} style={{ padding:'10px 12px',color:'var(--muted)' }}>TOTAL PORTFOLIO</td>
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
  );
}

/* ── Main Overview Panel ─────────────────────────────────────── */
export default function OverviewPanel() {
  const [positions, setPositions] = useState(null);
  const [equitySnap, setEquitySnap] = useState(null);
  const [engineStatus, setEngineStatus] = useState(null);
  const [equityReport, setEquityReport] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetchPositions().then(setPositions).catch(()=>{}),
      fetchEquitySnapshot().then(setEquitySnap).catch(()=>{}),
      fetchEngineStatus().then(s => setEngineStatus(s || null)).catch(()=>{}),
      fetchEquityReport().then(setEquityReport).catch(()=>{}),
    ]).finally(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading overview...</div>;

  const cashVal = equitySnap?.snapshot?.cash ?? null;
  const bpVal = equitySnap?.account?.buying_power ?? null;
  const posVal = positions?.reduce((s, p) => s + (p.current_value || 0), 0) ?? 0;

  // Use equity report for correct P&L and full curve data
  const hasData = equityReport?.curve?.length;
  const stats = equityReport?.stats || {};
  const startEquity = stats.start_equity || 0;
  const endEquity = stats.end_equity || 0;
  const netChange = endEquity - startEquity;
  const netPct = startEquity > 0 ? (netChange / startEquity * 100) : 0;
  const latestPnlColor = netPct >= 0 ? 'var(--green)' : 'var(--red)';

  // Full curve for chart, sorted ascending
  const fullCurve = hasData
    ? [...equityReport.curve].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
    : [];

  function fmtMoney(v) {
    if (v == null) return '-';
    return '$' + v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  // Get engine data — handles both old single-engine and new multi-process formats
  const engines = engineStatus?.engines || {};
  const anyRunning = engineStatus?.any_running;

  return (
    <>
      {/* Row 1: Engine cards + Net P&L + Cash */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8, marginBottom: 8 }}>
        <EngineCard name="Bar Ingest" info={engines['bar_ingest']} />
        <EngineCard name="Crypto Engine" info={engines['crypto_engine']} />
        <EngineCard name="Stock Engine" info={engines['stock_engine']} />
        <EngineCard name="Order Server" info={engines['order_server']} />
      </div>

      {/* Overall system status + portfolio stats */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 24 }}>
        <StatCard
          label="System Status"
          value={anyRunning ? 'All Good' : 'Stopped'}
          color={anyRunning ? 'var(--green)' : 'var(--red)'}
          sub={`${Object.values(engines).filter(e => e?.running).length}/${Object.keys(engines).length} processes running`}
        />
        {hasData ? (
          <StatCard
            label="Net P&L"
            value={`${netPct >= 0 ? '+' : ''}$${Math.abs(netChange).toFixed(2)}`}
            sub={`${netPct >= 0 ? '+' : ''}${netPct.toFixed(2)}%`}
            color={latestPnlColor}
          />
        ) : null}
        <StatCard label="Cash" value={fmtMoney(cashVal)} />
        <StatCard label="Positions Value" value={fmtMoney(posVal)} sub={(positions?.length ?? 0)+' open'} />
      </div>

      {/* Cumulative P&L from Start (same calc as report tab) */}
      {hasData && (
        <MiniEquityCurve data={fullCurve} startEquity={startEquity} />
      )}

      <h3 style={{ fontSize:14, margin:'24px 0 8px' }}>Positions ({positions?.length ?? 0})</h3>
      <MiniPositionsTable positions={positions} green={green} />
    </>
  );
}
