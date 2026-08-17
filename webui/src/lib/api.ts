// fetchJSON shim so the internal pages (extracted from the hermes panel)
// run unmodified against the Retinue server's cookie-session auth.

import { ApiError } from "@/api";

export interface FetchJSONOptions {
  timeoutMs?: number;
}

export async function fetchJSON<T>(
  url: string,
  init?: RequestInit,
  options?: FetchJSONOptions
): Promise<T> {
  const controller = options?.timeoutMs && !init?.signal ? new AbortController() : null;
  const timer = controller
    ? window.setTimeout(() => controller.abort(), options?.timeoutMs)
    : null;
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  try {
    const response = await fetch(url.replace(/^\//, ""), {
      ...init,
      headers,
      signal: init?.signal ?? controller?.signal ?? null,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const body = await response.json();
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        /* keep statusText */
      }
      throw new ApiError(response.status, detail);
    }
    return (await response.json()) as T;
  } finally {
    if (timer !== null) window.clearTimeout(timer);
  }
}
