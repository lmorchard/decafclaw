/**
 * Pure argv/env parser for decafclaw-tui. Lives outside entry.tsx so it can
 * be unit-tested without booting the React render.
 */

export interface Args {
  token: string;
  host: string;
  conv: string | null;
}

export type ParseResult =
  | { kind: "ok"; args: Args }
  | { kind: "help"; message: string }
  | { kind: "error"; message: string };

const USAGE =
  "Usage: decafclaw-tui [--token <t>] [--host <url>] [--conv <id>]\n" +
  "Env: DECAFCLAW_TOKEN, DECAFCLAW_HOST";

export function parseArgs(
  argv: string[],
  env: Record<string, string | undefined>,
): ParseResult {
  const partial: Partial<Args> = {};

  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];

    if (a === "--help" || a === "-h") {
      return { kind: "help", message: USAGE };
    }

    if (a === "--token" || a === "--host" || a === "--conv") {
      if (i + 1 >= argv.length) {
        return { kind: "error", message: `Missing value for ${a}` };
      }
      const value = argv[++i];
      if (a === "--token") partial.token = value;
      else if (a === "--host") partial.host = value;
      else partial.conv = value;
    }
  }

  const token = partial.token ?? env.DECAFCLAW_TOKEN;
  const host = partial.host ?? env.DECAFCLAW_HOST ?? "http://localhost:8088";

  if (!token) {
    return {
      kind: "error",
      message: "Missing token. Use --token <t> or DECAFCLAW_TOKEN.",
    };
  }

  return { kind: "ok", args: { token, host, conv: partial.conv ?? null } };
}
