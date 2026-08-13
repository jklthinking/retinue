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

interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
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
