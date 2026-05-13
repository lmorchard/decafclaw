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

interface Args {
  token: string;
  host: string;
  conv: string | null;
}

function parseArgs(argv: string[]): Args | { error: string } {
  const args: Partial<Args> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--token") args.token = argv[++i];
    else if (a === "--host") args.host = argv[++i];
    else if (a === "--conv") args.conv = argv[++i];
    else if (a === "--help" || a === "-h") {
      return {
        error:
          "Usage: decafclaw-tui [--token <t>] [--host <url>] [--conv <id>]\n" +
          "Env: DECAFCLAW_TOKEN, DECAFCLAW_HOST",
      };
    }
  }
  const token = args.token ?? process.env.DECAFCLAW_TOKEN;
  const host = args.host ?? process.env.DECAFCLAW_HOST ?? "http://localhost:8088";
  if (!token) {
    return { error: "Missing token. Use --token <t> or DECAFCLAW_TOKEN." };
  }
  return { token, host, conv: args.conv ?? null };
}

function main(): void {
  if (!process.stdin.isTTY) {
    console.error("decafclaw-tui requires a TTY stdin.");
    process.exit(1);
  }

  const parsed = parseArgs(process.argv.slice(2));
  if ("error" in parsed) {
    console.error(parsed.error);
    process.exit(1);
  }

  const client = new WSClient({ host: parsed.host, token: parsed.token });
  client.connect();

  render(
    <App client={client} initialConvId={parsed.conv} host={parsed.host} token={parsed.token} />,
    { exitOnCtrlC: false }
  );
}

main();
