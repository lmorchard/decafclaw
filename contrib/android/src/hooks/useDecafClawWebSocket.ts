import { useState, useEffect, useRef, useCallback } from 'react';
import { AppState, AppStateStatus } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

interface UseWebSocketOptions {
  host: string;
  token: string;
  customLobbyConvId?: string;
  onChunk: (text: string) => void;
  onTurnComplete: () => void;
  onError: (message: string) => void;
  onHistoryRecovered: (text: string) => void;
  onConversationSwitched?: (convId: string, title: string) => void;
}

interface ServerMessage {
  type?: string;
  text?: string;
  tool?: string;
  message?: string;
  conv_id?: string;
  target_conv_id?: string;
  title?: string;
  reason?: string;
  messages?: unknown[];
}

interface QueuedTurn {
  convId: string;
  text: string;
}

const RECONNECT_BASE_MS = 2_000;
const RECONNECT_MAX_MS = 10_000;
const TURN_TIMEOUT_MS = 45_000;
const HISTORY_LIMIT = 8;
const AUTH_FAILURE_CLOSE_CODE = 4001;
const STORAGE_KEY_CONV_ID = '@decafclaw_conv_id';

interface RNWebSocketOptions {
  headers: Record<string, string>;
}

interface RNWebSocketConstructor {
  new (
    url: string,
    protocols?: string[] | null,
    options?: RNWebSocketOptions
  ): WebSocket;
}

function toWsUrl(host: string): string {
  return host.replace(/^http/, 'ws').replace(/\/+$/, '') + '/ws/chat';
}

function clearTimeoutRef(ref: { current: ReturnType<typeof setTimeout> | null }) {
  if (ref.current) {
    clearTimeout(ref.current);
    ref.current = null;
  }
}

function lastAssistantText(messages: unknown[]): string {
  let lastUserIndex = -1;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const entry = messages[i] as { role?: unknown } | null;
    if (entry?.role === 'user') {
      lastUserIndex = i;
      break;
    }
  }
  for (let i = messages.length - 1; i > lastUserIndex; i -= 1) {
    const entry = messages[i] as { role?: unknown; content?: unknown } | null;
    if (entry?.role === 'assistant' && typeof entry.content === 'string' && entry.content.trim()) {
      return entry.content;
    }
  }
  return '';
}

