import React, { useState, useEffect } from 'react';
import { fetchStrategiesFull } from '../../lib/api.js';

export default function StrategiesSettingsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchStrategiesFull().then(d => { setData(d); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  if (loading) return <div style={{ color:'var(--muted)' }}>Loading strategies...</div>;
  const strategies = data?.available_strategies || [];

  return (
    <>
      {strategies.length === 0 && <div style={{ color:'var(--muted)' }}>No strategies found in src/strategies/.</div>}
      {strategies.map(s => (
        <div key={s.name} style={{ background:'var(--surface)',padding:16,borderRadius:8,border:'1px solid var(--border)',marginBottom:12 }}>
          <h3 style={{ fontSize:15,margin:'0 0 4px' }}>{s.name}</h3>
          {s.description && <div style={{ fontSize:12,color:'var(--muted)',marginBottom:8 }}>{s.description}</div>}
          <div style={{ display:'flex',gap:16,flexWrap:'wrap' }}>
            {Object.entries(s.default_params).length > 0 && (
              <span style={{ fontSize:12 }}>
                <span style={{ color:'var(--muted)' }}>Default params:</span>{' '}
                {Object.entries(s.default_params).map(([k,v]) => `${k}=${v}`).join(', ')}
              </span>
            )}
            <span style={{ fontSize:12 }}><span style={{ color:'var(--muted)' }}>Warmup:</span> {s.warm_up_bars_needed} bars</span>
          </div>
        </div>
      ))}
    </>
  );
}
