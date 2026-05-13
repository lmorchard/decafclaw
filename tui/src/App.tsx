import React from "react";
import { Text } from "ink";
import type { WSClient } from "./wsClient.js";

export interface AppProps {
  client: WSClient;
  initialConvId: string | null;
  host: string;
  token: string;
}

export function App(_props: AppProps): React.JSX.Element {
  return <Text>decafclaw-tui (placeholder)</Text>;
}
