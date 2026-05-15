import { describe, it, expect } from "vitest";
import { parseArgs } from "./parseArgs.js";

describe("parseArgs", () => {
  it("--help returns help result", () => {
    const r = parseArgs(["--help"], {});
    expect(r.kind).toBe("help");
    if (r.kind === "help") expect(r.message).toMatch(/Usage/);
  });

  it("-h returns help result", () => {
    const r = parseArgs(["-h"], {});
    expect(r.kind).toBe("help");
  });

  it("returns ok with all flags supplied", () => {
    const r = parseArgs(["--token", "t1", "--host", "http://x", "--conv", "c1"], {});
    expect(r.kind).toBe("ok");
    if (r.kind === "ok") {
      expect(r.args.token).toBe("t1");
      expect(r.args.host).toBe("http://x");
      expect(r.args.conv).toBe("c1");
    }
  });

  it("--token at end of argv is a missing-value error", () => {
    const r = parseArgs(["--token"], {});
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.message).toMatch(/--token/);
  });

  it("--host at end of argv is a missing-value error", () => {
    const r = parseArgs(["--host"], { DECAFCLAW_TOKEN: "t1" });
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.message).toMatch(/--host/);
  });

  it("--conv at end of argv is a missing-value error", () => {
    const r = parseArgs(["--conv"], { DECAFCLAW_TOKEN: "t1" });
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.message).toMatch(/--conv/);
  });

  it("missing token (no flag, no env) is an error", () => {
    const r = parseArgs([], {});
    expect(r.kind).toBe("error");
    if (r.kind === "error") expect(r.message).toMatch(/token/i);
  });

  it("token from env is accepted", () => {
    const r = parseArgs([], { DECAFCLAW_TOKEN: "envtoken" });
    expect(r.kind).toBe("ok");
    if (r.kind === "ok") {
      expect(r.args.token).toBe("envtoken");
      expect(r.args.host).toBe("http://localhost:8088");
      expect(r.args.conv).toBeNull();
    }
  });

  it("host from env is used when flag absent", () => {
    const r = parseArgs([], {
      DECAFCLAW_TOKEN: "t1",
      DECAFCLAW_HOST: "http://example:9000",
    });
    expect(r.kind).toBe("ok");
    if (r.kind === "ok") expect(r.args.host).toBe("http://example:9000");
  });

  it("flag value beats env value", () => {
    const r = parseArgs(["--token", "flag"], { DECAFCLAW_TOKEN: "env" });
    expect(r.kind).toBe("ok");
    if (r.kind === "ok") expect(r.args.token).toBe("flag");
  });
});
