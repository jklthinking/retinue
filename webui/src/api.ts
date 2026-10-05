import { demoMode, demoToday } from "./demo";
import { t } from "./i18n";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function readErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return t("会话已过期，请重新登录后重试。");
  }
  if (error instanceof Error && error.message) return error.message;
  return t("无法连接服务器，请稍后重试。");
}


interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

let demoRouteIndex: Record<string, string> | null = null;

async function loadDemoRouteIndex(): Promise<Record<string, string>> {
  if (demoRouteIndex) return demoRouteIndex;
  const response = await fetch("api/_index.json");
  if (!response.ok) {
    throw new ApiError(response.status, t("无法加载演示数据索引"));
  }
  demoRouteIndex = (await response.json()) as Record<string, string>;
  return demoRouteIndex;
}

function normalizeDemoPath(path: string): string {
  const withSlash = path.startsWith("/") ? path : `/${path}`;
  if (withSlash.startsWith("/api/summary")) {
    const today = demoToday();
    if (today) return `/api/summary?today=${today}`;
    return "/api/summary";
  }
  return withSlash;
}

async function demoRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (options.method && options.method !== "GET") {
    throw new ApiError(403, t("公开演示为只读，无法修改数据。"));
  }
  const index = await loadDemoRouteIndex();
  const key = normalizeDemoPath(path);
  const file = index[key];
  if (!file) {
    throw new ApiError(404, t("演示数据未收录: {key}", { key }));
  }
  const response = await fetch(`api/${file}`);
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText);
  }
  return response.json() as Promise<T>;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  if (demoMode) {
    return demoRequest<T>(path, options);
  }
  // Relative URLs keep the app working both at "/" and under a proxied prefix.
  const response = await fetch(path.replace(/^\//, ""), {
    cache: "no-store",
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail) detail = JSON.stringify(body.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  /** Compatibility name for existing callers. Live board reads always await
   * the server: a cached response must not hide new events, expired sessions,
   * or a failed refresh. Views may retain their last payload with an error. */
  getCached: <T>(path: string, _bypass = false): Promise<T> => request<T>(path),
  getAllPages: async <T>(path: string, pageSize = 100): Promise<T[]> => {
    const items: T[] = [];
    let cursor: string | null = null;
    do {
      const separator = path.includes("?") ? "&" : "?";
      // Both annotations are load-bearing: cursor is assigned from page, which
      // depends on cursorQuery, which reads cursor. Without them TypeScript
      // reports the loop as a circular inference (TS7022).
      const cursorQuery: string = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
      const page: CursorPage<T> = await request<CursorPage<T>>(
        `${path}${separator}page_size=${pageSize}${cursorQuery}`
      );
      items.push(...page.items);
      cursor = page.has_more ? page.next_cursor : null;
      if (page.has_more && !cursor) {
        throw new ApiError(500, t("分页响应缺少 next_cursor"));
      }
    } while (cursor);
    return items;
  },
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
};
