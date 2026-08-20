import React, { useState, useEffect, useRef } from 'react';
import { fetchWatchlistFull, saveWatchlist, fetchStrategiesFull } from '../../lib/api.js';

/* ═══════════════════════════════════════════════════════════════
   STRATEGY PARAM METADATA — single source of truth for all param editors
   Mirrors the strategy class default values (param_descriptors).
   ═══════════════════════════════════════════════════════════════ */
const STRATEGY_DEFS = {
  SMA_Crossover:    { desc:'SMA fast/slow crossover with volume confirmation', sma_fast:{label:'SMA Fast',default:9,step:1},sma_slow:{label:'SMA Slow',default:21,step:1},volume_mult:{label:'Volume Mult',default:1.5,step:0.1},volume_period:{label:'Volume Period',default:20,step:1} },
  MACD_Volume:      { desc:'MACD histogram crossover with volume confirmation', macd_fast:{label:'MACD Fast',default:12,step:1},macd_slow:{label:'MACD Slow',default:26,step:1},macd_signal:{label:'MACD Signal',default:9,step:1},sma_period:{label:'SMA Period',default:50,step:1} },
  RSI_MeanReversion:{ desc:'Larry Connors\' 2-period RSI mean reversion strategy with ADX trend filter', rsi_period:{label:'RSI Period',default:7,step:1},oversold_threshold:{label:'Oversold %',default:25,step:1},overbought_threshold:{label:'Overbought %',default:80,step:1},adx_threshold:{label:'ADX Threshold',default:10,step:1},adx_period:{label:'ADX Period',default:14,step:1} },
  Bollinger_Squeeze:{ desc:'Bollinger Band squeeze detection with breakout entry', bb_period:{label:'BB Period',default:20,step:1},bb_multiplier:{label:'BB Multiplier',default:2.0,step:0.1},squeeze_bandwidth_threshold:{label:'Squeeze BW Threshold',default:0.01,step:0.005},atr_decline_bars:{label:'ATR Decline Bars',default:10,step:1} },
  CryptoSwingReversion:{ desc:'RSI + Bollinger Band swing mean-reversion for BTC/USD volatile assets', rsi_period:{label:'RSI Period',default:14,step:1},rsi_oversold:{label:'RSI Oversold %',default:15,step:1},rsi_overbought:{label:'RSI Overbought %',default:80,step:1},bb_period:{label:'BB Period',default:30,step:5},bb_multiplier:{label:'BB Multiplier',default:2.5,step:0.1},atr_period:{label:'ATR Period',default:20,step:1},trend_sma_period:{label:'Trend SMA Period',default:100,step:5},min_rsi_stretch:{label:'Min RSI Stretch %',default:15,step:1} },
  TrendFollowing:     { desc:'Longer-term trend following with ATR exit', atr_period:{label:'ATR Period',default:14,step:1},trend_sma_fast:{label:'Trend SMA Fast',default:9,step:1},trend_sma_slow:{label:'Trend SMA Slow',default:21,step:1},exit_atr_mult:{label:'Exit ATR Mult',default:3.0,step:0.1} },
  CryptoSwingDaily:   { desc:'Daily timeframe swing strategy targeting 2-3% gains on crypto. BB lower/upper band penetration + RSI extremes, enter at penetration low/high.', rsi_period:{label:'RSI Period',default:14,step:1},oversold_threshold:{label:'Oversold Threshold (RSI)',default:28,step:1},overbought_threshold:{label:'Overbought Threshold (RSI)',default:75,step:1},bb_period:{label:'BB Period',default:20,step:5},bb_multiplier:{label:'BB Multiplier',default:2.0,step:0.1},trend_sma_period:{label:'Trend SMA Period',default:50,step:5},tp_fixed_pct:{label:'TP Fixed %',default:2.5,step:0.1},tp_atr_mult:{label:'TP ATR Mult',default:1.5,step:0.1},min_tp_pct:{label:'Min TP %',default:2.0,step:0.1},max_tp_pct:{label:'Max TP %',default:3.0,step:0.1},sl_fixed_pct:{label:'Buy Side SL Fixed %',default:2.0,step:0.1},sl_sell_pct:{label:'Sell Side SL Fixed %',default:3.0,step:0.1},use_trend_filter:{label:'Use Trend Filter',default:false,step:0},min_penetration_depth_pct:{label:'Min Penetration Depth %',default:0.1,step:0.1},atr_period:{label:'ATR Period',default:14,step:1},base_confidence:{label:'Base Confidence',default:0.7,step:0.01},rsi_depth_conf_mult:{label:'RSI Depth Conf Mult',default:0.002,step:0.001},penetration_conf_threshold:{label:'Penetration Conf Threshold %',default:0.3,step:0.05},penetration_conf_bonus:{label:'Penetration Conf Bonus %',default:0.05,step:0.01},confidence_cap:{label:'Confidence Cap',default:0.95,step:0.01},trend_filter_buy_threshold:{label:'Trend Filter Buy Threshold %',default:-3.0,step:0.5},trend_filter_sell_threshold:{label:'Trend Filter Sell Threshold %',default:3.0,step:0.5} },
  ORB:              { desc:'Opening Range Breakout — enters on breakout of initial range', orb_bars:{label:'ORB Bars',default:4,step:1},volume_mult:{label:'Volume Mult',default:1.3,step:0.1},confirm_pct:{label:'Confirm %',default:0.01,step:0.005} },
  Donchian_Breakout:{ desc:'Donchian channel breakout — classic turtle trading signal', donchian_period:{label:'Donchian Period',default:20,step:1},atr_min:{label:'Min ATR',default:0.5,step:0.1},breakout_confirm_pct:{label:'Breakout Confirm %',default:0.01,step:0.005} },
  Range_Bounce:   { desc:'Multi-level support/resistance mean-reversion for BTC-style markets', range_lookback_short:{label:'Range Lookback Short',default:20,step:1},range_lookback_mid:{label:'Range Lookback Mid',default:50,step:1},range_lookback_full:{label:'Range Lookback Full',default:100,step:1},trend_sma_period:{label:'Trend SMA Period',default:50,step:1},rsi_period:{label:'RSI Period',default:14,step:1},oversold_threshold:{label:'Oversold Threshold',default:50,step:1},overbought_threshold:{label:'Overbought Threshold',default:50,step:1},min_range_pct:{label:'Min Range %',default:1.0,step:0.1},max_range_pct:{label:'Max Range %',default:30.0,step:0.5},entry_buffer_short:{label:'Entry Buffer Short %',default:2.0,step:0.1},entry_buffer_mid:{label:'Entry Buffer Mid %',default:3.0,step:0.1},entry_buffer_full:{label:'Entry Buffer Full %',default:7.0,step:0.5},consecutive_drop_bars:{label:'Consecutive Drop Bars',default:3,step:1},sell_on_gain_pct:{label:'Sell on Gain %',default:2.5,step:0.1},max_hold_bars:{label:'Max Hold Bars',default:12,step:1},stop_loss_pct:{label:'Stop Loss %',default:-1.0,step:0.1} },
  RSI_Pullback:     { desc:'RSI pullback entry on trending assets with volume confirmation', rsi_period:{label:'RSI Period',default:14,step:1},pullback_depth:{label:'Pullback Depth %',default:8,step:0.5},volume_confirm:{label:'Volume Confirm Mult',default:1.2,step:0.1} },
  VWAP_Reversion:   { desc:'Price reversion to daily VWAP — institutional mean-reversion strategy', deviation_mult:{label:'Deviation Mult',default:2.0,step:0.1},revert_threshold:{label:'Revert Threshold',default:1.005,step:0.001} },
};

