/** Live views refresh while visible; task-level events use a smaller interval. */

export const BOARD_REFRESH_MS = 15_000;
export const COLLABORATION_REFRESH_MS = 5_000;
export const DATA_REFRESH_EVENT = "retinue:data-refresh";

export function requestDataRefresh(): void {
  window.dispatchEvent(new Event(DATA_REFRESH_EVENT));
}

/** Poll without overlapping requests. Hidden views stop scheduling; returning
 * to the page refreshes immediately. The caller owns result/error rendering.
 * A manual refresh during a request is coalesced into one subsequent read. */
export function startVisiblePolling(
  refresh: () => void | Promise<void>,
  interval = BOARD_REFRESH_MS,
): () => void {
  let stopped = false;
  let running = false;
  let rerun = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const clearTimer = () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
  const run = () => {
    clearTimer();
    if (stopped || document.visibilityState === "hidden") return;
    if (running) {
      rerun = true;
      return;
    }
    running = true;
    void Promise.resolve().then(refresh).catch(() => undefined).finally(() => {
      running = false;
      if (stopped || document.visibilityState === "hidden") return;
      if (rerun) {
        rerun = false;
        run();
      } else {
        timer = setTimeout(run, interval);
      }
    });
  };
  const onVisibility = () => {
    if (document.visibilityState === "hidden") clearTimer();
    else run();
  };
  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("focus", run);
  window.addEventListener(DATA_REFRESH_EVENT, run);
  run();
  return () => {
    stopped = true;
    clearTimer();
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("focus", run);
    window.removeEventListener(DATA_REFRESH_EVENT, run);
  };
}
