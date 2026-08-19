import React from "react";
import { Box, Text, useInput } from "ink";

interface ModalProps {
  confirm: {
    action_type: string;
    command?: string;
    message?: string;
    suggested_pattern?: string;
  };
  onDecision: (decision: { approved: boolean; always: boolean } | null) => void;
}

export function Modal({ confirm, onDecision }: ModalProps): React.JSX.Element {
  useInput((input, key) => {
    const decision =
      input === "y" || input === "Y" ? { approved: true, always: false } :
      input === "n" || input === "N" ? { approved: false, always: false } :
      input === "a" || input === "A" ? { approved: true, always: true } :
      null;
    if (decision) {
      onDecision(decision);
    }
  }, { isActive: true });

  return (
    <Box
      flexDirection="column"
      borderStyle="round"
      borderColor="red"
      padding={1}
      
    >
      <Text color="magenta" bold>
        confirm ({confirm.action_type}): {confirm.command || confirm.message}
      </Text>
      {confirm.suggested_pattern && (
        <Text color="magenta">suggested pattern: {confirm.suggested_pattern}</Text>
      )}
      <Text color="magenta">[y]es / [n]o / [a]lways</Text>
    </Box>
  );
}
