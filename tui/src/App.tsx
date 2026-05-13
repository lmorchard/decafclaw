import React, { useEffect, useReducer, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";
import TextInput from "ink-text-input";
import type { WSClient, WSEvent } from "./wsClient.js";
import { dispatch, initialState, type State } from "./dispatcher.js";
import type { ServerMessage } from "./types.js";

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
  | { kind: "closed"; code: number; reason: string };

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
  }
}

export function App({ client, initialConvId }: AppProps): React.JSX.Element {
  const [state, dispatchUi] = useReducer(reducer, initialState);
  const [draft, setDraft] = useState("");
  const { exit } = useApp();

  useEffect(() => {
    // Capture-once subscription: registered once on mount. Re-registering on
    // every render would stack up duplicate handlers. The stale-closure trade-off
    // is accepted for the spike: `state.conv_id` inside this handler always reads
    // the mount-time value (null). The __reconnected branch uses it to re-select
    // the conversation — this will silently no-op until T9 provides a ref-based
    // fix. Happy-path smoke (no reconnect) is unaffected.
    client.on((e: WSEvent) => {
      if (e.type === "__reconnected") {
        dispatchUi({ kind: "reconnected" });
        if (state.conv_id) client.send({ type: "select_conv", conv_id: state.conv_id });
      } else if (e.type === "__auth_failed") {
        dispatchUi({ kind: "auth_failed", reason: e.reason });
      } else if (e.type === "__closed") {
        dispatchUi({ kind: "closed", code: e.code, reason: e.reason });
      } else {
        dispatchUi({ kind: "wire", msg: e });
      }
    });

    if (initialConvId) {
      // Small delay to let the socket open before sending select_conv.
      // WSClient silently drops sends when not yet OPEN, and a proper
      // buffered-send is out of scope for the spike.
      const t = setInterval(() => {
        client.send({ type: "select_conv", conv_id: initialConvId });
        clearInterval(t);
      }, 100);
      return () => clearInterval(t);
    }
    return undefined;
  }, []);

  useInput((_input, key) => {
    if (key.ctrl && _input === "c") {
      client.close();
      exit();
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
      <Box>
        <Text>{state.conv_id ? "> " : "(connecting...) "}</Text>
        <TextInput value={draft} onChange={setDraft} onSubmit={onSubmit} />
      </Box>
    </Box>
  );
}
