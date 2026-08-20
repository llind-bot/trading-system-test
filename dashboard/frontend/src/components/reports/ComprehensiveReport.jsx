import React, { useState, useEffect } from 'react';
import { fetchReportComprehensiveDetailed } from '../../lib/api.js';
import { formatEasternShort } from '../../lib/tz.js';

/* ── Helpers ───────────────────────────────────────────────── */

function StatCard({ label, value, color }) {
  return (
    <div style={{ background:'var(--surface)',padding:'14px 16px',borderRadius:8,border:'1px solid var(--border)' }}>
      <div style={{ fontSize:11,color:'var(--muted)',marginBottom:4 }}>{label}</div>
      <div style={{ fontSize:20,fontWeight:700,color:color||'var(--text)' }}>{value}</div>
    </div>
  );
}

function fmt$(v) {
  if (v == null) return '-';
  const s = v.toLocaleString(undefined,{maximumFractionDigits:2});
  return '$'+s+(v<0?'':'');
}

const ASSET_FILTERS = [
  {id:'all',label:'All Assets'},
  {id:'crypto',label:'Crypto'},
  {id:'stock',label:'Stock/US Equity'},
];

/* ── Main Component ─────────────────────────────────────────── */

export default function ComprehensiveReport() {
  const [assetFilter, setAssetFilter] = useState('all');
  const [symbolFilter, setSymbolFilter] = useState('');
  const [symbolSearch, setSymbolSearch] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [detailTab, setDetailTab] = useState('trades');
  const [strategyAssetFilter, setStrategyAssetFilter] = useState('all');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchReportComprehensiveDetailed({ assetClass: assetFilter==='all'?null:assetFilter, symbol: symbolFilter||null })
      .then(d => { if (!cancelled) setData(d); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [assetFilter, symbolFilter]);

  // Build symbol options from positions data.
  const allSymbols = [];
  if (data?.pnl_summary) {
    const seen = new Set();
    for (const key of Object.keys(data.pnl_summary)) {
      if (!seen.has(key)) { seen.add(key); allSymbols.push(key); }
    }
    if (!symbolFilter) {
      for (const p of data.positions || []) {
        const posSym = p.symbol;
        const fullSym = getFullSymbol(posSym, data);
        if (fullSym && !seen.has(fullSym)) { seen.add(fullSym); allSymbols.push(fullSym); }
      }
    }
  }

  // Deduplicate and apply symbol search filter
  const uniqueSymbols = [...new Set(allSymbols)];
  const filteredBySearch = symbolSearch.trim() === ''
    ? uniqueSymbols
    : uniqueSymbols.filter(s => s.toLowerCase().includes(symbolSearch.toLowerCase()));

  // Filter by asset class when "All Assets" is active
  const visibleSymbols = assetFilter === 'all' ? filteredBySearch : filteredBySearch.filter(s => {
    if (assetFilter === 'crypto') return s.includes('/');
    return !s.includes('/');
  });

  function getFullSymbol(baseName, reportData) {
    const pos = reportData?.positions?.find(p => p.symbol === baseName);
    if (pos && pos.symbol.includes('/')) return pos.symbol;
    return null;
  }

  // Aggregate stats from pnl_summary
  let totalRealizedPnl = 0, totalTrades = 0, totalBuys = 0, totalSells = 0;
  if (data?.pnl_summary) {
    for (const [,v] of Object.entries(data.pnl_summary)) {
      totalRealizedPnl += v.realized_pnl || 0;
      totalTrades += v.total_trades || 0;
      totalBuys += v.buy_count || 0;
      totalSells += v.sell_count || 0;
    }
  }

  const hasData = !!data && data.trades?.length > 0;
  const noStratData = !hasData || (data.strategy_history?.length || 0) === 0;

  // ── Sub-renderers ────────────────────────────────────────

  const renderPositions = () => {
    const pos = data?.positions || [];
    if (!pos.length) return <div style={{padding:16,textAlign:'center',color:'var(--muted)'}}>No position data</div>;
    return (
      <>
        <h4 style={{ fontSize:13,margin:'0 0 8px',color:'var(--muted)' }}>Current Positions</h4>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:650 }}>
            <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
              {[
                { h:'Symbol', ta:'left' },
                { h:'Qty', ta:'right' },
                { h:'Avg Cost', ta:'right' },
                { h:'Current', ta:'right' },
                { h:'Cost Basis', ta:'right' },
                { h:'Mkt Value', ta:'right' },
                { h:'P\u0026L $', ta:'right' },
                { h:'P\u0026L %', ta:'right' },
              ].map(({ h, ta }) => (
                <th key={h} style={{ padding:'6px 8px',textAlign:ta,color:'var(--muted)',fontWeight:500 }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {pos.map(p => {
                const pnlPct = p.unrealized_pnl_pct;
                return (
                  <tr key={p.symbol} style={{ borderBottom:'1px solid var(--border)' }}>
                    <td style={{ padding:'6px 8px',textAlign:'left',fontWeight:600,whiteSpace:'nowrap' }}>{p.symbol}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right' }}>{(p.qty??0).toLocaleString(undefined,{maximumFractionDigits:4})}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right' }}>${(p.avg_cost!=null?fmt$(p.avg_cost):'-')}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right' }}>${(p.current_price!=null?fmt$(p.current_price):'-')}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right' }}>${(p.cost_basis!=null?p.cost_basis.toLocaleString(undefined,{maximumFractionDigits:2}):'-')}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right' }}>${(p.current_value!=null?p.current_value.toLocaleString(undefined,{maximumFractionDigits:2}):'-')}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right',color:(p.unrealized_pnl||0)>=0?'var(--green)':'var(--red)' }}>
                      ${(p.unrealized_pnl??0).toFixed(2)}
                    </td>
                    <td style={{ padding:'6px 8px',textAlign:'right',fontWeight:600,color:(pnlPct||0)>=0?'var(--green)':'var(--red)' }}>
                      {pnlPct!=null ? pnlPct.toFixed(2)+'%' : '-'}
                    </td>
                  </tr>);
              })}
            </tbody>
          </table>
        </div>
      </>
    );
  };

  const renderTrades = () => {
    const trades = data?.trades || [];
    if (!trades.length) return <div style={{padding:16,textAlign:'center',color:'var(--muted)'}}>No trade data for this filter</div>;
    
    // Use per-trade stored P\u0026L (computed at fill time in transaction_logger.py)
    // No inline FIFO — just read t._pnl_dollars / t._pnl_pct from the API response.

    return (
      <>
        <h4 style={{ fontSize:13,margin:'0 0 8px',color:'var(--muted)' }}>Trade History</h4>
        <div style={{ overflowX:'auto' }}>
          <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:520 }}>
            <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
              {[
                { h:'Date', ta:'left' },
                { h:'Symbol', ta:'left' },
                { h:'Side', ta:'left' },
                { h:'Qty', ta:'right' },
                { h:'Price', ta:'right' },
                { h:'Total Cost', ta:'right' },
                { h:'P\u0026L $', ta:'right' },
                { h:'P\u0026L %', ta:'right' },
                { h:'Strategy', ta:'left' },
              ].map(({ h, ta }) => (
                <th key={h} style={{ padding:'6px 8px',textAlign:ta,color:'var(--muted)',fontWeight:500 }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {trades.map((t,i) => {
                const isSell = t.side === 'sell';
                // P&L only shown for sells; buys get nothing
                const pnlDollar = isSell ? (t._pnl_dollars ?? 0) : null;
                const pnlPct = isSell && t._pnl_pct != null ? parseFloat(t._pnl_pct) : null;
                return (
                  <tr key={t.id} style={{ borderBottom:'1px solid var(--border)' }}>
                    <td style={{ padding:'6px 8px',textAlign:'left',color:'var(--muted)',fontStyle:'italic' }}>{formatEasternShort(t.timestamp)}</td>
                    <td style={{ padding:'6px 8px',textAlign:'left',fontWeight:600,whiteSpace:'nowrap' }}>{t.symbol}</td>
                    <td style={{ padding:'6px 8px',textAlign:'left',color:t.side==='buy'?'var(--green)':'var(--red)',fontWeight:500,fontStyle:'italic' }}>
                      {t.side.toUpperCase()}
                    </td>
                    <td style={{ padding:'6px 8px',textAlign:'right' }}>{(t.qty||0).toLocaleString(undefined,{maximumFractionDigits:6})}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right' }}>${(t.price??0).toFixed(4)}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right' }}>${((t.notional||t.total_cost||0)).toLocaleString(undefined,{maximumFractionDigits:2})}</td>
                    <td style={{ padding:'6px 8px',textAlign:'right',color:(isSell && pnlDollar>=0)?'var(--green)':(isSell ? 'var(--red)' : 'var(--muted)'),fontWeight:500 }}>
                      {isSell ? (pnlDollar >= 0 ? '+' : '') + '$' + Math.abs(pnlDollar).toFixed(2) : '—'}
                    </td>
                    <td style={{ padding:'6px 8px',textAlign:'right',color:(isSell && pnlPct!=null && pnlPct>=0)?'var(--green)':(isSell ? 'var(--red)' : 'var(--muted)'),fontWeight:500 }}>
                      {isSell ? (pnlPct != null ? (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%' : '—') : '—'}
                    </td>
                    <td style={{ padding:'6px 8px',textAlign:'left' }}>{t.strategy || '-'}</td>
                  </tr>);
              })}
            </tbody>
          </table>
        </div>
      </>
    );
  };

  const renderStrategyHistory = () => {
    const history = data?.strategy_history || [];
    if (!history.length) return <div style={{padding:16,textAlign:'center',color:'var(--muted)'}}>No strategy signal data for this filter</div>;

    const filteredHistory = strategyAssetFilter === 'all'
      ? history
      : history.filter(s => strategyAssetFilter === 'crypto' ? (s.symbol||'').includes('/') : !(s.symbol||'').includes('/'));

    // Per-asset summary
    const byAsset = {};
    for (const s of filteredHistory) {
      const sym = s.symbol || '-';
      if (!byAsset[sym]) byAsset[sym] = { symbol: sym, BUY: 0, SELL: 0, HOLD: 0, confs: [] };
      const vote = (s.vote_result || 'HOLD').toUpperCase();
      if (vote === 'BUY') byAsset[sym].BUY++;
      else if (vote === 'SELL') byAsset[sym].SELL++;
      else byAsset[sym].HOLD++;
      if (s.confidence != null) byAsset[sym].confs.push(s.confidence);
    }
    const assetRows = Object.values(byAsset).sort((a,b) => b.BUY+b.SELL - a.BUY-a.SELL);

    // Unique strategies used for this filter
    const strats = [...new Set(filteredHistory.map(s => s.strategy || '-'))].sort();

    return (
      <>
      {/* Asset class filter */}
      <div style={{ display:'flex',gap:4,marginBottom:12,borderBottom:'1px solid var(--border)',width:'fit-content' }}>
        {[{id:'all',label:'All Assets'}, {id:'crypto',label:'Crypto'}, {id:'stock',label:'Stock'}].map(f => (
          <button key={f.id} onClick={()=>setStrategyAssetFilter(f.id)} style={{
            padding:'4px 12px',background:strategyAssetFilter===f.id?'var(--blue)':'transparent',
            color:strategyAssetFilter===f.id?'#fff':'var(--muted)',border:'none',
            borderBottom:strategyAssetFilter===f.id?'2px solid var(--blue)':'2px solid transparent',
            cursor:'pointer',fontSize:11,fontWeight:strategyAssetFilter===f.id?600:400
          }}>{f.label}</button>))}    
      </div>

      <div style={{ marginBottom:12, fontSize:11,color:'var(--muted)' }}>{filteredHistory.length} signals · {assetRows.length} assets · {strats.length} strategies</div>

      {/* Per-asset summary */}
      <div style={{ overflowX:'auto' }}>
        <table style={{ width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:520 }}>
          <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
            {['Symbol','Total','BUY','SELL','HOLD','Avg Confidence'].map(h => (
              <th key={h} style={{ padding:'6px 10px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
          </tr></thead>
          <tbody>
            {assetRows.map((a,i) => (
              <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                <td style={{ padding:'6px 10px',fontWeight:600 }}>{a.symbol}</td>
                <td style={{ padding:'6px 10px' }}>{a.BUY+a.SELL+a.HOLD}</td>
                <td style={{ padding:'6px 10px',color:'var(--green)',fontWeight:500 }}>{a.BUY}</td>
                <td style={{ padding:'6px 10px',color:'var(--red)',fontWeight:500 }}>{a.SELL}</td>
                <td style={{ padding:'6px 10px',color:'var(--muted)' }}>{a.HOLD}</td>
                <td style={{ padding:'6px 10px' }}>
                  {a.confs.length > 0 ? ((a.confs.reduce((x,y)=>x+y,0)/a.confs.length)*100).toFixed(1)+'%' : '-'}
                </td>
              </tr>))}
          </tbody>
        </table>
      </div>

      {/* Raw signal log */}
      <details style={{ marginTop:16 }}>
        <summary style={{ cursor:'pointer',fontSize:12,color:'var(--muted)' }}>Show raw signals (last {Math.min(filteredHistory.length,100)})</summary>
        <div style={{ overflowX:'auto',marginTop:8 }}>
          <table style={{ width:'100%',borderCollapse:'collapse',fontSize:11 }}>
            <thead><tr style={{ borderBottom:'1px solid var(--border)' }}>
              {['Timestamp','Symbol','Strategy','Vote','Confidence'].map(h => (
                <th key={h} style={{ padding:'4px 8px',textAlign:'left',color:'var(--muted)',fontWeight:500 }}>{h}</th>))}
            </tr></thead>
            <tbody>
              {filteredHistory.slice(0,100).map((s,i) => (
                <tr key={i} style={{ borderBottom:'1px solid var(--border)' }}>
                  <td style={{ padding:'4px 8px',color:'var(--muted)',fontStyle:'italic' }}>{formatEasternShort(s.timestamp)}</td>
                  <td style={{ padding:'4px 8px',fontWeight:600,whiteSpace:'nowrap' }}>{s.symbol}</td>
                  <td style={{ padding:'4px 8px' }}>{s.strategy || '-'}</td>
                  <td style={{ padding:'4px 8px',fontWeight:600,color:{BUY:'var(--green)',SELL:'var(--red)',HOLD:'var(--muted)'}[s.vote_result]||'var(--text)' }}>
                    {s.vote_result}
                  </td>
                  <td style={{ padding:'4px 8px' }}>{s.confidence != null ? (s.confidence*100).toFixed(1)+'%' : '-'}</td>
                </tr>))}
            </tbody>
          </table>
        </div>
      </details>
      </>
    );
  };

  // ── Handlers ─────────────────────────────────────────────

  const handleReset = () => {
    setAssetFilter('all');
    setSymbolFilter('');
    setSymbolSearch('');
    setDetailTab('trades');
  };

  const handleAssetClick = (id) => {
    setAssetFilter(id);
    setSymbolFilter('');
    setDetailTab('trades');
  };

  // ── Main render ──────────────────────────────────────────

  return (
    <>
      {/* Filter row */}
      <div style={{ display:'flex',gap:12,alignItems:'center',marginBottom:16,flexWrap:'wrap' }}>
        <span style={{ fontSize:12,color:'var(--muted)',fontWeight:500 }}>Filter:</span>
        
        {/* Asset type */}
        <div style={{ display:'flex',gap:4,borderBottom:'1px solid var(--border)' }}>
          {ASSET_FILTERS.map(f => (
            <button key={f.id} onClick={()=>handleAssetClick(f.id)} style={{
              padding:'5px 12px',background:assetFilter===f.id?'var(--blue)':'transparent',
              color:assetFilter===f.id?'#fff':'var(--muted)',border:'none',
              borderBottom:assetFilter===f.id?'2px solid var(--blue)':'2px solid transparent',
              cursor:'pointer',fontSize:12,fontWeight:assetFilter===f.id?600:400,borderRadius:'4px 4px 0 0'
            }}>{f.label}</button>))}
        </div>

        {/* Symbol text filter — fuzzy search */}
        {visibleSymbols.length > 0 && (
          <input
            type="text"
            placeholder="Filter assets..."
            value={symbolSearch}
            onChange={e => setSymbolSearch(e.target.value)}
            style={{
              padding:'4px 8px',background:'var(--surface)',color:'var(--text)',border:'1px solid var(--border)',borderRadius:6,fontSize:12,width:140
            }}
          />
        )}

        {/* Symbol dropdown */}
        {visibleSymbols.length > 0 && (
          <select value={symbolFilter} onChange={e=>setSymbolFilter(e.target.value)} style={{
            padding:'4px 8px',background:'var(--surface)',color:'var(--text)',border:'1px solid var(--border)',borderRadius:6,fontSize:12,minWidth:130
          }}>
            <option value="">All symbols</option>
            {visibleSymbols.map(s => (
              <option key={s} value={s}>{s}</option>))}
          </select>
        )}

        {/* Reset */}
        {(assetFilter!=='all'||symbolSearch||symbolFilter) && (
          <button onClick={handleReset} style={{
            padding:'4px 10px',background:'transparent',color:'var(--muted)',border:'1px solid var(--border)',
            borderRadius:6,cursor:'pointer',fontSize:12
          }}>Reset filters</button>
        )}
      </div>

      {/* Loading */}
      {loading && <div style={{ color:'var(--muted)' }}>Loading comprehensive report...</div>}
      
      {!loading && data && (
        <>
          {/* Summary cards — moved above positions */}
          <div style={{ display:'flex',gap:8,marginBottom:16 }}>
            <StatCard label="Total Realized P\u0026L" value={fmt$(totalRealizedPnl)} color={totalRealizedPnl>=0?'var(--green)':'var(--red)'}/>
            <StatCard label="Trades (shown)" value={String(totalTrades)}/>
            <StatCard label="Buys / Sells" value={`${totalBuys} / ${totalSells}`}/>
          </div>

          {/* Positions table */}
          {renderPositions()}

          {/* Detail tabs */}
          {hasData && (
            <>
              <div style={{ display:'flex',gap:4,marginBottom:16,borderBottom:'1px solid var(--border)' }}>
                <button onClick={()=>setDetailTab('trades')} style={{
                  padding:'6px 14px',background:detailTab==='trades'?'var(--blue)':'transparent',
                  color:detailTab==='trades'?'#fff':'var(--muted)',border:'none',
                  borderBottom:detailTab==='trades'?'2px solid var(--blue)':'2px solid transparent',
                  cursor:'pointer',fontSize:12,fontWeight:detailTab==='trades'?600:400,borderRadius:'4px 4px 0 0'
                }}>Trade History ({data.trades.length})</button>
                <button onClick={()=>setDetailTab('strategy')} style={{
                  padding:'6px 14px',background:detailTab==='strategy'?'var(--blue)':'transparent',
                  color:detailTab==='strategy'?'#fff':'var(--muted)',border:'none',
                  borderBottom:detailTab==='strategy'?'2px solid var(--blue)':'2px solid transparent',
                  cursor:'pointer',fontSize:12,fontWeight:detailTab==='strategy'?600:400,borderRadius:'4px 4px 0 0'
                }}>Strategy History ({data.strategy_history?.length||0})</button>
              </div>

              {detailTab === 'trades' && renderTrades()}
              {detailTab === 'strategy' && (noStratData ? <div style={{padding:16,textAlign:'center',color:'var(--muted)'}}>No strategy signal data for this filter</div> : renderStrategyHistory())}
            </>
          )}

          {/* No data state */}
          {!hasData && (
            <div style={{ padding:32, textAlign:'center', color:'var(--muted)' }}>
              No data for the selected filters. Try selecting a different asset class or symbol.
            </div>
          )}

          {/* Active filters display */}
          {(assetFilter!=='all'||symbolSearch||symbolFilter) && (
            <div style={{ marginTop:12, fontSize:11, color:'var(--muted)' }}>
              Showing data for{' '}
              {symbolFilter ? (
                <>symbol <strong>{symbolFilter}</strong></>
              ) : symbolSearch ? (
                <>filtered by <strong>"{symbolSearch}"</strong></>
              ) : assetFilter === 'crypto' ? (
                <strong>Crypto</strong>
              ) : assetFilter === 'stock' ? (
                <strong>Stock/US Equity</strong>
              ) : (
                <strong>All Assets</strong>
              )}
            </div>
          )}
        </>
      )}
    </>
  );
}
