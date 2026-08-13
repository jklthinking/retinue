import { useEffect, useState } from "react";
import { api } from "../api";
import type { ActorInfo, Me } from "../types";
import TaskDrawer from "./TaskDrawer";

/** Pages reload their task data when the deep-linked drawer changes a card. */
export const TASKS_CHANGED_EVENT = "retinue:tasks-changed";

export function notifyTasksChanged() {
  window.dispatchEvent(new Event(TASKS_CHANGED_EVENT));
}

/** App-level task drawer driven by the URL hash: any card address can be
 * shared and a refresh lands on the same drawer. Fetches its own actor list
 * so it works on top of any page. */
export default function DeepTaskDrawer({
  taskId,
  me,
  onClose,
  onOpenTask,
}: {
  taskId: string;
  me: Me;
  onClose: () => void;
  onOpenTask: (taskId: string) => void;
}) {
  const [actors, setActors] = useState<ActorInfo[]>([]);

  useEffect(() => {
    void api
      .get<ActorInfo[]>("/api/actors")
      .then(setActors)
      .catch(() => setActors([]));
  }, []);

  return (
    <TaskDrawer
      taskId={taskId}
      me={me}
      actors={actors}
      onClose={onClose}
      onChanged={notifyTasksChanged}
      onOpenTask={onOpenTask}
    />
  );
}
