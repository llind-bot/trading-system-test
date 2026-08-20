import React, { useState, useEffect } from 'react';
import { fetchEngineStatus, fetchRecentEvents, fetchCycleHistory, restartEngine } from '../lib/api.js';
import { formatEasternShort } from '../lib/tz.js';

/* ── single engine tile for the engine tab ───────────────────── */
function EngineTile({ name, info }) {
  const isRunning = !!info?.running;
  return (
    <div style={{ background: 'var(--surface)', padding: '12px 16px', borderRadius: 8, border: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12 }}>
      <span style={{ width: 10, height: 10, borderRadius: '50%', background: isRunning ? 'var(--green)' : 'var(--red)' }}></span>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{name}</div>
        <div style={{ fontSize: 11, color: 'var(--muted)' }}>
          {isRunning ? `PID ${info.pid}${info.uptime ? ` · ${info.uptime}` : ''}` : 'Stopped'}
        </div>
      </div>
    </div>
  );
}

export default function EnginePanel() {
  const [engineStatus, setEngineStatus] = useState(null);
  const [events, setEvents] = useState(null);
  const [cycles, setCycles] = useState(null);
  const [loading, setLoading] = useState(true);
  const [restartMsg, setRestartMsg] = useState('');

  useEffect(() => {
    Promise.all([
      fetchEngineStatus().then(setEngineStatus).catch(()=>{}),
      Promise.all([fetchRecentEvents(50).catch(()=>[]), fetchCycleHistory(20).catch(()=>[])]).then(([ev,c]) => {
        setEvents(ev);
        setCycles(c);
        setLoading(false);
      }),
    ]);
  }, []);

  const handleRestart = async () => {
    if (!confirm('Restart all engine processes? (bar_ingest, crypto_engine, stock_engine, order_server)')) return;
    setRestartMsg('Restarting...');
    try {
      await restartEngine();
      setRestartMsg('Restart initiated. Refresh in a few seconds.');
    } catch(e) { alert('Error: '+e.message); setRestartMsg(''); }
  };

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading engine status...</div>;

  const engines = engineStatus?.engines || {};
  const anyRunning = engineStatus?.any_running;

  return (
    <>
      <h2 style={{ margin:'0 0 16px',fontSize:18 }}>Engine</h2>

      {/* System status bar */}
      <div style={{ background:'var(--surface)',padding:16,borderRadius:8,border:'1px solid var(--border)',marginBottom:20,display:'flex',alignItems:'center',gap:16 }}>
        <span style={{width:12,height:12,borderRadius:'50%',background:anyRunning?'var(--green)':'var(--red)'}}></span>
        <span style={{fontSize:14,fontWeight:600}}>
          {anyRunning ? 'System Running' : 'All Stopped'}
        </span>
        <span style={{color:'var(--muted)',fontSize:13}}>
          ({Object.values(engines).filter(e => e?.running).length}/{Object.keys(engines).length} processes)
        </span>
        {restartMsg && (
          <span style={{ color:'var(--amber)', fontSize:12, fontWeight:600 }}>{restartMsg}</span>
        )}
        <button onClick={handleRestart} style={{ marginLeft:'auto',padding:'6px 16px',background:'var(--amber)',border:'none',borderRadius:6,color:'#000',fontWeight:600,cursor:'pointer' }}>Restart All Engines</button>
      </div>

      {/* Individual engine process tiles */}
      <h3 style={{ fontSize:14,marginBottom:8 }}>Engine Processes</h3>
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(260px,1fr))', gap:8, marginBottom:24 }}>
        <EngineTile name="Bar Ingest (WS Feed)" info={engines['bar_ingest']} />
        <EngineTile name="Crypto Engine" info={engines['crypto_engine']} />
        <EngineTile name="Stock Engine" info={engines['stock_engine']} />
        <EngineTile name="Order Server" info={engines['order_server']} />
      </div>

      {/* Cycle history */}
      <h3 style={{ fontSize:14,marginBottom:8 }}>Recent Cycles</h3>
      <div style={{ background:'var(--surface)',padding:12,borderRadius:8,border:'1px solid var(--border)',maxHeight:200,overflowY:'auto',fontSize:12,marginBottom:24 }}>
        {cycles?.map((c,i) => (
          <div key={i} style={{ padding:'3px 0', borderBottom:'1px solid var(--border)' }}>
            <span style={{ color:'var(--muted)' }}>{c.cycle_id}</span> — {c.events?.map(e => `${e.event_type}: ${e.count}`).join(' | ') || ''}
          </div>
        ))}
      </div>

      {/* Event log */}
      <h3 style={{ fontSize:14,marginBottom:8 }}>Recent Events</h3>
      <div style={{ background:'var(--surface)',padding:12,borderRadius:8,border:'1px solid var(--border)',maxHeight:300,overflowY:'auto',fontFamily:'monospace',fontSize:12 }}>
        {(events||[]).map((e,i) => (
          <div key={i} style={{ padding:'4px 0', borderBottom:'1px solid var(--border)' }}>
            <span style={{ color:'var(--muted)' }}>{formatEasternShort(e.timestamp)}</span> [{e.event_type}] {e.details || ''}
          </div>
        ))}
      </div>
    </>
  );
}
