import { useState, useEffect, useRef, useCallback } from 'react';
import { AppState, AppStateStatus } from 'react-native';

interface UseWebSocketOptions {
  host: string;
  token: string;
  onChunk: (text: string) => void;
  onToolStart: (tool: string) => void;
  onToolEnd: (tool: string) => void;
  onTurnComplete: () => void;
}

export function useDecafClawWebSocket({
  host,
  token,
  onChunk,
  onToolStart,
  onToolEnd,
  onTurnComplete,
}: UseWebSocketOptions) {
  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState('Connecting...');
  const [convId, setConvId] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryCountRef = useRef<number>(0);

  // Auto-reconnect with exponential backoff
  const scheduleReconnect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    const delay = Math.min(2000 * Math.pow(1.5, retryCountRef.current), 10000);
    retryCountRef.current += 1;
    setStatus(`Reconnect in ${Math.round(delay / 1000)}s...`);

    reconnectTimeoutRef.current = setTimeout(() => {
      connect();
    }, delay);
  }, []);

  const connect = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch (e) {}
      wsRef.current = null;
    }

    const wsUrl = host.replace(/^http/, 'ws') + '/ws/chat';
    setStatus('Connecting...');

    try {
      const ws = new (WebSocket as any)(wsUrl, [], {
        headers: {
          Cookie: `decafclaw_session=${token}`,
        },
      });

      ws.onopen = () => {
        retryCountRef.current = 0;
        setConnected(true);
        setStatus('Connected');
      };

      ws.onmessage = (e: any) => {
        try {
          const event = JSON.parse(e.data);
          if (event.type === 'chunk') {
            onChunk(event.text || '');
            setStatus('Speaking...');
          } else if (event.type === 'tool_start') {
            onToolStart(event.tool || 'tool');
            setStatus(`Tool: ${event.tool}...`);
          } else if (event.type === 'tool_end') {
            onToolEnd(event.tool || 'tool');
            setStatus(`Tool done: ${event.tool}`);
          } else if (event.type === 'turn_complete') {
            onTurnComplete();
            setStatus('Ready');
          }
        } catch (err) {
          console.error('Failed to parse WS event', err);
        }
      };

      ws.onerror = (e: any) => {
        console.warn('WebSocket error:', e);
        setConnected(false);
        setStatus('Conn Error');
      };

      ws.onclose = () => {
        setConnected(false);
        setStatus('Disconnected');
        scheduleReconnect();
      };

      wsRef.current = ws;
    } catch (err) {
      setConnected(false);
      setStatus('Failed to connect');
      scheduleReconnect();
    }
  }, [host, token, onChunk, onToolStart, onToolEnd, onTurnComplete, scheduleReconnect]);

  // Connect on mount
  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  // Reconnect when returning to foreground
  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextAppState: AppStateStatus) => {
      if (nextAppState === 'active') {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
          retryCountRef.current = 0;
          connect();
        }
      }
    });
    return () => subscription.remove();
  }, [connect]);

  // Manual reconnect trigger
  const manualReconnect = useCallback(() => {
    retryCountRef.current = 0;
    connect();
  }, [connect]);

  // Dispatch spoken turn to DecafClaw
  const sendTurn = useCallback(
    async (text: string) => {
      setStatus('Dispatching turn...');

      let activeConvId = convId;
      if (!activeConvId) {
        try {
          const resp = await fetch(`${host}/api/conversations`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Cookie: `decafclaw_session=${token}`,
            },
            body: JSON.stringify({ title: 'Pixel Pocket Session' }),
          });
          const data = await resp.json();
          activeConvId = data.conv_id;
          setConvId(activeConvId);
        } catch (err) {
          setStatus('Failed to create session');
          return;
        }
      }

      const payload = {
        type: 'send',
        conv_id: activeConvId,
        text: `[Voice Session: Pixel 10 Pro Tensor STT] ${text}`,
        attachments: [],
      };

      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify(payload));
        setStatus('Thinking...');
      } else {
        setStatus('Reconnecting socket...');
        connect();
      }
    },
    [convId, host, token, connect]
  );

  return {
    connected,
    status,
    manualReconnect,
    sendTurn,
  };
}
