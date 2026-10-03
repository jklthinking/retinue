import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../api";
import { DATA_REFRESH_EVENT, startVisiblePolling } from "../lib/refresh";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  localStorage.clear();
});

describe("live API reads", () => {
  it("returns the current server state even when a previous day cache exists", async () => {
    localStorage.setItem("retinue.cache.v1:/api/tasks", JSON.stringify({
      at: Date.now(), data: [{ status: "queued" }],
    }));
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify([{ status: "doing" }])));
    vi.stubGlobal("fetch", fetch);
    await expect(api.getCached("/api/tasks")).resolves.toEqual([{ status: "doing" }]);
    expect(fetch).toHaveBeenCalledWith("api/tasks", expect.objectContaining({ cache: "no-store" }));
  });

  it("never hides an expired session behind a cached private response", async () => {
    localStorage.setItem("retinue.cache.v1:/api/tasks", JSON.stringify({
      at: Date.now(), data: [{ title: "previous session" }],
    }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: "session expired",
    }), { status: 401 })));
    await expect(api.getCached("/api/tasks")).rejects.toEqual(new ApiError(401, "session expired"));
  });
});

describe("visible polling", () => {
  let visibility: "visible" | "hidden";

  beforeEach(() => {
    vi.useFakeTimers();
    visibility = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
  });

  it("pauses hidden pages and refreshes immediately when returning", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    const stop = startVisiblePolling(refresh, 5_000);
    try {
      await vi.advanceTimersByTimeAsync(0);
      expect(refresh).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(refresh).toHaveBeenCalledTimes(2);
      visibility = "hidden";
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(30_000);
      expect(refresh).toHaveBeenCalledTimes(2);
      visibility = "visible";
      document.dispatchEvent(new Event("visibilitychange"));
      await vi.advanceTimersByTimeAsync(0);
      expect(refresh).toHaveBeenCalledTimes(3);
    } finally {
      stop();
    }
    await vi.advanceTimersByTimeAsync(30_000);
    expect(refresh).toHaveBeenCalledTimes(3);
  });

  it("coalesces manual refreshes while a slow request is running", async () => {
    let release: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    const refresh = vi.fn().mockReturnValueOnce(pending).mockResolvedValue(undefined);
    const stop = startVisiblePolling(refresh, 5_000);
    try {
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(15_000);
      window.dispatchEvent(new Event(DATA_REFRESH_EVENT));
      window.dispatchEvent(new Event(DATA_REFRESH_EVENT));
      expect(refresh).toHaveBeenCalledTimes(1);
      release();
      await vi.advanceTimersByTimeAsync(0);
      expect(refresh).toHaveBeenCalledTimes(2);
    } finally {
      stop();
    }
  });

  it("does not restart after cleanup when an in-flight request completes", async () => {
    let release: () => void = () => undefined;
    const pending = new Promise<void>((resolve) => { release = resolve; });
    const refresh = vi.fn().mockReturnValue(pending);
    const stop = startVisiblePolling(refresh, 5_000);
    await vi.advanceTimersByTimeAsync(0);
    stop();
    release();
    await vi.advanceTimersByTimeAsync(30_000);
    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(0);
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