/* ═══════════════════════════════════════════════════════════════
   HELPER FUNCTIONS
   ═══════════════════════════════════════════════════════════════ */
function numVal(v) { return v != null ? v : ''; }

function NumInput({ value, onChange, style, step = 'any', min, max, extra = {} }) {
  const inputRef = React.useRef(null);
  return (
    <input ref={inputRef} type="number" inputMode="decimal" step={step} min={min} max={max}
      value={value ?? ''} onFocus={e => { e.target.select(); }}
      onChange={e => { const raw = e.target.value; if (raw === '') { onChange(''); } else { onChange(raw); } }}
      style={{ ...style, MozAppearance:'textfield', WebkitAppearance:'none', textAlign:'right' }} {...extra} />
  );
}

/* ═══ Inline param editor for a concrete strategy ═══ */
function ParamEditor({ assetIdx, concreteName, paramKeys, currentParams, updateParam }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div style={{ marginLeft: 20 }}>
      <button onClick={() => setExpanded(!expanded)} style={{ padding:'2px 10px',fontSize:10,background:'transparent',border:'none',color:'var(--blue)',cursor:'pointer' }}>
        {expanded ? '▼ Hide Params' : '▶ Edit Params'}
      </button>
      {expanded && (
        <div style={{ padding:'6px 10px',borderRadius:6,background:'#0a0b10',border:'1px solid var(--border)',marginTop:4 }}>
          {Object.entries(paramKeys).map(([key, def]) => {
            const displayValue = currentParams[key] != null ? currentParams[key] : def.default;
            return (
              <div key={key} style={{ display:'flex',alignItems:'center',gap:6,marginBottom:3 }}>
                <span style={{ fontSize:10,color:'var(--muted)',minWidth:120 }}>{def.label}</span>
                <NumInput value={numVal(displayValue)} onChange={v => updateParam(assetIdx, concreteName, key, parseFloat(v) || def.default)} step={def.step} style={{ width:72,padding:'2px 6px',fontSize:11,background:'#0f1117',border:'1px solid var(--border)',borderRadius:4,color:'#e4e6eb' }} />
              </div>);})}
        </div>
      )}
    </div>
  );
}

