import type { Status } from "../types";

export const STATUS_COLOR: Record<Status, string> = {
  queued: "#5c574c",
  doing: "#186b5e",
  handoff: "#c9a227",
  blocked: "#9b3333",
  done: "#32805b",
  cancelled: "#a49d8d",
};
