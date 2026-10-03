import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.useRealTimers();
  vi.resetModules();
});

describe("public demo snapshot clock", () => {
  it("uses the configured observation instant in demo mode", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "1");
    vi.stubEnv("VITE_DEMO_TODAY", "2026-08-31");
    vi.stubEnv("VITE_DEMO_NOW", "2026-08-31T16:19:23Z");
    const { panelNow } = await import("../demo");

    expect(panelNow().toISOString()).toBe("2026-08-31T16:19:23.000Z");
  });

  it("keeps the calendar-day clock for demos built without an instant", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "1");
    vi.stubEnv("VITE_DEMO_TODAY", "2026-07-08");
    vi.stubEnv("VITE_DEMO_NOW", undefined);
    const { panelNow } = await import("../demo");

    expect(panelNow().toISOString()).toBe("2026-07-08T09:42:00.000Z");
  });

  it("falls back to the calendar-day clock when the instant is invalid", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "1");
    vi.stubEnv("VITE_DEMO_TODAY", "2026-08-31");
    vi.stubEnv("VITE_DEMO_NOW", "invalid-snapshot-clock");
    const { panelNow } = await import("../demo");

    expect(panelNow().toISOString()).toBe("2026-08-31T09:42:00.000Z");
  });

  it("uses the real clock outside demo mode despite demo environment values", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-03T12:34:56Z"));
    vi.stubEnv("VITE_DEMO_MODE", "0");
    vi.stubEnv("VITE_DEMO_TODAY", "2026-08-31");
    vi.stubEnv("VITE_DEMO_NOW", "2026-08-31T16:19:23Z");
    const { panelNow } = await import("../demo");

    expect(panelNow().toISOString()).toBe("2026-10-03T12:34:56.000Z");
  });
});
