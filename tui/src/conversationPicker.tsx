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

interface Props {
  host: string;
  token: string;
  onPick: (convId: string) => void;
}

function newConvId(): string {
  // Random short ID; the bot creates the conversation lazily on first send.
  return "tui-" + Math.random().toString(36).slice(2, 10);
}

export function ConversationPicker({
  host,
  token,
  onPick,
}: Props): React.JSX.Element {
  const [convs, setConvs] = useState<ConvSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    fetch(host + "/api/conversations", {
      headers: { Cookie: `decafclaw_session=${token}` },
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = (await r.json()) as ConversationsResponse;
        // Endpoint returns {folder, folders, conversations}; extract the array.
        setConvs(data.conversations.slice(0, 20));
      })
      .catch((e: Error) => setErr(e.message));
  }, [host, token]);

  useInput((input, key) => {
    if (!convs) return;
    const max = convs.length; // index `max` = "new conversation" option
    if (key.upArrow) setCursor((c) => Math.max(0, c - 1));
    else if (key.downArrow) setCursor((c) => Math.min(max, c + 1));
    else if (key.return) {
      if (cursor === max) onPick(newConvId());
      else onPick(convs[cursor]!.conv_id);
    } else if (input === "n" || input === "N") {
      onPick(newConvId());
    }
  });

  if (err) return <Text color="red">Failed to list conversations: {err}</Text>;
  if (!convs) return <Text>Loading conversations…</Text>;

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
