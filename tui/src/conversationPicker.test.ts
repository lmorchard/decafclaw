import { describe, it, expect } from "vitest";
import { formatRelative } from "./conversationPicker.js";

const NOW = new Date("2026-05-15T16:00:00.000Z");

describe("formatRelative", () => {
  it("returns empty string for undefined", () => {
    expect(formatRelative(undefined, NOW)).toBe("");
  });

  it("returns empty string for malformed input", () => {
    expect(formatRelative("not-a-date", NOW)).toBe("");
  });

  it("formats seconds", () => {
    expect(formatRelative("2026-05-15T15:59:45.000Z", NOW)).toBe("15s ago");
  });

  it("formats minutes", () => {
    expect(formatRelative("2026-05-15T15:30:00.000Z", NOW)).toBe("30m ago");
  });

  it("formats hours", () => {
    expect(formatRelative("2026-05-15T10:00:00.000Z", NOW)).toBe("6h ago");
  });

  it("formats days", () => {
    expect(formatRelative("2026-05-13T16:00:00.000Z", NOW)).toBe("2d ago");
  });

  it("falls back to ISO date for older than 30 days", () => {
    expect(formatRelative("2026-03-01T12:00:00.000Z", NOW)).toBe("2026-03-01");
  });

  it("clamps negative diffs to 0 seconds (future timestamps)", () => {
    expect(formatRelative("2027-01-01T00:00:00.000Z", NOW)).toBe("0s ago");
  });
});
