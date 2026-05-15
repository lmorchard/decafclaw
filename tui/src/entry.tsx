#!/usr/bin/env -S npx tsx
/**
 * Entry point: parse argv + env, construct WSClient, render App.
 *
 * Required: token (--token / DECAFCLAW_TOKEN).
 * Optional: --host (default http://localhost:8088), --conv <id>.
 */

import React from "react";
import { render } from "ink";
import { WSClient } from "./wsClient.js";
import { App } from "./App.js";
import { parseArgs } from "./parseArgs.js";

function main(): void {
  const parsed = parseArgs(process.argv.slice(2), process.env);

  if (parsed.kind === "help") {
    console.log(parsed.message);
    process.exit(0);
  }
  if (parsed.kind === "error") {
    console.error(parsed.message);
    process.exit(1);
  }

  if (!process.stdin.isTTY) {
    console.error("decafclaw-tui requires a TTY stdin.");
    process.exit(1);
  }

  const { token, host, conv } = parsed.args;
  const client = new WSClient({ host, token });
  client.connect();

  render(
    <App client={client} initialConvId={conv} host={host} token={token} />,
    { exitOnCtrlC: false }
  );
}

main();
