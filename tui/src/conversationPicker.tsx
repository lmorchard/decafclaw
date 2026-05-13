import React, { useEffect, useState } from "react";
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
}

export function ConversationPicker({
  host,
  token,
  onPick,
}: Props): React.JSX.Element {
  const [convs, setConvs] = useState<ConvSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    fetch(host + "/api/conversations", {
      headers: { Cookie: `decafclaw_session=${token}` },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = (await r.json()) as ConversationsResponse;
        setConvs(data.conversations.slice(0, 20));
      })
      .catch((e: Error) => setErr(e.message));
  }, [host, token]);

  async function createConversation(): Promise<void> {
    setCreating(true);
    try {
      const r = await fetch(host + "/api/conversations", {
        method: "POST",
        headers: {
          Cookie: `decafclaw_session=${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ title: "tui" }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const created = (await r.json()) as CreatedConv;
      onPick(created.conv_id);
    } catch (e) {
      setErr((e as Error).message);
      setCreating(false);
    }
  }

  useInput((input, key) => {
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
      {convs.map((c, i) => (
        <Text key={c.conv_id} color={i === cursor ? "cyan" : undefined}>
          {i === cursor ? "> " : "  "}
          {c.title || c.conv_id}
        </Text>
      ))}
      <Text color={cursor === convs.length ? "cyan" : "gray"}>
        {cursor === convs.length ? "> " : "  "}[new conversation]
      </Text>
    </Box>
  );
}