/* ═══ Reference block for a single strategy's default params ═══ */
function StrategyDefaultBlock({ sname }) {
  const defKeys = STRATEGY_DEFS[sname] || {};
  if (Object.keys(defKeys).length === 0) return null;
  const desc = defKeys.desc;
  delete defKeys.desc; // don't render as param
  return (
    <div style={{ padding:'6px 12px',borderRadius:6,background:'#0a0b10',border:'1px solid var(--border)',marginBottom:4 }}>
      <div style={{ fontSize:11,fontWeight:600,color:'var(--amber)',marginBottom:4 }}>{sname}</div>
      {desc && <div style={{ fontSize:10,color:'var(--muted)',marginBottom:4 }}>{desc}</div>}
      <div style={{ display:'flex',flexWrap:'wrap',gap:8 }}>
        {Object.entries(defKeys).map(([k,d]) => (
          <span key={k} style={{ fontSize:10,color:'var(--muted)' }}>
            {d.label}: <b style={{color:'var(--text)'}}>{d.default}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   MAIN COMPONENT
   ═══════════════════════════════════════════════════════════════ */
export default function WatchlistSettings() {
  const [assets, setAssets] = useState([]);
  const assetsRef = useRef(assets);
  // Keep ref in sync with state so handleSaveAll always reads current values
  useEffect(() => { assetsRef.current = assets; }, [assets]);
  const [defaults, setDefaults] = useState({});
  const [loading, setLoading] = useState(true);
  const [saveMsg, setSaveMsg] = useState('');
  const [enableFilter, setEnableFilter] = useState('all');
  const [assetFilter, setAssetFilter] = useState('all');
  const [saveState, setSaveState] = useState('idle'); // idle | saving | saved
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedAssetIdx, setExpandedAssetIdx] = useState(null);
  const [editingOriginal, setEditingOriginal] = useState(null);
  const [availableStrategies, setAvailableStrategies] = useState([]);

  /* Load watchlist */
  useEffect(() => {
    fetchWatchlistFull().then(d => {
      setAssets(d?.assets || []);
      setDefaults(d?.defaults || {});
      setLoading(false);
    }).catch(e => { console.error('[WatchlistSettings] fetch error:', e); setLoading(false); });
  }, []);

  /* Load available strategies from dynamic scanner */
  useEffect(() => {
    fetchStrategiesFull().then(d => { setAvailableStrategies(d?.available_strategies || []); }).catch(() => {});
  }, []);

  /* Unique strategy names from the scanner — already deduplicated by the backend */
  const uniqueStrats = availableStrategies.map(s => ({ name: s.name }));

  const handleSaveAll = async () => {
    setSaveState('saving');
    // DEBUG: log what's being sent BEFORE the request
    const sendAssets = assetsRef.current.filter(a => a.symbol);
    console.log('[SaveAll] sending', sendAssets.length, 'assets');
    for (const a of sendAssets) {
      console.log('  -', a.symbol, ': enabled=' + a.enabled + ' (type=' + typeof a.enabled + ')');
    }
    try {
      const resp = await saveWatchlist({ assets: sendAssets, defaults: defaults || {} });
      console.log('[SaveAll] response status:', resp);
      console.log('[SaveAll] response:', resp);
      setSaveState('saved');
      fetchWatchlistFull().then(d => { setAssets(d?.assets || []); setDefaults(d?.defaults || {}); }).catch(() => {});
      setTimeout(() => setSaveState('idle'), 2000);
    } catch (e) {
      setSaveMsg('Error: ' + e.message);
      setSaveState('idle');
    }
  };

  const handleSave = async () => {
    try {
      await saveWatchlist({ assets, defaults });
      setSaveMsg('Saved OK');
      setExpandedAssetIdx(null);
      setEditingOriginal(null);
      fetchWatchlistFull().then(d => { setAssets(d?.assets || []); setDefaults(d?.defaults || {}); }).catch(() => {});
      setTimeout(() => setSaveMsg(''), 3000);
    } catch (e) { setSaveMsg('Error: ' + e.message); }
  };

  const startEditing = (idx) => {
    setEditingOriginal(JSON.stringify(assets[idx]));
    setExpandedAssetIdx(idx);
  };

  const handleCancel = () => {
    if (expandedAssetIdx !== null && assets[expandedAssetIdx] && !assets[expandedAssetIdx].symbol) {
      setAssets(prev => prev.filter((_, i) => i !== expandedAssetIdx));
    } else if (editingOriginal !== null) {
      const originalAssets = JSON.parse(editingOriginal);
      setAssets(prev => { const copy = [...prev]; copy[expandedAssetIdx] = originalAssets; return copy; });
    }
    setExpandedAssetIdx(null);
    setEditingOriginal(null);
  };

  const updateField = (idx, field, value) => setAssets(prev => { const c = [...prev]; c[idx] = {...c[idx], [field]: value}; return c; });

  const toggleEnabledLocal = (idx) => setAssets(prev => {
    const c = [...prev];
    if (c[idx]) {
      const oldVal = c[idx].enabled;
      c[idx] = {...c[idx], enabled: !oldVal};
      console.log('[Toggle] idx=' + idx + ' asset=' + c[idx].symbol + ' toggled from ' + oldVal + ' to ' + !oldVal);
    }
    return c;
  });

  const addAsset = () => setAssets(prev => {
    const newAsset = {
      symbol: '', asset_class: 'stock', enabled: true, strategies: [], strategy_params: {},
      sell_strategies: defaults?.sell_strategies || ['tp_ladder','trailing_stop'],
      max_position_dollar: 100, sl_pct: defaults?.sl_pct ?? -5.0, trailing_stop_pct: defaults?.trailing_stop_pct ?? 2.0,
      tp_levels: defaults?.tp_levels_default || [
        { level: 1, sell_pct: 0.25, profit_pct: 2.0 },
        { level: 2, sell_pct: 0.5, profit_pct: 4.0 },
        { level: 3, sell_pct: 0.25, profit_pct: 6.0 },
      ],
    };
    setTimeout(() => { setSearchQuery(''); setFilter('all'); setExpandedAssetIdx(0); setEditingOriginal(JSON.stringify(newAsset)); }, 50);
    return [newAsset, ...prev];
  });

  const removeAsset = async (idx) => {
    setAssets(prev => {
      const updated = prev.filter((_, i) => i !== idx);
      saveWatchlist({ assets: updated }).catch(e => { setAssets(prev); alert('Failed to remove asset: ' + e.message); });
      return updated;
    });
    setExpandedAssetIdx(null);
    setEditingOriginal(null);
  };

  const updateTpLevel = (assetIdx, li, field, val) => {
    setAssets(prev => {
      const c = {...prev[assetIdx]};
      const levels = [...(c.tp_levels || [])];
      levels[li] = {...levels[li], [field]: val};
      return [...prev.slice(0, assetIdx), {...c, tp_levels: levels}, ...prev.slice(assetIdx + 1)];
    });
  };

  const addTpLevel = (assetIdx) => setAssets(prev => {
    const c = {...prev[assetIdx]};
    const levels = [...(c.tp_levels || [])];
    levels.push({ level: levels.length + 1, sell_pct: 0.25, profit_pct: 5.0 });
    return [...prev.slice(0, assetIdx), {...c, tp_levels: levels}, ...prev.slice(assetIdx + 1)];
  });

  const removeTpLevel = (assetIdx, li) => setAssets(prev => {
    const c = {...prev[assetIdx]};
    const levels = [...(c.tp_levels || [])];
    levels.splice(li, 1);
    levels.forEach((l, i) => { l.level = i + 1; });
    return [...prev.slice(0, assetIdx), {...c, tp_levels: levels}, ...prev.slice(assetIdx + 1)];
  });

  /* Copy ALL default params for a single strategy onto this asset */
  const copyStrategyDefaults = (assetIdx, concreteName) => {
    const defaultsObj = STRATEGY_DEFS[concreteName];
    if (!defaultsObj) return;
    setAssets(prev => {
      const c = {...prev[assetIdx]};
      if (!c.strategy_params) c.strategy_params = {};
      c.strategy_params[concreteName] = {};
      for (const [k, def] of Object.entries(defaultsObj)) {
        c.strategy_params[concreteName][k] = def.default;
      }
      return [...prev.slice(0, assetIdx), {...c}, ...prev.slice(assetIdx + 1)];
    });
  };

  /* Toggle a single concrete strategy on/off for this asset */
  const handleConcreteToggle = (assetIdx, concreteName) => {
    setAssets(prev => {
      const c = {...prev[assetIdx]};
      if ((c.strategies || []).includes(concreteName)) {
        c.strategies = (c.strategies || []).filter(s => s !== concreteName);
      } else {
        const next = [...(c.strategies || []), concreteName];
        c.strategies = next;
      }
      return [...prev.slice(0, assetIdx), {...c}, ...prev.slice(assetIdx + 1)];
    });
  };

  const updateStrategyParam = (assetIdx, concreteName, paramKey, val) => {
    setAssets(prev => {
      const c = {...prev[assetIdx]};
      if (!c.strategy_params) c.strategy_params = {};
      if (!c.strategy_params[concreteName]) c.strategy_params[concreteName] = {};
      c.strategy_params[concreteName][paramKey] = val;
      return [...prev.slice(0, assetIdx), {...c}, ...prev.slice(assetIdx + 1)];
    });
  };

  const enabledAssets = enableFilter === 'all' ? assets : (enableFilter === 'enabled' ? assets.filter(a => a.enabled) : assets.filter(a => !a.enabled));
  const filteredByClass = assetFilter === 'all' ? enabledAssets : enabledAssets.filter(a => a.asset_class === assetFilter);
  const visibleAssets = searchQuery
    ? filteredByClass.filter(a => !a.symbol || a.symbol.toLowerCase().includes(searchQuery.toLowerCase()))
    : filteredByClass;

  useEffect(() => {
    if (expandedAssetIdx !== null) {
      const expandedAsset = assets[expandedAssetIdx];
      if (expandedAsset && !visibleAssets.includes(expandedAsset)) { setExpandedAssetIdx(null); setEditingOriginal(null); }
    }
  }, [enableFilter, assetFilter, visibleAssets]);

  const stockCount = assets.filter(a => a.asset_class === 'stock').length;
  const cryptoCount = assets.filter(a => a.asset_class === 'crypto').length;

  /* ═══ RENDER ═══ */
  if (loading) return <div style={{ color:'var(--muted)' }}>Loading watchlist...</div>;

  return (
    <>
      {saveMsg && (
        <div style={{ marginBottom:8,padding:'6px 16px',borderRadius:6,background:saveMsg.startsWith('Error')?'rgba(239,68,68,0.1)':'rgba(34,197,94,0.1)',border:saveMsg.startsWith('Error')?'var(--red)':'var(--green)',color:saveMsg.startsWith('Error')?'var(--red)':'var(--green)',fontSize:12 }}>{saveMsg}</div>
      )}

      {/* ═══ SAVE ALL BUTTON ═══ */}
      <button onClick={handleSaveAll} style={{ marginBottom:16,padding:'10px 32px',background:saveState==='saving'?'var(--muted)':'linear-gradient(135deg,var(--green),#16a34a)',border:'none',borderRadius:8,color:'#fff',cursor:'pointer',fontWeight:700,fontSize:14,boxShadow:'0 2px 8px rgba(34,197,94,0.3)',letterSpacing:0.5 }}>{saveState==='saving' ? 'Saving...' : saveState==='saved' ? '✓ Saved!' : '💾 Save All Changes'}</button>

      {/* ════════════════════════════════════════════════════════
          SECTION 1: DEFAULT STRATEGIES — Reference blocks
          All concrete strategies with their default param values.
          These blocks serve as the source of truth; click "Copy"
          on an asset to apply a strategy's defaults onto it.
          ════════════════════════════════════════════════════════ */}
      {uniqueStrats.length > 0 && (
        <details style={{ marginBottom:16,padding:'8px 14px',borderRadius:8,background:'var(--surface)',border:'1px solid var(--border)' }}>
          <summary style={{ fontWeight:600,cursor:'pointer',fontSize:13,color:'var(--blue)',marginBottom:8 }}>
            📋 Default Strategies — Reference (use "Copy" button on assets below to apply defaults)
          </summary>
          {uniqueStrats.map(({ name: sname }) => (
            <StrategyDefaultBlock key={sname} sname={sname} />
          ))}
        </details>
      )}

      {/* ═══ SECTION 2: ENABLE FILTER (row 1) + ASSET CLASS FILTER (row 2) & SEARCH ═══ */}
      <div style={{ display:'flex',gap:6,marginBottom:8 }}>
        {[{key:'all',label:`All (${assets.length})`},{key:'enabled',label:'✓ Enabled'},{key:'disabled',label:'✗ Disabled'}].map(f => (
          <button key={f.key} onClick={()=>setEnableFilter(f.key)} style={{ padding:'5px 12px',background:enableFilter===f.key?(f.key==='enabled'?'var(--green)':f.key==='disabled'?'rgba(239,68,68,0.2)':'var(--blue)'):f.key==='enabled'?'rgba(34,197,94,0.1)':f.key==='disabled'?'rgba(239,68,68,0.1)':'transparent',color:enableFilter===f.key?(f.key==='all'?'#fff':f.key):'var(--muted)',border:f.key==='enabled'?'var(--green)':f.key==='disabled'&&enableFilter!=='disabled'?'rgba(239,68,68,0.4)':'1px solid var(--border)',borderRadius:6,cursor:'pointer',fontSize:12,fontWeight:enableFilter===f.key?600:400 }}>{f.label}</button>))}
      </div>
      <div style={{ display:'flex',gap:6,marginBottom:8 }}>
        {[{key:'all',label:`All Classes`},{key:'stock',label:`Stock (${stockCount})`},{key:'crypto',label:`Crypto (${cryptoCount})`}].map(f => (
          <button key={f.key} onClick={()=>setAssetFilter(f.key)} style={{ padding:'5px 12px',background:assetFilter===f.key?'var(--blue)':'transparent',color:assetFilter===f.key?'#fff':'var(--muted)',border:'1px solid var(--border)',borderRadius:6,cursor:'pointer',fontSize:12,fontWeight:assetFilter===f.key?600:400 }}>{f.label}</button>))}
      </div>

      <div style={{ marginBottom:16 }}>
        <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="Search by symbol..." style={{ width:'100%',padding:'8px 12px',background:'var(--surface)',border:'1px solid var(--border)',borderRadius:6,color:'#e4e6eb',fontSize:13 }} />
      </div>

      <button onClick={addAsset} style={{ marginBottom:16,padding:'8px 16px',background:'var(--blue)',border:'none',borderRadius:6,color:'#fff',cursor:'pointer',fontWeight:600,fontSize:13 }}>+ Add Asset</button>

      {/* ═══ SECTION 3: ASSET LIST ═══ */}
      {visibleAssets.length === 0 && <div style={{ padding:24,textAlign:'center',color:'var(--muted)' }}>{assets.length===0 ? 'No assets configured.' : searchQuery ? `No assets match "${searchQuery}".` : 'No assets match the selected filter.'}</div>}

      {visibleAssets.map((asset) => {
        const globalIdx = assets.findIndex(a => a === asset);
        const isExpanded = expandedAssetIdx === globalIdx;

        return (
          <div key={globalIdx} style={{ marginBottom:4 }}>
            {/* Summary row */}
            <div onClick={() => startEditing(globalIdx)} style={{ display:'flex',justifyContent:'space-between',alignItems:'center',padding:'10px 16px',borderRadius:8,border:'1px solid var(--border)',background:isExpanded?'#0f1117':'var(--surface)',cursor:'pointer' }}>
              <div style={{ display:'flex',alignItems:'center',gap:12 }}>
                <span style={{ fontWeight:600,fontSize:14 }}>{asset.symbol || '(no symbol)'}</span>
                <span onClick={(e)=>{e.stopPropagation();toggleEnabledLocal(globalIdx)}} style={{ fontSize:9,padding:'2px 8px',borderRadius:99,fontWeight:600,cursor:'pointer',background:asset.enabled?'rgba(34,197,94,0.15)':'rgba(239,68,68,0.15)',color:asset.enabled?'#22c55e':'#ef4444',userSelect:'none' }}>{asset.enabled ? 'ON' : 'OFF'}</span>
                <span style={{ fontSize:10,padding:'2px 8px',borderRadius:99,background:asset.asset_class==='crypto'?'rgba(251,191,36,0.15)':'rgba(148,163,184,0.15)',color:asset.asset_class==='crypto'?'#fbbf24':'var(--muted)' }}>{asset.asset_class}</span>
              </div>
              <span style={{ fontSize:11,color:'var(--muted)' }}>{(asset.tp_levels||[]).length} TP · {(asset.strategies||[]).length} strat</span>
            </div>

            {/* Expanded panel */}
            {isExpanded && (
              <div style={{ padding:'4px 0 16px 24px',background:'#0a0b10',borderRadius:'0 0 8px 8px',border:'1px solid var(--border)',borderTop:'none' }}>
                {/* Symbol + remove */}
                <div style={{ display:'flex',gap:12,marginBottom:12,alignItems:'center' }}>
                  <input value={asset.symbol||''} onChange={e=>updateField(globalIdx,'symbol',e.target.value)} placeholder="Symbol" style={{ fontSize:16,fontWeight:600,background:'transparent',border:'none',borderBottom:'2px solid var(--border)',color:'#e4e6eb',padding:'4px 0',width:180 }} />
                  <button onClick={()=>removeAsset(globalIdx)} style={{ padding:'4px 12px',background:'rgba(239,68,68,0.15)',border:'1px solid var(--red)',borderRadius:4,color:'var(--red)',cursor:'pointer',fontSize:12 }}>Remove</button>
                </div>

                {/* Config fields */}
                <div style={{ display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(160px,1fr))',gap:12,marginBottom:16 }}>
                  <label style={{ fontSize:11 }}><span style={{ color:'var(--muted)' }}>Class</span>
                    <select value={asset.asset_class||'stock'} onChange={e=>updateField(globalIdx,'asset_class',e.target.value)} style={{ width:'100%',marginTop:4,padding:'6px 8px',background:'#0f1117',border:'1px solid var(--border)',borderRadius:4,color:'#e4e6eb' }}>
                      <option value="stock">Stock</option><option value="crypto">Crypto</option>
                    </select></label>
                  <label style={{ fontSize:11 }}><span style={{ color:'var(--muted)' }}>Max Position ($)</span>
                    <NumInput value={numVal(asset.max_position_dollar)} onChange={v=>updateField(globalIdx,'max_position_dollar',Number(v))} step="0.1" style={{ width:'100%',marginTop:4,padding:'6px 8px',background:'#0f1117',border:'1px solid var(--border)',borderRadius:4,color:'#e4e6eb' }} /></label>
                  <label style={{ fontSize:11 }}><span style={{ color:'var(--muted)' }}>Stop Loss %</span>
                    <NumInput value={numVal(asset.sl_pct)} onChange={v=>updateField(globalIdx,'sl_pct',Number(v))} step="0.1" style={{ width:'100%',marginTop:4,padding:'6px 8px',background:'#0f1117',border:'1px solid var(--border)',borderRadius:4,color:'#e4e6eb' }} /></label>
                  <label style={{ fontSize:11 }}><span style={{ color:'var(--muted)' }}>Trail Stop %</span>
                    <NumInput value={numVal(asset.trailing_stop_pct)} onChange={v=>updateField(globalIdx,'trailing_stop_pct',Number(v))} step="0.1" style={{ width:'100%',marginTop:4,padding:'6px 8px',background:'#0f1117',border:'1px solid var(--border)',borderRadius:4,color:'#e4e6eb' }} /></label>
                </div>

                {/* ═══ SECTION 4: INDIVIDUAL STRATEGIES — flat list, no groups ═══ */}
                <div style={{ borderTop:'1px solid var(--border)',paddingTop:12 }}>
                  <div style={{ fontSize:12,fontWeight:600,marginBottom:8 }}>Strategies (toggle on/off)</div>

                  {uniqueStrats.length === 0 ? (
                    <div style={{ fontSize:12,color:'var(--muted)' }}>No strategies loaded.</div>
                  ) : uniqueStrats.map(({name:cname}) => {
                      const active = (asset.strategies || []).includes(cname);
                      const defKeys = STRATEGY_DEFS[cname] || {};

                      return (
                        <div key={cname} style={{ marginBottom:4 }}>
                          <label style={{ display:'flex',alignItems:'center',gap:8,cursor:'pointer',padding:'6px 10px',borderRadius:6,background:active?'rgba(59,130,246,0.08)':'transparent' }}>
                            <input type="checkbox" checked={active} onChange={()=>handleConcreteToggle(globalIdx,cname)} style={{ cursor:'pointer',accentColor:'var(--blue)' }} />
                            <span style={{ fontSize:12,fontWeight:active?600:400 }}>{cname}</span>
                            {active && (
                              <button onClick={(e) => { e.stopPropagation(); copyStrategyDefaults(globalIdx, cname); }} style={{ fontSize:9,padding:'1px 6px',marginLeft:'auto',background:'rgba(251,191,36,0.1)',border:'1px solid var(--amber)',borderRadius:3,color:'var(--amber)',cursor:'pointer' }}>
                                Copy Defaults
                              </button>
                            )}
                          </label>
                          {active && Object.keys(defKeys).length > 0 && (
                            <ParamEditor assetIdx={globalIdx} concreteName={cname} paramKeys={defKeys} currentParams={asset.strategy_params?.[cname] || {}} updateParam={updateStrategyParam} />
                          )}
                        </div>);})}
                </div>

                {/* ═══ SECTION 5: TP LADDER ═══ */}
                <div style={{ borderTop:'1px solid var(--border)',paddingTop:12,marginTop:12 }}>
                  <div style={{ display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8 }}>
                    <span style={{ fontSize:12,fontWeight:600 }}>Take Profit Ladder</span>
                    <div style={{ display:'flex',gap:4 }}>
                      {defaults?.tp_levels_default && (
                        <button onClick={()=>{
                          const defTp = defaults.tp_levels_default;
                          setAssets(prev => {
                            const c = {...prev[globalIdx]};
                            c.tp_levels = defTp.map((l,i) => ({...l, level: i + 1}));
                            return [...prev.slice(0, globalIdx), {...c}, ...prev.slice(globalIdx + 1)];
                          });
                        }} style={{ padding:'3px 10px',fontSize:11,background:'rgba(251,191,36,0.1)',border:'1px solid var(--amber)',borderRadius:4,color:'var(--amber)',cursor:'pointer' }}>Apply Defaults</button>
                      )}
                      <button onClick={()=>addTpLevel(globalIdx)} style={{ padding:'3px 10px',fontSize:11,background:'var(--blue)',border:'none',borderRadius:4,color:'#fff',cursor:'pointer' }}>+ Add</button>
                    </div>
                  </div>
                  {(asset.tp_levels||[]).map((tp,li) => (
                    <div key={li} style={{ display:'flex',gap:8,alignItems:'center',marginBottom:4 }}>
                      <span style={{ fontSize:11,color:'var(--muted)',minWidth:240 }}>L{tp.level}: at {tp.profit_pct}% profit sell {(tp.sell_pct * 100).toFixed(1)}% of asset</span>
                      <NumInput value={numVal(tp.sell_pct)} onChange={v=>updateTpLevel(globalIdx,li,'sell_pct',parseFloat(v)||0)} step="0.01" min="0" max="1" style={{ width:70,padding:'4px 6px',fontSize:12,background:'#0f1117',border:'1px solid var(--border)',borderRadius:4,color:'#e4e6eb' }} />
                      <NumInput value={numVal(tp.profit_pct)} onChange={v=>updateTpLevel(globalIdx,li,'profit_pct',parseFloat(v)||0)} step="any" style={{ width:70,padding:'4px 6px',fontSize:12,background:'#0f1117',border:'1px solid var(--border)',borderRadius:4,color:'#e4e6eb' }} />
                      {(asset.tp_levels||[]).length > 1 && <button onClick={()=>removeTpLevel(globalIdx,li)} style={{ padding:'2px 8px',fontSize:10,background:'rgba(239,68,68,0.15)',border:'none',borderRadius:4,color:'var(--red)',cursor:'pointer' }}>×</button>}
                    </div>))}
                </div>

                {/* Save / Cancel */}
                <div style={{ marginTop:16,display:'flex',gap:8 }}>
                  <button onClick={handleSave} style={{ padding:'8px 20px',background:'var(--green)',border:'none',borderRadius:6,color:'#fff',fontWeight:600,cursor:'pointer' }}>Save</button>
                  <button onClick={handleCancel} style={{ padding:'8px 20px',background:'transparent',border:'1px solid var(--border)',borderRadius:6,color:'var(--muted)',cursor:'pointer' }}>Cancel</button>
                </div>
              </div>
            )}
          </div>);})}

      {expandedAssetIdx === null && visibleAssets.length > 0 && <div style={{ marginTop:16,fontSize:12,color:'var(--muted)',textAlign:'center' }}>Click an asset to edit</div>}
    </>
  );
}
