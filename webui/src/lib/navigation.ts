import { useCallback, useEffect, useState } from "react";

export const PAGE_KEYS = [
  "home", "affairs", "agenda", "taskflow", "board", "taskcenter", "workroom",
  "sessions", "live", "collab", "overview", "ops", "agents", "skills",
  "catalog", "knowledge", "infra", "kingdom", "admin",
] as const;
export type Page = typeof PAGE_KEYS[number];
export const NAVIGATION_EVENT = "retinue:navigation";

export function readPage(search = window.location.search): Page {
  const value = new URLSearchParams(search).get("page");
  return PAGE_KEYS.includes(value as Page) ? value as Page : "home";
}

export function readFlowTask(search = window.location.search): string | null {
  const value = new URLSearchParams(search).get("task");
  return value && /^task-[0-9]{8}-[0-9]{3}$/.test(value) ? value : null;
}

export function writeNavigation(values: Record<string, string | null>, replace = false) {
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(values)) {
    if (value === null) url.searchParams.delete(key);
    else url.searchParams.set(key, value);
  }
  window.history[replace ? "replaceState" : "pushState"](null, document.title, `${url.pathname}${url.search}${url.hash}`);
  window.dispatchEvent(new Event(NAVIGATION_EVENT));
}

export function usePageNavigation(): [Page, (page: Page) => void] {
  const [page, setPage] = useState<Page>(() => readPage());
  useEffect(() => {
    const sync = () => setPage(readPage());
    window.addEventListener("popstate", sync);
    window.addEventListener(NAVIGATION_EVENT, sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(NAVIGATION_EVENT, sync);
    };
  }, []);
  const navigate = useCallback((next: Page) => {
    if (!PAGE_KEYS.includes(next)) return;
    writeNavigation({ page: next === "home" ? null : next });
  }, []);
  return [page, navigate];
}

export function navigationGroup(page: Page): Page {
  if (["taskflow", "board", "taskcenter", "workroom", "collab"].includes(page)) return "taskflow";
  if (page === "live") return "sessions";
  if (page === "ops") return "overview";
  if (page === "agenda") return "affairs";
  return page;
}
