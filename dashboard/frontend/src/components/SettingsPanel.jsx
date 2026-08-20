import React, { useState } from 'react';
import WatchlistSettings from './settings/WatchlistSettings.jsx';
import StrategiesSettingsPage from './settings/StrategiesSettingsPage.jsx';
import SystemInfoPage from './settings/SystemInfoPage.jsx';

const TABS = [
  {id:'watchlist',label:'Watchlist'},
  {id:'strategies',label:'Strategies'},
  {id:'system',label:'System Info'},
];

export default function SettingsPanel() {
  const [active, setActive] = useState('watchlist');

  return (
    <>
      <h2 style={{ margin:'0 0 16px',fontSize:18 }}>Settings</h2>
      <div style={{ display:'flex',gap:4,marginBottom:20,borderBottom:'1px solid var(--border)' }}>
        {TABS.map(t => (
          <button key={t.id} onClick={()=>setActive(t.id)} style={{
            padding:'6px 14px',background:active===t.id?'var(--blue)':'transparent',
            color:active===t.id?'#fff':'var(--muted)',border:'none',
            borderBottom:active===t.id?'2px solid var(--blue)':'2px solid transparent',
            cursor:'pointer',fontSize:12,fontWeight:active===t.id?600:400,borderRadius:'4px 4px 0 0'
          }}>{t.label}</button>))}
      </div>

      {active === 'watchlist' && <WatchlistSettings />}
      {active === 'strategies' && <StrategiesSettingsPage />}
      {active === 'system' && <SystemInfoPage />}
    </>
  );
}
