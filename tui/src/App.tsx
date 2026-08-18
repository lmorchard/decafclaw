import React, { useEffect, useReducer, useRef, useState } from "react";
import { Box, useApp, useInput, Text } from "ink";
import type { WSClient, WSEvent } from "./wsClient.js";
import { dispatch, initialState, type State } from "./dispatcher.js";
import type { ServerMessage } from "./types.generated.js";
import { Sidebar } from "./Sidebar.js";
import { ChatLog } from "./ChatLog.js";

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
    case "closed": {
      const reason = a.reason ? ` ${a.reason}` : "";
      return {
        ...s,
        transcript: [
          ...s.transcript,
          { kind: "system", text: `[disconnected: ${a.code}${reason}]` },
        ],
      };
    }
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
  const { exit } = useApp();
  const [pickedConv, setPickedConv] = useState<string | null>(initialConvId);
  const [focus, setFocus] = useState<"sidebar" | "chat">("sidebar");


  const activeConvIdRef = useRef<string | null>(initialConvId);
  useEffect(() => {
    activeConvIdRef.current = pickedConv;
  }, [pickedConv]);

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

  useEffect(() => {
    if (!pickedConv) return;
    if (state.conv_id === pickedConv) return;
    client.send({ type: "select_conv", conv_id: pickedConv });
  }, [pickedConv, state.conv_id, client]);

  const [cancelArmed, setCancelArmed] = useState(false);
  const cancelArmedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (cancelArmedTimerRef.current) {
        clearTimeout(cancelArmedTimerRef.current);
      }
    };
  }, []);

  useInput((input, key) => {
    if (key.ctrl && input === "c") {
      if (state.turnInFlight && !cancelArmed && state.conv_id) {
        client.send({ type: "cancel_turn", conv_id: state.conv_id });
        setCancelArmed(true);
        if (cancelArmedTimerRef.current) {
          clearTimeout(cancelArmedTimerRef.current);
        }
        cancelArmedTimerRef.current = setTimeout(() => {
          cancelArmedTimerRef.current = null;
          setCancelArmed(false);
        }, 2000);
        return;
      }
      client.close();
      exit();
      return;
    }

    if (key.tab || input === "\t") {
      setFocus(prev => prev === "sidebar" ? "chat" : "sidebar");
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
  }

  function handleDecision(decision: { approved: boolean; always: boolean } | null) {
    if (decision && state.conv_id && state.confirm) {
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
  }

  return (
    <Box flexDirection="row" width="100%" height="100%">
      <Sidebar
        host={host}
        token={token}
        pickedConv={pickedConv}
        isFocused={focus === "sidebar" && !state.confirm}
        onPick={(id) => {
          setPickedConv(id);
          setFocus("chat");
        }}
        onExit={() => {
          client.close();
          exit();
        }}
      />
      
      <ChatLog 
        state={state} 
        isFocused={focus === "chat" && !state.confirm} 
        onSubmit={onSubmit}
        onDecision={handleDecision}
      />
    </Box>
  );
}
