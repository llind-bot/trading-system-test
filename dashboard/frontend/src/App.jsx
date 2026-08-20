import { useEffect, useRef, useState } from 'react'
import './index.css'
import OverviewPanel from './components/OverviewPanel.jsx'
import PositionsPanel from './components/PositionsPanel.jsx'
import TradesPanel from './components/TradesPanel.jsx'
import StrategiesPanel from './components/StrategiesPanel.jsx'
import ReportsPanel from './components/ReportsPanel.jsx'
import StrategyLabPanel from './components/StrategyLabPanel.jsx'
import EnginePanel from './components/EnginePanel.jsx'
import SettingsPanel from './components/SettingsPanel.jsx'
import LogsPanel from './components/LogsPanel.jsx'

function App() {
  const [activeTab, setActiveTab] = useState('overview')
  const [equitySnap, setEquitySnap] = useState(null)
  const [wsStatus, setWsStatus] = useState('disconnected')

  useEffect(() => {
    fetch('/api/equity/snapshot').then(r => r.json()).then(setEquitySnap).catch(() => {})
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(proto + '//' + location.host + '/ws/live')
    ws.onopen = () => setWsStatus('connected')
    ws.onclose = () => setWsStatus('disconnected')
  }, [])

  const tabs = [
    { id: 'overview', label: 'Overview' }, { id: 'positions', label: 'Positions' },
    { id: 'trades', label: 'Trades' }, { id: 'strategies', label: 'Strategies' },
    { id: 'reports', label: 'Reports' }, { id: 'simulation', label: 'Simulation' },
    { id: 'engine', label: 'Engine' }, { id: 'logs', label: 'Logs' }, { id: 'settings', label: 'Settings' },
  ]

  return (
    <div style={{ minHeight: '100vh' }}>
      <header style={{ display:'flex',alignItems:'center',justifyContent:'space-between',padding:'12px 24px',background:'var(--surface)',borderBottom:'1px solid var(--border)' }}>
        <div style={{ display:'flex',alignItems:'center',gap:16 }}>
          <h1 style={{ margin:0,fontSize:18,fontWeight:600 }}>Trading Dashboard <span style={{ fontSize:11,fontWeight:400,color:'var(--muted)',marginLeft:8 }}>v1.0.0-pnlfix</span></h1>
          <span style={{ fontSize:11,padding:'2px 8px',borderRadius:99,background:wsStatus==='connected'?'var(--green)':'var(--red)',color:'#fff' }}>
            {wsStatus === 'connected' ? '\u25cf Live' : '\u25cb Offline'}
          </span>
        </div>
        {equitySnap && <div style={{ textAlign:'right' }}>
          <div style={{ fontSize:11,color:'var(--muted)' }}>Total Equity</div>
          <div style={{ fontSize:20,fontWeight:700 }}>${(equitySnap.snapshot?.total_equity?.toLocaleString() ?? '-')}</div>
        </div>}
      </header>

      <nav style={{ display:'flex',gap:0,borderBottom:'1px solid var(--border)',padding:'0 24px',overflowX:'auto' }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => setActiveTab(t.id)} style={{
            padding:'10px 16px',background:activeTab===t.id?'var(--blue)':'transparent',
            color:activeTab===t.id?'#fff':'var(--muted)',border:'none',
            borderBottom:activeTab===t.id?'2px solid var(--blue)':'2px solid transparent',
            cursor:'pointer',fontSize:13,fontWeight:activeTab===t.id?600:400,whiteSpace:'nowrap'
          }}>{t.label}</button>))}
      </nav>

      <main style={{ padding:24 }}>
        {activeTab === 'overview' && <ErrorWrap name="Overview"><OverviewPanel /></ErrorWrap>}
        {activeTab === 'positions' && <ErrorWrap name="Positions"><PositionsPanel /></ErrorWrap>}
        {activeTab === 'trades' && <ErrorWrap name="Trades"><TradesPanel /></ErrorWrap>}
        {activeTab === 'strategies' && <ErrorWrap name="Strategies"><StrategiesPanel /></ErrorWrap>}
        {activeTab === 'reports' && <ErrorWrap name="Reports"><ReportsPanel /></ErrorWrap>}
        {activeTab === 'simulation' && <ErrorWrap name="Simulation"><StrategyLabPanel /></ErrorWrap>}
        {activeTab === 'engine' && <ErrorWrap name="Engine"><EnginePanel /></ErrorWrap>}
        {activeTab === 'logs' && <ErrorWrap name="Logs"><LogsPanel /></ErrorWrap>}
        {activeTab === 'settings' && <ErrorWrap name="Settings"><SettingsPanel /></ErrorWrap>}
      </main>
    </div>
  )
}

function ErrorWrap({ name, children }) {
  const [err, setErr] = useState(null);
  try {
    return children;
  } catch(e) {
    setErr(e);
    return (
      <div style={{ background:'#2d1b1b', border:'1px solid #f85149', borderRadius:8, padding:16, color:'#f85149', fontFamily:'monospace', fontSize:12 }}>
        <strong>&#9888;&#65039; {name} Component Error</strong>
        <div style={{ marginTop:8 }}>{e.message}</div>
      </div>
    );
  }
}

export default App
