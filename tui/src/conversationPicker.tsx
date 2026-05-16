import React, { useEffect, useRef, useState } from "react";
import { Box, Text, useInput } from "ink";

interface ConvSummary {
  conv_id: string;
  title?: string;
  updated_at?: string;
}

// Actual API shape: GET /api/conversations returns {folder, folders, conversations}
// (not a bare array). The plan assumed a bare array; we adapt here.
interface ConversationsResponse {
  folder: string;
  folders: unknown[];
  conversations: ConvSummary[];
}

interface CreatedConv {
  conv_id: string;
}

interface Props {
  host: string;
  token: string;
  onPick: (convId: string) => void;
  // Abort path. Picker doesn't own the WSClient (entry.tsx connects before
  // mount), so it bubbles abort up to App, which closes the socket and
  // exits Ink. Without this, Esc/q/Ctrl+C would unmount the picker but
  // leave the WS + reconnect timers running.
  onExit: () => void;
}

// Format an ISO timestamp as a compact relative-time string. Returns "" when
// undefined so callers can render conditionally without a guard.
export function formatRelative(iso: string | undefined, now: Date = new Date()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const diffMs = now.getTime() - t;
  const sec = Math.max(0, Math.floor(diffMs / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  // > 30 days: fall back to the date portion of the ISO string.
  return iso.slice(0, 10);
}

export function ConversationPicker({
  host,
  token,
  onPick,
  onExit,
}: Props): React.JSX.Element {
  const [convs, setConvs] = useState<ConvSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [creating, setCreating] = useState(false);
  // Tracks the most recent in-flight fetch so the abort path can cancel it
  // before unmount. Without this, fetches keep the Node event loop alive
  // and can setState after unmount.
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    fetch(host + "/api/conversations", {
      headers: { Cookie: `decafclaw_session=${token}` },
      signal: controller.signal,
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = (await r.json()) as ConversationsResponse;
        setConvs(data.conversations.slice(0, 20));
      })
      .catch((e: Error) => {
        if (e.name === "AbortError") return;
        setErr(e.message);
      });
    return () => {
      controller.abort();
    };
  }, [host, token]);

  async function createConversation(): Promise<void> {
    setCreating(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const r = await fetch(host + "/api/conversations", {
        method: "POST",
        headers: {
          Cookie: `decafclaw_session=${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title: "tui" }),
        signal: controller.signal,
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const created = (await r.json()) as CreatedConv;
      onPick(created.conv_id);
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      setErr((e as Error).message);
      setCreating(false);
    }
  }

  function abort(): void {
    abortRef.current?.abort();
    onExit();
  }

  useInput((input, key) => {
    // Universal abort. Lives above the loading guard so the user can always
    // bail out — including during the initial fetch.
    if (key.ctrl && input === "c") {
      abort();
      return;
    }
    if (key.escape || input === "q" || input === "Q") {
      abort();
      return;
    }
    if (!convs || creating) return;
    const max = convs.length; // index `max` = "new conversation" option
    if (key.upArrow) setCursor((c) => Math.max(0, c - 1));
    else if (key.downArrow) setCursor((c) => Math.min(max, c + 1));
    else if (key.return) {
      if (cursor === max) void createConversation();
      else onPick(convs[cursor]!.conv_id);
    } else if (input === "n" || input === "N") {
      void createConversation();
    }
  });

  if (err) return <Text color="red">Failed to list conversations: {err}</Text>;
  if (!convs) return <Text>Loading conversations…</Text>;
  if (creating) return <Text>Creating new conversation…</Text>;

  return (
    <Box flexDirection="column">
      <Text bold>Pick a conversation (Up/Down + Enter, or `n` for new):</Text>
      {convs.map((c, i) => {
        const rel = formatRelative(c.updated_at);
        return (
          <Text key={c.conv_id} color={i === cursor ? "cyan" : undefined}>
            {i === cursor ? "> " : "  "}
            {c.title || c.conv_id}
            {rel ? `  (${rel})` : ""}
          </Text>
        );
      })}
      <Text color={cursor === convs.length ? "cyan" : "gray"}>
        {cursor === convs.length ? "> " : "  "}[new conversation]
      </Text>
    </Box>
  );
}
