/**
 * Task deep links via the URL hash.
 *
 * Hash-based links (``#/task/<id>``) keep the single build mountable at "/" or
 * under a reverse-proxy prefix: no server route table changes, no history API
 * base-path juggling, and a pasted link restores the same drawer after refresh.
 */

import { useCallback, useEffect, useState } from "react";

const TASK_HASH = /^#\/task\/(task-[0-9]{8}-[0-9]{3})$/;

export function parseTaskHash(hash: string): string | null {
  const match = TASK_HASH.exec(hash);
  return match ? match[1] : null;
}

export function taskDeepLink(taskId: string): string {
  const base = `${window.location.pathname}${window.location.search}`;
  return `${window.location.origin}${base}#/task/${taskId}`;
}

function readCurrent(): string | null {
  return parseTaskHash(window.location.hash);
}

/** The task id currently addressed by the URL, plus open/close helpers that
 * keep the address bar in sync so every open drawer is a shareable link. */
export function useTaskDeepLink(): [string | null, (taskId: string | null) => void] {
  const [taskId, setTaskId] = useState<string | null>(readCurrent);

  useEffect(() => {
    const onHashChange = () => setTaskId(readCurrent());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  const setDeepLink = useCallback((next: string | null) => {
    if (next) {
      window.location.hash = `/task/${next}`;
      // hashchange fires asynchronously; set state now so the drawer opens
      // without waiting for the event loop turn.
      setTaskId(next);
    } else {
      // pushState does not fire hashchange, so update state directly.
      window.history.pushState(
        null,
        document.title,
        `${window.location.pathname}${window.location.search}`
      );
      setTaskId(null);
    }
  }, []);

  return [taskId, setDeepLink];
}
