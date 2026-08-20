import React from 'react';

function StatCard({ label, value }) {
  return (
    <div style={{ background:'var(--surface)',padding:'14px 16px',borderRadius:8,border:'1px solid var(--border)' }}>
      <div style={{ fontSize:11,color:'var(--muted)',marginBottom:4 }}>{label}</div>
      <div style={{ fontSize:20,fontWeight:700,color:'var(--text)' }}>{value}</div>
    </div>
  );
}

export default function SystemInfoPage() {
  const BUILD_VERSION = '2026.7.20c';
  return (
    <div style={{ display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:12 }}>
      <StatCard label="Dashboard" value="Port 8081" />
      <StatCard label="API" value="FastAPI + Uvicorn" />
      <StatCard label="Broker" value="Alpaca Paper" />
      <StatCard label="Engine" value="4-process (crypto, stock, order_server, bar_ingest)" />
      <StatCard label="Build Version" value={BUILD_VERSION} />
    </div>
  );
}
