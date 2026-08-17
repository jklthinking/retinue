import type { Task } from "../types";
import { STAGES, stageIndex } from "../lib/collab";

export default function StageStepper({ task }: { task: Task }) {
  const current = stageIndex(task);
  const blocked = task.status === "blocked";
  return (
    <div className="stepper" title={STAGES[current] + (blocked ? " · 受阻" : "")}>
      {STAGES.map((label, index) => {
        const state =
          index < current ? "is-past" : index === current ? (blocked ? "is-blocked" : "is-now") : "";
        return (
          <span key={label} className="stepper__step">
            {index > 0 && <i className={`stepper__bar ${index <= current ? "is-past" : ""}`} />}
            <b className={`stepper__dot ${state}`} />
            <em>{label}</em>
          </span>
        );
      })}
    </div>
  );
}
