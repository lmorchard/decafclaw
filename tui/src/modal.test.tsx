import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "ink-testing-library";
import { App } from "./App.js";

class MockWSClient {
  handlers: Record<string, Function> = {};
  on(cb: Function) {
    this.handlers["event"] = cb;
  }
  send = vi.fn();
  close = vi.fn();

  triggerEvent(e: any) {
    if (this.handlers["event"]) {
      this.handlers["event"](e);
    }
  }
}

describe("App Modal", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("pushes a Modal overlay when confirm_request arrives and blocks input", async () => {
    const client = new MockWSClient();
    const { lastFrame, stdin } = render(<App client={client as any} initialConvId="conv1" host="http://localhost" token="t" />);
    
    await vi.advanceTimersByTimeAsync(10);
    
    // Simulate server selecting conversation
    client.triggerEvent({
      type: "conv_selected",
      conv_id: "conv1"
    });
    
    await vi.advanceTimersByTimeAsync(10);
    
    client.triggerEvent({
      type: "confirm_request",
      confirmation_id: "conf1",
      action_type: "bash",
      command: "rm -rf /",
    });

    await vi.advanceTimersByTimeAsync(10);

    const frame = lastFrame();
    expect(frame).toContain("confirm (bash): rm -rf /");
    expect(frame).toContain("[y]es / [n]o / [a]lways");

    // Input y should be intercepted by the modal
    stdin.write("y");
    
    await vi.advanceTimersByTimeAsync(10);

    expect(client.send).toHaveBeenCalledWith(expect.objectContaining({
      type: "confirm_response",
      approved: true,
    }));
  });
});
