import React, { useEffect, useReducer, useRef, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import TextInput from "ink-text-input";
import type { WSClient, WSEvent } from "./wsClient.js";
import { dispatch, initialState, type State } from "./dispatcher.js";
import type { ServerMessage } from "./types.generated.js";
import { ConversationPicker } from "./conversationPicker.js";

export interface AppProps {
  client: WSClient;
  initialConvId: string | null;
  host: string;
  token: string;
}

type Action =
  | { kind: "wire"; msg: ServerMessage }
  | { kind: "reconnected" }
  | { kind: "auth_failed"; reason: string }
  | { kind: "closed"; code: number; reason: string }
  | { kind: "clear_confirm" };

function reducer(s: State, a: Action): State {
  switch (a.kind) {
    case "wire":
      return dispatch(s, a.msg);
    case "reconnected":
      return {
        ...s,
        transcript: [...s.transcript, { kind: "system", text: "[reconnected]" }],
      };
    case "auth_failed":
      return {
        ...s,
        transcript: [
          ...s.transcript,
          { kind: "system", text: `[auth failed: ${a.reason}]` },
        ],
      };
    case "closed":
      return s;
    case "clear_confirm":
      return { ...s, confirm: null };
  }
}

export function App({
  client,
  initialConvId,
  host,
  token,
}: AppProps): React.JSX.Element {
  const [state, dispatchUi] = useReducer(reducer, initialState);
  const [draft, setDraft] = useState("");
  const { exit } = useApp();
  const [pickedConv, setPickedConv] = useState<string | null>(initialConvId);

  // Live pointer to the currently-active conversation. The WS subscription
  // effect runs once on mount with stale-closed state, so it reads through
  // this ref to know which conv to re-select after a reconnect.
  const activeConvIdRef = useRef<string | null>(initialConvId);
  useEffect(() => {
    activeConvIdRef.current = pickedConv;
  }, [pickedConv]);

  // Subscribe to WS events once on mount. The empty dep array is deliberate —
  // re-subscribing on every state change would accumulate handlers (wsClient.on
  // is append-only). State references inside the handler are stale-closed; use
  // activeConvIdRef.current for the currently-picked conversation id.
  useEffect(() => {
    client.on((e: WSEvent) => {
      if (e.type === "__reconnected") {
        dispatchUi({ kind: "reconnected" });
        const liveConvId = activeConvIdRef.current;
        if (liveConvId) client.send({ type: "select_conv", conv_id: liveConvId });
      } else if (e.type === "__auth_failed") {
        dispatchUi({ kind: "auth_failed", reason: e.reason });
      } else if (e.type === "__closed") {
        dispatchUi({ kind: "closed", code: e.code, reason: e.reason });
      } else {
        dispatchUi({ kind: "wire", msg: e });
      }
    });
  }, []);

  // Select the active conversation whenever the user picks one. WSClient
  // silently drops sends before the socket is OPEN, and we don't have a
  // buffered-send yet — so poll until conv_selected echoes back. We can detect
  // that by watching state.conv_id (the dispatcher sets it on conv_selected).
  useEffect(() => {
    if (!pickedConv) return undefined;
    let cancelled = false;
    const t = setInterval(() => {
      if (cancelled) return;
      if (state.conv_id === pickedConv) {
        clearInterval(t);
        return;
      }
      client.send({ type: "select_conv", conv_id: pickedConv });
    }, 250);
    // Fire one attempt immediately so the common fast-open case doesn't wait.
    client.send({ type: "select_conv", conv_id: pickedConv });
    return () => {
      cancelled = true;
      clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickedConv, state.conv_id]);

  const [cancelArmed, setCancelArmed] = useState(false);

  useInput((input, key) => {
    // Universal escape: Ctrl+C always works, even mid-confirm.
    // Mirrors what users expect from CLI prompts everywhere.
    if (key.ctrl && input === "c") {
      // If a turn is in flight (and we haven't already armed), send cancel_turn
      // and arm for 2s — a second Ctrl+C within that window forces exit.
      if (state.turnInFlight && !cancelArmed && state.conv_id) {
        client.send({ type: "cancel_turn", conv_id: state.conv_id });
        setCancelArmed(true);
        setTimeout(() => setCancelArmed(false), 2000);
        return;
      }
      // Otherwise: close cleanly and exit. This also fires from inside a
      // confirm prompt — server will time out the confirm on its end.
      client.close();
      exit();
      return;
    }

    // Confirm prompt active: y/n/a keys send decision.
    if (state.confirm) {
      // Map UI keys to the server's flat-flag confirm shape (websocket.py).
      // y → approved once, n → deny, a → approved + always.
      const decision =
        input === "y" || input === "Y" ? { approved: true, always: false } :
        input === "n" || input === "N" ? { approved: false, always: false } :
        input === "a" || input === "A" ? { approved: true, always: true } :
        null;
      if (decision && state.conv_id) {
        client.send({
          type: "confirm_response",
          conv_id: state.conv_id,
          confirmation_id: state.confirm.confirmation_id,
          approved: decision.approved,
          always: decision.always,
          add_pattern: false,
        });
        dispatchUi({ kind: "clear_confirm" });
      }
      return;
    }
  });

  function onSubmit(text: string): void {
    if (!state.conv_id) return;
    if (!text.trim()) return;
    client.send({
      type: "send",
      conv_id: state.conv_id,
      text,
      attachments: [],
    });
    setDraft("");
  }

  if (!pickedConv) {
    return (
      <ConversationPicker
        host={host}
        token={token}
        onPick={(id) => setPickedConv(id)}
      />
    );
  }

  return (
    <Box flexDirection="column">
      {state.transcript.map((item, i) => (
        <Text key={i} color={item.kind === "system" ? "yellow" : undefined}>
          {item.kind === "user" ? "you> " : item.kind === "assistant" ? "bot> " : ""}
          {item.text}
        </Text>
      ))}
      {state.draft && <Text color="cyan">{"bot> "}{state.draft}</Text>}
      {state.activity && (
        <Text color="gray">
          [{state.activity.name}] {state.activity.status || "running..."}
        </Text>
      )}
      {state.confirm && (
        <Box flexDirection="column">
          <Text color="magenta">
            confirm ({state.confirm.action_type}): {state.confirm.command || state.confirm.message}
          </Text>
          <Text color="magenta">[y]es / [n]o / [a]lways</Text>
        </Box>
      )}
      <Box>
        <Text>{state.conv_id ? "> " : "(connecting...) "}</Text>
        {!state.confirm && (
          <TextInput value={draft} onChange={setDraft} onSubmit={onSubmit} />
        )}
      </Box>
    </Box>
  );
}