export function useDecafClawWebSocket(options: UseWebSocketOptions) {
  const { host, token, customLobbyConvId } = options;

  const [connected, setConnected] = useState(false);
  const [status, setStatus] = useState('Connecting...');
  const [conversationId, setConversationId] = useState<string | null>(null);

  const callbacksRef = useRef(options);
  useEffect(() => {
    callbacksRef.current = options;
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const watchdogRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryCountRef = useRef(0);
  const stoppedRef = useRef(false);
  const authFailedRef = useRef(false);
  const connectionInitializedRef = useRef(false);
  const connectRef = useRef<() => void>(() => {});

  const convIdRef = useRef<string | null>(null);
  const convPromiseRef = useRef<Promise<string> | null>(null);
  const pendingTurnsRef = useRef<QueuedTurn[]>([]);
  const turnsInFlightRef = useRef(0);
  const announceVoiceRef = useRef(true);
  const historyRequestConvRef = useRef<string | null>(null);

  const scheduleReconnect = useCallback(() => {
    if (stoppedRef.current || authFailedRef.current || reconnectTimerRef.current) {
      return;
    }
    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(1.5, retryCountRef.current),
      RECONNECT_MAX_MS
    );
    retryCountRef.current += 1;
    setStatus(`Reconnect in ${Math.round(delay / 1000)}s...`);
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      connectRef.current();
    }, delay);
  }, []);

  const sendRaw = useCallback((ws: WebSocket, payload: Record<string, unknown>) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(payload));
    }
  }, []);

  const armWatchdog = useCallback((ws: WebSocket) => {
    clearTimeoutRef(watchdogRef);
    watchdogRef.current = setTimeout(() => {
      watchdogRef.current = null;
      if (wsRef.current !== ws || turnsInFlightRef.current === 0) {
        return;
      }
      callbacksRef.current.onError(
        'No response from DecafClaw for 45s. Reconnecting and reloading the turn.'
      );
      // Drop handlers so the stale socket cannot schedule its own reconnect.
      ws.onopen = null;
      ws.onmessage = null;
      ws.onerror = null;
      ws.onclose = null;
      wsRef.current = null;
      try {
        ws.close();
      } catch {
        // Already closing.
      }
      setConnected(false);
      connectRef.current();
    }, TURN_TIMEOUT_MS);
  }, []);

  const flushQueue = useCallback(
    (ws: WebSocket) => {
      const queued = pendingTurnsRef.current;
      pendingTurnsRef.current = [];
      for (const turn of queued) {
        sendRaw(ws, {
          type: 'send',
          conv_id: turn.convId,
          text: turn.text,
          attachments: [],
        });
      }
      if (queued.length > 0) {
        turnsInFlightRef.current += queued.length;
        setStatus('Thinking...');
        armWatchdog(ws);
      }
    },
    [armWatchdog, sendRaw]
  );

  const requestHistory = useCallback(
    (ws: WebSocket, convId: string) => {
      historyRequestConvRef.current = convId;
      sendRaw(ws, { type: 'select_conv', conv_id: convId });
      sendRaw(ws, { type: 'load_history', conv_id: convId, limit: HISTORY_LIMIT });
    },
    [sendRaw]
  );

  const switchConversation = useCallback(
    (targetConvId: string, title: string) => {
      convIdRef.current = targetConvId;
      setConversationId(targetConvId);
      AsyncStorage.setItem(STORAGE_KEY_CONV_ID, targetConvId).catch(() => {});
      pendingTurnsRef.current = [];
      turnsInFlightRef.current = 0;
      clearTimeoutRef(watchdogRef);
      historyRequestConvRef.current = null;
      const ws = wsRef.current;
      if (ws) {
        sendRaw(ws, { type: 'select_conv', conv_id: targetConvId });
      }
      setStatus(title ? `Switched: ${title}` : 'Switched');
      callbacksRef.current.onConversationSwitched?.(targetConvId, title);
    },
    [sendRaw]
  );

  const clearSavedConversation = useCallback(() => {
    AsyncStorage.removeItem(STORAGE_KEY_CONV_ID).catch(() => {});
  }, []);

  const handleServerMessage = useCallback(
    (event: ServerMessage) => {
      const callbacks = callbacksRef.current;
      const ws = wsRef.current;
      if (ws && turnsInFlightRef.current > 0) {
        // Any server traffic resets the inactivity watchdog.
        armWatchdog(ws);
      }

      switch (event.type) {
        case 'chunk': {
          const text = typeof event.text === 'string' ? event.text : '';
          if (text) {
            callbacks.onChunk(text);
            setStatus('Speaking...');
          }
          return;
        }
        case 'tool_start': {
          const tool = typeof event.tool === 'string' && event.tool ? event.tool : 'tool';
          setStatus(`Tool: ${tool}...`);
          return;
        }
        case 'tool_end':
          setStatus('Thinking...');
          return;
        case 'turn_complete':
          turnsInFlightRef.current = Math.max(0, turnsInFlightRef.current - 1);
          clearTimeoutRef(watchdogRef);
          if (turnsInFlightRef.current > 0 && wsRef.current) {
            armWatchdog(wsRef.current);
          }
          callbacks.onTurnComplete();
          setStatus('Ready');
          return;
        case 'error': {
          const message =
            typeof event.message === 'string' && event.message
              ? event.message
              : 'DecafClaw returned an error.';
          if (message.toLowerCase().includes('conversation not found')) {
            convIdRef.current = null;
            setConversationId(null);
            announceVoiceRef.current = true;
          }
          turnsInFlightRef.current = 0;
          clearTimeoutRef(watchdogRef);
          callbacks.onError(message);
          setStatus('Error');
          return;
        }
        case 'conv_history': {
          if (!event.conv_id || historyRequestConvRef.current !== event.conv_id) {
            return;
          }
          historyRequestConvRef.current = null;
          const messages = Array.isArray(event.messages) ? event.messages : [];
          const text = lastAssistantText(messages);
          turnsInFlightRef.current = 0;
          clearTimeoutRef(watchdogRef);
          if (text) {
            callbacks.onHistoryRecovered(text);
            setStatus('Ready');
          } else {
            setStatus('Awaiting reply...');
          }
          return;
        }
        case 'conv_selected':
          return;
        case 'switch_conversation': {
          const targetConvId =
            typeof event.target_conv_id === 'string' ? event.target_conv_id : '';
          const title = typeof event.title === 'string' ? event.title : '';
          if (targetConvId) {
            switchConversation(targetConvId, title);
          }
          return;
        }
        default:
          return;
      }
    },
    [armWatchdog, switchConversation]
  );

  const connect = useCallback(() => {
    if (stoppedRef.current) {
      return;
    }

    clearTimeoutRef(reconnectTimerRef);
    clearTimeoutRef(watchdogRef);

    // Detach handlers before closing so the replaced socket cannot schedule
    // a reconnect or clobber the state of the socket that replaces it.
    const previous = wsRef.current;
    wsRef.current = null;
    if (previous) {
      previous.onopen = null;
      previous.onmessage = null;
      previous.onerror = null;
      previous.onclose = null;
      try {
        previous.close();
      } catch {
        // Already closed.
      }
    }

    authFailedRef.current = false;
    setStatus('Connecting...');

    let ws: WebSocket;
    try {
      const RNWebSocket = WebSocket as unknown as RNWebSocketConstructor;
      ws = new RNWebSocket(toWsUrl(host), [], {
        headers: { Cookie: `decafclaw_session=${token}` },
      });
    } catch {
      setConnected(false);
      setStatus('Failed to connect');
      scheduleReconnect();
      return;
    }

    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) {
        return;
      }
      retryCountRef.current = 0;
      setConnected(true);
      setStatus('Connected');

      const convId = convIdRef.current;
      if (convId) {
        sendRaw(ws, { type: 'select_conv', conv_id: convId });
      }
      const shouldRecover = Boolean(convId) && turnsInFlightRef.current > 0;
      flushQueue(ws);
      if (convId && shouldRecover) {
        requestHistory(ws, convId);
      }
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) {
        return;
      }
      const raw = typeof event.data === 'string' ? event.data : '';
      if (!raw) {
        return;
      }
      try {
        handleServerMessage(JSON.parse(raw) as ServerMessage);
      } catch (err) {
        console.warn('Failed to parse WS event', err);
      }
    };

    ws.onerror = (event) => {
      if (wsRef.current !== ws) {
        return;
      }
      console.debug('WebSocket error:', event.type);
    };

    ws.onclose = (event) => {
      if (wsRef.current !== ws) {
        return;
      }
      wsRef.current = null;
        clearTimeoutRef(watchdogRef);
      setConnected(false);

      if (event.code === AUTH_FAILURE_CLOSE_CODE) {
        authFailedRef.current = true;
        setStatus('Auth failed — check token');
        return;
      }

      setStatus('Disconnected');
      scheduleReconnect();
    };
  }, [
    host,
    token,
    scheduleReconnect,
    sendRaw,
    flushQueue,
    requestHistory,
    handleServerMessage,
  ]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  // A host or token change starts a fresh conversation on the new server.
  useEffect(() => {
    if (!connectionInitializedRef.current) {
      connectionInitializedRef.current = true;
      return;
    }
    convIdRef.current = null;
    convPromiseRef.current = null;
    pendingTurnsRef.current = [];
    turnsInFlightRef.current = 0;
    announceVoiceRef.current = true;
    historyRequestConvRef.current = null;
    // Defer the state reset so the effect does not synchronously set state.
    const timer = setTimeout(() => setConversationId(null), 0);
    return () => clearTimeout(timer);
  }, [host, token]);

  // Connect on mount and whenever the connection target changes.
  useEffect(() => {
    stoppedRef.current = false;
    // Defer the first connect so the effect does not synchronously set state.
    const timer = setTimeout(connect, 0);
    return () => {
      clearTimeout(timer);
      stoppedRef.current = true;
      clearTimeoutRef(reconnectTimerRef);
        clearTimeoutRef(watchdogRef);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        try {
          ws.close();
        } catch {
          // Already closed.
        }
      }
    };
  }, [connect]);

  // Reconnect when returning to foreground.
  useEffect(() => {
    const subscription = AppState.addEventListener('change', (nextAppState: AppStateStatus) => {
      if (nextAppState !== 'active' || authFailedRef.current) {
        return;
      }
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        retryCountRef.current = 0;
        connect();
      }
    });
    return () => subscription.remove();
  }, [connect]);

  useEffect(() => {
    AsyncStorage.getItem(STORAGE_KEY_CONV_ID)
      .then((saved) => {
        if (saved && saved.trim() && !convIdRef.current) {
          const trimmed = saved.trim();
          convIdRef.current = trimmed;
          setConversationId(trimmed);
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            sendRaw(wsRef.current, { type: 'select_conv', conv_id: trimmed });
          }
        }
      })
      .catch(() => {});
  }, [sendRaw]);

  const manualReconnect = useCallback(() => {
    authFailedRef.current = false;
    retryCountRef.current = 0;
    connect();
  }, [connect]);

  const ensureConversation = useCallback(async (): Promise<string> => {
    if (convIdRef.current) {
      return convIdRef.current;
    }
    if (!convPromiseRef.current) {
      convPromiseRef.current = (async () => {
        // 1. Try restoring from AsyncStorage
        try {
          const saved = await AsyncStorage.getItem(STORAGE_KEY_CONV_ID);
          if (saved && saved.trim()) {
            const trimmed = saved.trim();
            convIdRef.current = trimmed;
            setConversationId(trimmed);
            return trimmed;
          }
        } catch {
          // Fall through
        }

        // 2. Custom lobby if configured
        if (customLobbyConvId && customLobbyConvId.trim()) {
          const custom = customLobbyConvId.trim();
          convIdRef.current = custom;
          setConversationId(custom);
          await AsyncStorage.setItem(STORAGE_KEY_CONV_ID, custom).catch(() => {});
          return custom;
        }

        // 3. Resolve Home Lobby from server
        try {
          const homeResp = await fetch(`${host}/api/conversations/home`, {
            headers: {
              Cookie: `decafclaw_session=${token}`,
            },
          });
          if (homeResp.ok) {
            const homeData = (await homeResp.json()) as { conv_id?: unknown };
            if (typeof homeData.conv_id === 'string' && homeData.conv_id) {
              const homeId = homeData.conv_id;
              convIdRef.current = homeId;
              setConversationId(homeId);
              await AsyncStorage.setItem(STORAGE_KEY_CONV_ID, homeId).catch(() => {});
              announceVoiceRef.current = true;
              return homeId;
            }
          }
        } catch {
          // Fall through to session creation
        }

        // 4. Fallback: create fresh session
        const resp = await fetch(`${host}/api/conversations`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Cookie: `decafclaw_session=${token}`,
          },
          body: JSON.stringify({ title: 'Pixel Pocket Session' }),
        });
        if (!resp.ok) {
          throw new Error(`Session creation failed (HTTP ${resp.status})`);
        }
        const data = (await resp.json()) as { conv_id?: unknown };
        if (typeof data.conv_id !== 'string' || !data.conv_id) {
          throw new Error('Session creation returned no conversation id');
        }
        convIdRef.current = data.conv_id;
        setConversationId(data.conv_id);
        await AsyncStorage.setItem(STORAGE_KEY_CONV_ID, data.conv_id).catch(() => {});
        announceVoiceRef.current = true;
        return data.conv_id;
      })().finally(() => {
        convPromiseRef.current = null;
      });
    }
    return convPromiseRef.current;
  }, [customLobbyConvId, host, token]);

  const cancelTurn = useCallback(() => {
    const convId = convIdRef.current;
    const ws = wsRef.current;
    if (ws && convId && turnsInFlightRef.current > 0) {
      sendRaw(ws, { type: 'cancel_turn', conv_id: convId });
      turnsInFlightRef.current = 0;
      clearTimeoutRef(watchdogRef);
      setStatus('Cancelled');
    }
  }, [sendRaw]);

  const returnToHome = useCallback(async () => {
    let targetId = customLobbyConvId?.trim();
    if (!targetId) {
      try {
        const resp = await fetch(`${host}/api/conversations/home`, {
          headers: { Cookie: `decafclaw_session=${token}` },
        });
        if (resp.ok) {
          const data = (await resp.json()) as { conv_id?: unknown };
          if (typeof data.conv_id === 'string' && data.conv_id) {
            targetId = data.conv_id;
          }
        }
      } catch (err) {
        console.warn('Failed to fetch home conversation:', err);
      }
    }
    if (targetId) {
      switchConversation(targetId, 'Lobby');
      setStatus('Lobby');
    }
  }, [customLobbyConvId, host, switchConversation, token]);

  const sendTurn = useCallback(
    async (text: string) => {
      setStatus('Dispatching turn...');

      let convId: string;
      try {
        convId = await ensureConversation();
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Could not create a session.';
        setStatus('Session failed');
        callbacksRef.current.onError(message);
        return;
      }

      let outgoing = text;
      if (announceVoiceRef.current) {
        announceVoiceRef.current = false;
        outgoing = `[Voice Session: Pixel 10 Pro Tensor STT] ${text}`;
      }

      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        sendRaw(ws, { type: 'send', conv_id: convId, text: outgoing, attachments: [] });
        turnsInFlightRef.current += 1;
        setStatus('Thinking...');
        armWatchdog(ws);
      } else {
        pendingTurnsRef.current.push({ convId, text: outgoing });
        setStatus('Queued — reconnecting...');
        if (!ws || ws.readyState === WebSocket.CLOSED) {
          connectRef.current();
        }
      }
    },
    [armWatchdog, ensureConversation, sendRaw]
  );

  return {
    connected,
    status,
    convId: conversationId,
    manualReconnect,
    sendTurn,
    cancelTurn,
    returnToHome,
    clearSavedConversation,
  };
}
