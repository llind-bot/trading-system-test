import { useState, useEffect, useRef } from 'react';

/**
 * useWebSocket — manages a single WS connection to /ws/live.
 * Calls onMessage(type, data) when a typed event arrives.
 * Returns { status }.
 */
export function useWebSocket(onMessage) {
  const [status, setStatus] = useState('disconnected');
  const wsRef = useRef(null);

  useEffect(() => {
    if (!onMessage) return;

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = proto + '//' + location.host + '/ws/live';
    const ws = new WebSocket(url);

    ws.onopen = () => setStatus('connected');
    ws.onclose = () => setStatus('disconnected');
    ws.onerror = () => setStatus('error');
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        onMessage(msg.type, msg.data);
      } catch {}
    };

    wsRef.current = ws;

    return () => {
      try { ws.close(); } catch {}
      wsRef.current = null;
    };
  }, [onMessage]);

  return { status };
}
