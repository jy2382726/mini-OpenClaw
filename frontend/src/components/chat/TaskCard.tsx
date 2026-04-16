"use client";

import { Target, CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import type { TaskState, TaskStep } from "@/lib/store";

/** 步骤状态图标 */
function StepIcon({ status }: { status: TaskStep["status"] }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="w-3 h-3" style={{ color: "#2e7d32" }} />;
    case "in_progress":
      return <Loader2 className="w-3 h-3 animate-spin" style={{ color: "#002fa7" }} />;
    case "blocked":
      return <XCircle className="w-3 h-3" style={{ color: "#d32f2f" }} />;
    default:
      return <Circle className="w-3 h-3" style={{ color: "#999" }} />;
  }
}

/** 完成计数 */
function CompletionBadge({ steps }: { steps: TaskStep[] }) {
  const done = steps.filter((s) => s.status === "completed").length;
  const total = steps.length;
  return (
    <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-[#002fa7]/10 text-[#002fa7]">
      {done}/{total}
    </span>
  );
}

interface Props {
  taskState: TaskState;
}

export default function TaskCard({ taskState }: Props) {
  const { goal, steps, artifacts } = taskState;
  const completedCount = steps.filter((s) => s.status === "completed").length;
  const allDone = steps.length > 0 && completedCount === steps.length;

  return (
    <div className="animate-fade-in-scale rounded-xl border border-black/[0.04] bg-white/50 overflow-hidden">
      {/* 标题行 */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-black/[0.03]">
        <Target className="w-3.5 h-3.5" style={{ color: allDone ? "#2e7d32" : "#002fa7" }} />
        <span className="text-[12px] font-medium text-gray-700 truncate flex-1">
          {goal}
        </span>
        <CompletionBadge steps={steps} />
        {allDone && (
          <CheckCircle2 className="w-3.5 h-3.5" style={{ color: "#2e7d32" }} />
        )}
      </div>

      {/* 步骤摘要行 */}
      {steps.length > 0 && (
        <div className="px-3 py-1.5 flex items-center gap-1.5 flex-wrap">
          {steps.map((step, idx) => (
            <div
              key={idx}
              className="flex items-center gap-1 text-[11px]"
              style={{
                color: step.status === "in_progress" ? "#002fa7" : "#999",
                fontWeight: step.status === "in_progress" ? 500 : 400,
                background: step.status === "in_progress" ? "#f0f4ff" : "transparent",
                padding: step.status === "in_progress" ? "1px 6px" : "0",
                borderRadius: step.status === "in_progress" ? "4px" : "0",
              }}
              title={step.description}
            >
              <StepIcon status={step.status} />
              <span className="truncate max-w-[120px]">{step.description}</span>
            </div>
          ))}
        </div>
      )}

      {/* 产物路径列表 */}
      {artifacts.length > 0 && (
        <div className="px-3 py-1.5 border-t border-black/[0.03] text-[10px] text-gray-400">
          <span className="font-medium">产物: </span>
          {artifacts.join(", ")}
        </div>
      )}
    </div>
  );
}
