import React, { useState } from 'react';
import ComprehensiveReport from './reports/ComprehensiveReport.jsx';
import PositionTrackerReport from './reports/PositionTrackerReport.jsx';
import Trades24hReport from './reports/Trades24hReport.jsx';
import StrategyHistoryReport from './reports/StrategyHistoryReport.jsx';
import BarsCoverageReport from './reports/BarsCoverageReport.jsx';
import EquityCurveReport from './reports/EquityCurveReport.jsx';
import SignalEvaluationsReport from './reports/SignalEvaluationsReport.jsx';

const REPORT_TABS = [
  {id:'comprehensive',label:'Comprehensive'},
  {id:'equity',label:'Equity Curve'},
  {id:'positions',label:'Position Tracker'},
  {id:'24h',label:'24h Trades'},
  {id:'strategy',label:'Strategy History'},
  {id:'bars',label:'Bar Coverage'},
  {id:'evaluations',label:'✅ Success Metrics'},
];

const REPORT_MAP = {
  comprehensive: ComprehensiveReport,
  equity: EquityCurveReport,
  positions: PositionTrackerReport,
  '24h': Trades24hReport,
  strategy: StrategyHistoryReport,
  bars: BarsCoverageReport,
  evaluations: SignalEvaluationsReport,
};

export default function ReportsPanel() {
  const [active, setActive] = useState('comprehensive');
  const Panel = REPORT_MAP[active];

  return (
    <>
      <h2 style={{ margin:'0 0 16px',fontSize:18 }}>Reports</h2>
      <div style={{ display:'flex',gap:4,marginBottom:20,borderBottom:'1px solid var(--border)' }}>
        {REPORT_TABS.map(r => (
          <button key={r.id} onClick={()=>setActive(r.id)} style={{
            padding:'6px 14px',background:active===r.id?'var(--blue)':'transparent',
            color:active===r.id?'#fff':'var(--muted)',border:'none',
            borderBottom:active===r.id?'2px solid var(--blue)':'2px solid transparent',
            cursor:'pointer',fontSize:12,fontWeight:active===r.id?600:400,borderRadius:'4px 4px 0 0'
          }}>{r.label}</button>))}
      </div>
      <Panel />
    </>
  );
}
