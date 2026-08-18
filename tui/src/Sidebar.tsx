import React, { useEffect, useRef, useState } from "react";
import { Box, Text, useInput } from "ink";
import { formatRelative } from "./conversationPicker.js";

interface ConvSummary {
  conv_id: string;
  title?: string;
  updated_at?: string;
}

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
  onExit: () => void;
  isFocused: boolean;
  pickedConv: string | null;
}

export function Sidebar({
  host,
  token,
  onPick,
  onExit,
  isFocused,
  pickedConv,
}: Props): React.JSX.Element {
  const [convs, setConvs] = useState<ConvSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [creating, setCreating] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    abortRef.current = controller;
    setErr(null);
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
      setCreating(false);
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
    if (!isFocused) return;
    
    // Allow aborting
    if (key.ctrl && input === "c") {
      abort();
      return;
    }
    if (key.escape || input === "q" || input === "Q") {
      abort();
      return;
    }
    if (!convs || creating) return;
    const max = convs.length; 
    if (key.upArrow) setCursor((c) => Math.max(0, c - 1));
    else if (key.downArrow) setCursor((c) => Math.min(max, c + 1));
    else if (key.return) {
      if (cursor === 0) void createConversation();
      else onPick(convs[cursor - 1]!.conv_id);
    } else if (input === "n" || input === "N") {
      void createConversation();
    }
  }, { isActive: isFocused });

  const content = () => {
    if (err) return <Text color="red">Failed to list: {err}</Text>;
    if (!convs) return <Text>Loading…</Text>;
    if (creating) return <Text>Creating…</Text>;

    return (
      <Box flexDirection="column">
        <Text color={cursor === 0 ? "cyan" : "gray"}>
          {cursor === 0 ? "> " : "  "}[new]
        </Text>
        {convs.map((c, i) => {
          const itemIndex = i + 1;
          const rel = formatRelative(c.updated_at);
          const isSelected = pickedConv === c.conv_id;
          const isHighlighted = itemIndex === cursor;
          const color = isSelected ? "green" : (isHighlighted ? "cyan" : undefined);
          return (
            <Text key={c.conv_id} color={color}>
              {isHighlighted ? "> " : "  "}
              {c.title || c.conv_id}
              {rel ? ` (${rel})` : ""}
            </Text>
          );
        })}
      </Box>
    );
  };

  return (
    <Box flexDirection="column" borderStyle={isFocused ? "double" : "single"} width={50}>
      <Text bold color={isFocused ? "cyan" : undefined}>Sidebar {isFocused ? "(focused)" : ""}</Text>
      {content()}
    </Box>
  );
}
