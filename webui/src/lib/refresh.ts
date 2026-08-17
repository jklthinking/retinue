/** Board data freshness: daily automatic pull, plus a manual trigger. */

export const BOARD_REFRESH_MS = 24 * 60 * 60 * 1000;
export const DATA_REFRESH_EVENT = "retinue:data-refresh";

export function requestDataRefresh(): void {
  window.dispatchEvent(new Event(DATA_REFRESH_EVENT));
}
