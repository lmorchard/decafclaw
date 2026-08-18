import React from "react";
import { describe, it, expect } from "vitest";
import { render } from "ink-testing-library";
import { App } from "./App.js";

const delay = (ms: number) => new Promise(res => setTimeout(res, ms));

class MockWSClient {
  on = () => {};
  send = () => {};
  close = () => {};
}

describe("App Layout", () => {
  it("renders a persistent two-pane layout with a Sidebar and a ChatLog", async () => {
    const client = new MockWSClient() as any;
    const { lastFrame } = render(<App client={client} initialConvId="conv1" host="http://localhost" token="t" />);
    await delay(10);
    const frame = lastFrame();
    
    expect(frame).toContain("Sidebar");
    expect(frame).toContain("(connecting...)"); 
  });

  it("switches focus between Chat Input and Sidebar on Tab", async () => {
    const client = new MockWSClient() as any;
    const { lastFrame, stdin } = render(<App client={client} initialConvId="conv1" host="http://localhost" token="t" />);
    
    await delay(10);
    expect(lastFrame()).toContain("Sidebar (focused)");
    
    // Press Tab
    stdin.write("\t");
    
    await delay(10);
    
    expect(lastFrame()).not.toContain("Sidebar (focused)");
    expect(lastFrame()).toContain("Sidebar"); // Still present, just not focused
  });
});
