import React, { useState } from "react";
import { Box, Text, useInput } from "ink";
import TextInput from "ink-text-input";
import type { State } from "./dispatcher.js";

interface ChatLogProps {
  state: State;
  isFocused: boolean;
  onSubmit: (text: string) => void;
}

export function ChatLog({ state, isFocused, onSubmit }: ChatLogProps): React.JSX.Element {
  const [draft, setDraft] = useState("");

  const handleSubmit = (val: string) => {
    onSubmit(val);
    setDraft("");
  };

  return (
    <Box flexDirection="column" borderStyle={isFocused ? "double" : "single"} flexGrow={1}>
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
        <TextInput 
          value={draft} 
          onChange={setDraft} 
          onSubmit={handleSubmit} 
          focus={isFocused && !state.confirm} 
        />
      </Box>
    </Box>
  );
}
