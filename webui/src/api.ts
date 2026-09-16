import { demoMode, demoToday } from "./demo";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function readErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 401) {
    return "会话已过期，请重新登录后重试。";
  }
  if (error instanceof Error && error.message) return error.message;
  return "无法连接服务器，请稍后重试。";
}


const CACHE_PREFIX = "retinue.cache.v1:";
const CACHE_TTL_MS = 24 * 60 * 60 * 1000;

function cacheKey(path: string): string {
  return CACHE_PREFIX + path;
}

function readCache<T>(path: string): { at: number; data: T } | null {
  try {
    const raw = localStorage.getItem(cacheKey(path));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { at: number; data: T };
    if (!parsed || typeof parsed.at !== "number") return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeCache<T>(path: string, data: T): void {
  try {
    localStorage.setItem(cacheKey(path), JSON.stringify({ at: Date.now(), data }));
  } catch {
    /* quota exceeded: skip */
  }
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
    throw new ApiError(response.status, "无法加载演示数据索引");
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
    throw new ApiError(403, "公开演示为只读，无法修改数据。");
  }
  const index = await loadDemoRouteIndex();
  const key = normalizeDemoPath(path);
  const file = index[key];
  if (!file) {
    throw new ApiError(404, `演示数据未收录: ${key}`);
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
  /** Return last good payload immediately; revalidate in the background.
   *  On a cache miss, wait for the network. If the network fails and a
   *  (possibly stale) copy exists, return that instead of throwing. */
  getCached: async <T>(path: string, bypass = false): Promise<T> => {
    const hit = readCache<T>(path);
    const freshEnough = hit !== null && Date.now() - hit.at < CACHE_TTL_MS;
    if (!bypass && freshEnough && hit) {
      void request<T>(path).then((fresh) => writeCache(path, fresh)).catch(() => {});
      return hit.data;
    }
    try {
      const fresh = await request<T>(path);
      writeCache(path, fresh);
      return fresh;
    } catch (err) {
      if (hit) return hit.data;
      throw err;
    }
  },
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
        throw new ApiError(500, "分页响应缺少 next_cursor");
      }
    } while (cursor);
    return items;
  },
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
};
