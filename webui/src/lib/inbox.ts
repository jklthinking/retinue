import { api } from "../api";
import type { InboxInfo } from "../types";

/** Client for the inbox endpoint: the four attention lanes in one fetch.
 *
 * The same GET also nudges the daily digest server-side (date-keyed and
 * idempotent), so polling it on the home page doubles as the digest trigger.
 */
export function fetchInbox(laneLimit = 5): Promise<InboxInfo> {
  return api.get<InboxInfo>(`/api/inbox?lane_limit=${laneLimit}`);
}
