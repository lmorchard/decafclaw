/**
 * Pure reducer mapping (state, ServerMessage) -> new state.
 *
 * Exhaustive switch with TS `never` guard on default — adding a new
 * ServerMessage variant in types.ts will surface here as a compile error
 * rather than silent drift. See spec.md "Promotion path" section.
 */

import type { ServerMessage } from "./types.js";

export type TranscriptItem =
  | { kind: "user"; text: string }
  | { kind: "assistant"; text: string }
  | { kind: "system"; text: string };

// Spike simplification: tracks one active tool at a time. Phase 2 with
// concurrent tools would need this to become `Activity[]` or a Map.
export type Activity = {
  tool_call_id: string;
  name: string;
  status: string;
} | null;

export type Confirm = {
  request_id: string;
  kind: string;
  payload: Record<string, unknown>;
} | null;

export interface State {
  conv_id: string | null;
  transcript: TranscriptItem[];
  draft: string;
  activity: Activity;
  confirm: Confirm;
  turnInFlight: boolean;
  model: string | null;
}

export const initialState: State = {
  conv_id: null,
  transcript: [],
  draft: "",
  activity: null,
  confirm: null,
  turnInFlight: false,
  model: null,
};

function assertNever(_x: never): void {
  // Exhaustiveness check — TS errors here if a new ServerMessage variant
  // is added without a matching case in `dispatch`.
}

function appendTranscript(s: State, item: TranscriptItem): State {
  return { ...s, transcript: [...s.transcript, item] };
}

function extractText(msg: Record<string, unknown> | null | undefined): string {
  if (!msg) return "";
  const t = msg["text"];
  return typeof t === "string" ? t : "";
}

export function dispatch(s: State, m: ServerMessage): State {
  switch (m.type) {
    case "chunk":
      return { ...s, draft: s.draft + m.text };

    case "message_complete": {
      const text = m.text || s.draft;
      const next = appendTranscript(s, { kind: "assistant", text });
      return { ...next, draft: "" };
    }

    case "user_message":
      return appendTranscript(s, { kind: "user", text: m.text });

    case "turn_start":
      return { ...s, turnInFlight: true };

    case "turn_complete":
      return { ...s, turnInFlight: false };

    case "tool_start":
      return {
        ...s,
        activity: { tool_call_id: m.tool_call_id, name: m.name, status: "" },
      };

    case "tool_status":
      if (s.activity && s.activity.tool_call_id === m.tool_call_id) {
        return { ...s, activity: { ...s.activity, status: m.status } };
      }
      return s;

    case "tool_end":
      if (s.activity && s.activity.tool_call_id === m.tool_call_id) {
        return { ...s, activity: null };
      }
      return s;

    case "confirm_request":
      return {
        ...s,
        confirm: {
          request_id: m.request_id,
          kind: m.kind,
          payload: m.payload,
        },
      };

    case "confirmation_response":
      // Server replay of a prior decision (e.g., loading history). Currently no-op for live UI.
      return s;

    case "conv_selected":
      return { ...s, conv_id: m.conv_id, model: m.model };

    case "conv_history": {
      const items: TranscriptItem[] = [];
      for (const raw of m.messages) {
        const role = (raw as { role?: string }).role;
        const text = extractText(raw as Record<string, unknown>);
        if (role === "user") items.push({ kind: "user", text });
        else if (role === "assistant") items.push({ kind: "assistant", text });
        // skip tool/system roles in the spike
      }
      // Preserves draft/activity/confirm/turnInFlight from prior state — history
      // is expected to arrive on connect, before any turn is in flight.
      return { ...s, transcript: [...items, ...s.transcript] };
    }

    case "compaction_done":
      return appendTranscript(s, { kind: "system", text: "[compaction complete]" });

    case "model_changed":
      return appendTranscript(
        { ...s, model: m.model },
        { kind: "system", text: `[model: ${m.model}]` }
      );

    case "error":
      return appendTranscript(s, { kind: "system", text: `[error: ${m.message}]` });

    // Spike-deferred. Acknowledged in types.ts but not surfaced in UI.
    case "background_event":
    case "canvas_update":
    case "command_ack":
    case "models_available":
    case "notification_created":
    case "notification_read":
    case "reflection_result":
    case "vault_changed":
      return s;

    default: {
      // Forward-compat: surface unknown types as unchanged. The assertNever
      // call below is a compile-time exhaustiveness check; at runtime we
      // silently no-op so new wire types from the server don't crash the TUI.
      assertNever(m);
      return s;
    }
  }
}
