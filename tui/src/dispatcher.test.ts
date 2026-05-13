import { describe, it, expect } from "vitest";
import { initialState, dispatch } from "./dispatcher.js";
import type { ServerMessage } from "./types.js";

const CONV = "conv-abc";

describe("dispatcher", () => {
  it("chunk appends to draft", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, { type: "chunk", conv_id: CONV, text: "hello " });
    const s2 = dispatch(s1, { type: "chunk", conv_id: CONV, text: "world" });
    expect(s2.draft).toBe("hello world");
  });

  it("message_complete finalizes draft into transcript", () => {
    const s0 = { ...initialState, conv_id: CONV, draft: "answer" };
    const s1 = dispatch(s0, {
      type: "message_complete",
      conv_id: CONV,
      text: "answer",
    });
    expect(s1.draft).toBe("");
    expect(s1.transcript).toEqual([{ kind: "assistant", text: "answer" }]);
  });

  it("message_complete with [cancelled] preserves streamed draft", () => {
    const s0 = { ...initialState, conv_id: CONV, draft: "partial reply..." };
    const s1 = dispatch(s0, {
      type: "message_complete",
      conv_id: CONV,
      text: "[cancelled]",
    });
    expect(s1.draft).toBe("");
    expect(s1.transcript).toEqual([
      { kind: "assistant", text: "partial reply..." },
      { kind: "system", text: "[cancelled]" },
    ]);
  });

  it("turn_start / turn_complete toggle turnInFlight", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, { type: "turn_start", conv_id: CONV });
    expect(s1.turnInFlight).toBe(true);
    const s2 = dispatch(s1, { type: "turn_complete", conv_id: CONV });
    expect(s2.turnInFlight).toBe(false);
  });

  it("tool_start sets activity; tool_status updates it; tool_end clears it", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, {
      type: "tool_start",
      conv_id: CONV,
      tool: "run_shell_command",
      tool_call_id: "tc1",
    });
    expect(s1.activity).toEqual({
      tool_call_id: "tc1",
      name: "run_shell_command",
      status: "",
    });
    const s2 = dispatch(s1, {
      type: "tool_status",
      conv_id: CONV,
      tool: "run_shell_command",
      tool_call_id: "tc1",
      message: "running",
    });
    expect(s2.activity?.status).toBe("running");
    const s3 = dispatch(s2, {
      type: "tool_end",
      conv_id: CONV,
      tool: "run_shell_command",
      tool_call_id: "tc1",
      result_text: "done",
    });
    expect(s3.activity).toBeNull();
  });

  it("confirm_request sets confirm", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, {
      type: "confirm_request",
      conv_id: CONV,
      confirmation_id: "req1",
      action_type: "run_shell_command",
      tool: "shell",
      command: "ls",
      suggested_pattern: "",
      message: "Run ls",
      approve_label: "",
      deny_label: "",
      tool_call_id: "",
      action_data: {},
    });
    expect(s1.confirm).toEqual({
      confirmation_id: "req1",
      action_type: "run_shell_command",
      command: "ls",
      message: "Run ls",
      suggested_pattern: "",
    });
  });

  it("user_message echo appends to transcript", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, {
      type: "user_message",
      conv_id: CONV,
      text: "hi",
    });
    expect(s1.transcript).toEqual([{ kind: "user", text: "hi" }]);
  });

  it("conv_selected stores model", () => {
    const s0 = { ...initialState };
    const s1 = dispatch(s0, {
      type: "conv_selected",
      conv_id: CONV,
      model: "claude-opus-4-7",
    });
    expect(s1.conv_id).toBe(CONV);
    expect(s1.model).toBe("claude-opus-4-7");
  });

  it("compaction_done appends system line", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, { type: "compaction_done", conv_id: CONV });
    expect(s1.transcript.at(-1)).toEqual({
      kind: "system",
      text: "[compaction complete]",
    });
  });

  it("model_changed appends system line + updates model", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const s1 = dispatch(s0, {
      type: "model_changed",
      conv_id: CONV,
      model: "claude-haiku-4-5",
    });
    expect(s1.model).toBe("claude-haiku-4-5");
    expect(s1.transcript.at(-1)?.text).toContain("claude-haiku-4-5");
  });

  it("error appends system line", () => {
    const s0 = { ...initialState };
    const s1 = dispatch(s0, {
      type: "error",
      message: "boom",
      conv_id: null,
    });
    expect(s1.transcript.at(-1)).toEqual({
      kind: "system",
      text: "[error: boom]",
    });
  });

  it("unknown type returns state unchanged (forward-compat)", () => {
    const s0 = { ...initialState, conv_id: CONV };
    const unknown = { type: "future_message", conv_id: CONV } as unknown as ServerMessage;
    const s1 = dispatch(s0, unknown);
    expect(s1).toBe(s0); // referential equality — no copy
  });
});
