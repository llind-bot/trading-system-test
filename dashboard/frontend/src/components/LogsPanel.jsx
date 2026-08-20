import React, { useState, useEffect, useCallback } from 'react';
import { fetchLogFiles, fetchLogLines } from '../lib/api.js';

/* ── Main panel ──────────────────────────────────────────────── */
export default function LogsPanel() {
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [logData, setLogData] = useState(null);
  const [numLines, setNumLines] = useState(100);
  const [refreshInterval, setRefreshInterval] = useState('manual'); // manual | 5 | 15 | 30 | 60
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [error, setError] = useState(null);

  // Fetch file list once on mount
  useEffect(() => {
    fetchLogFiles().then(resp => setFiles(resp.files || [])).catch(e => setError('Failed to list log files: ' + e.message));
  }, []);

  // Load log data whenever file/numLines/search changes (manual refresh)
  const loadLog = useCallback(async () => {
    if (!selectedFile) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetchLogLines(selectedFile, numLines);
      setLogData(resp);
      setLastUpdated(new Date());
    } catch(e) {
      setError('Failed to load log: ' + e.message);
    } finally {
      setLoading(false);
    }
  }, [selectedFile, numLines]);

  useEffect(() => {
    loadLog();
  }, [loadLog]);

  // Auto-refresh interval
  useEffect(() => {
    if (refreshInterval === 'manual' || !selectedFile) return;
    const ms = parseInt(refreshInterval) * 1000;
    const id = setInterval(loadLog, ms);
    return () => clearInterval(id);
  }, [refreshInterval, selectedFile, loadLog]);

  // Log data for rendering
  const filtered = searchQuery
    ? logData?.lines?.filter(line => line.toLowerCase().includes(searchQuery.toLowerCase()))
    : logData?.lines || [];

  const formatLine = (line) => {
    let text = line;
    let bg = '';
    let color = 'var(--text)';
    
    // Colorize ERROR lines red, WARN yellow
    if (/\bERROR\b/.test(line)) {
      bg = 'rgba(220, 53, 69, 0.1)';
      color = '#ff6b7a';
    } else if (/\bWARN(ING)?\b/.test(line)) {
      bg = 'rgba(255, 193, 7, 0.08)';
      color = '#ffc107';
    }

    // Highlight search term in the line
    if (searchQuery) {
      const idx = text.toLowerCase().indexOf(searchQuery.toLowerCase());
      if (idx >= 0) {
        const before = text.slice(0, idx);
        const match = text.slice(idx, idx + searchQuery.length);
        const after = text.slice(idx + searchQuery.length);
        return (
          <span key={line} style={{ display: 'block', padding: '2px 8px', background: bg, color, fontSize: 12, fontFamily: 'monospace' }}>
            {before}<mark style={{ background: '#ffc107', color: 'inherit', borderRadius: 2 }}>{match}</mark>{after}
          </span>
        );
      }
    }

    return (
      <span key={line} style={{ display: 'block', padding: '2px 8px', background: bg, fontSize: 12, fontFamily: 'monospace' }}>
        {text}
      </span>
    );
  };

  return (
    <div style={{ maxWidth: '100%' }}>
      <h2 style={{ margin:'0 0 16px', fontSize:18 }}>Logs</h2>

      {/* Controls */}
      <div style={{ 
        display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center',
        background: 'var(--surface)', padding: 14, borderRadius: 8, border: '1px solid var(--border)' 
      }}>
        {/* Log file selector */}
        <select 
          value={selectedFile || ''} 
          onChange={e => setSelectedFile(e.target.value)}
          style={{ flex: '1 1 200px', padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
        >
          <option value="">Select a log file...</option>
          {files.map(f => (
            <option key={f.name} value={f.name}>
              {f.name} ({f.size_kb} KB)
            </option>
          ))}
        </select>

        {/* Lines selector */}
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
          Lines:
          <input 
            type="number" min={10} max={2000} value={numLines} 
            onChange={e => setNumLines(parseInt(e.target.value) || 100)}
            style={{ width: 60, padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
          />
        </label>

        {/* Search */}
        <input 
          type="text" 
          placeholder="Filter (client-side)..." 
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          style={{ flex: '1 1 180px', padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
        />

        {/* Refresh interval */}
        <select 
          value={refreshInterval}
          onChange={e => setRefreshInterval(e.target.value)}
          style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)' }}
        >
          <option value="manual">Manual only</option>
          <option value="5">Every 5s</option>
          <option value="15">Every 15s</option>
          <option value="30">Every 30s</option>
          <option value="60">Every 60s</option>
        </select>

        {/* Manual refresh button */}
        <button 
          onClick={loadLog} disabled={loading || !selectedFile}
          style={{ padding: '8px 14px', borderRadius: 6, border: 'none', background: selectedFile ? 'var(--blue)' : 'var(--muted)', color: '#fff', cursor: selectedFile ? 'pointer' : 'default', fontSize: 12, fontWeight: 500 }}
        >
          {loading ? 'Loading...' : '↻ Refresh'}
        </button>
      </div>

      {/* Status bar */}
      <div style={{ padding: '8px 14px', fontSize: 11, color: 'var(--muted)', display: 'flex', alignItems: 'center', gap: 8 }}>
        {refreshInterval === 'manual' ? (
          lastUpdated && <span>Last updated: {lastUpdated.toLocaleTimeString()}</span>
        ) : (
          <span style={{ color: 'var(--green)' }}>● Auto-refresh every {refreshInterval}s</span>
        )}
      </div>

      {/* Error */}
      {error && <div style={{ background: 'rgba(220,53,69,0.1)', color: '#ff6b7a', padding: 12, borderRadius: 6, fontSize: 12 }}>{error}</div>}

      {/* Log viewer */}
      {selectedFile && (
        <div style={{ marginTop: 8 }}>
          {logData ? (
            <>
              {/* Metadata bar */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', fontSize: 11, color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
                <span>{logData.file}</span>
                <span>·</span>
                <span>{logData.size_kb} KB</span>
                <span>·</span>
                <span>{logData.returned_lines} lines shown</span>
                {searchQuery && (
                  <>
                    <span>·</span>
                    <span style={{ color: 'var(--blue)' }}>filtered by "{searchQuery}"</span>
                  </>
                )}
              </div>

              {/* Log lines */}
              <div style={{ 
                overflowY: 'auto', 
                maxHeight: 500, 
                padding: 0,
                background: '#1e1e2e',
                minHeight: 150,
              }}>
                {filtered.length > 0 ? (
                  filtered.map(formatLine)
                ) : (
                  <div style={{ color: 'var(--muted)', padding: 20, textAlign: 'center' }}>No matching lines for "{searchQuery}"</div>
                )}
              </div>
            </>
          ) : (
            <div style={{ color: '#8b949e', padding: 20, textAlign: 'center' }}>Loading log data...</div>
          )}
        </div>
      )}

      {/* Initial prompt if no file selected */}
      {!selectedFile && (
        <div style={{ color: '#8b949e', padding: 20, textAlign: 'center' }}>Select a log file from the dropdown above to view its contents.</div>
      )}
    </div>
  );
}
