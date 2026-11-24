import { ArrowRightCircle, Check, Circle } from "lucide-react";

const STATE = {
  done: { text: "Done", Icon: Check },
  current: { text: "Current", Icon: ArrowRightCircle },
  todo: { text: "To do", Icon: Circle },
};

// The improvement loop as an ordered list. Each step says its state in words and in an icon shape;
// the current step (if any) has aria-current="step".
export default function LoopStepper({ steps }) {
  if (!Array.isArray(steps) || steps.length === 0) return null;
  return (
    <ol className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
      {steps.map((step) => {
        const { text, Icon } = STATE[step.state] ?? STATE.todo;
        const current = step.state === "current";
        return (
          <li
            key={step.id}
            aria-current={current ? "step" : undefined}
            className={`rounded-md border px-3 py-2 text-sm ${current ? "border-foreground bg-muted" : "border-border bg-card"}`}
          >
            <p className="flex items-center gap-1.5 font-medium text-foreground">
              <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
              <span>{step.label}</span>
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {text}
              {step.detail ? ` · ${step.detail}` : ""}
            </p>
          </li>
        );
      })}
    </ol>
  );
}
