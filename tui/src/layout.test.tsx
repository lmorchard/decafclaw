import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "ink-testing-library";
import { App } from "./App.js";

class MockWSClient {
  on = () => {};
  send = () => {};
  close = () => {};
}

describe("App Layout", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the Sidebar initially", async () => {
    const client = new MockWSClient() as any;
    const { lastFrame } = render(<App client={client} initialConvId="conv1" host="http://localhost" token="t" />);
    await vi.advanceTimersByTimeAsync(10);
    
    const frame = lastFrame();
    expect(frame).toContain("Sidebar");
  });

  it("switches focus to ChatLog on Tab", async () => {
    const client = new MockWSClient() as any;
    const { lastFrame, stdin } = render(<App client={client} initialConvId="conv1" host="http://localhost" token="t" />);
    
    await vi.advanceTimersByTimeAsync(10);
    expect(lastFrame()).toContain("Sidebar (focused)");
    
    // Press Tab
    stdin.write("\t");
    await vi.advanceTimersByTimeAsync(10);
    
    expect(lastFrame()).not.toContain("Sidebar");
    expect(lastFrame()).toContain("(connecting...)"); 
  });
});
