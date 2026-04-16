"use client";

import { useState } from "react";
import {
  ChevronUp,
  ChevronDown,
  CheckCircle2,
  Circle,
  Loader2,
  XCircle,
  Target,
  FileText,
} from "lucide-react";
import type { TaskState, TaskStep } from "@/lib/store";

/** 步骤状态图标和颜色 */
function StepStatusIcon({ status }: { status: TaskStep["status"] }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="w-3.5 h-3.5" style={{ color: "#2e7d32" }} />;
    case "in_progress":
      return <Loader2 className="w-3.5 h-3.5 animate-spin" style={{ color: "#002fa7" }} />;
    case "blocked":
      return <XCircle className="w-3.5 h-3.5" style={{ color: "#d32f2f" }} />;
    default:
      return <Circle className="w-3.5 h-3.5" style={{ color: "#999" }} />;
  }
}

interface Props {
  taskState: TaskState | null;
}

export default function TaskProgressPanel({ taskState }: Props) {
  const [expanded, setExpanded] = useState(false);

  // 无活跃 TaskState 时不渲染
  if (!taskState) return null;

  const { goal, steps, artifacts } = taskState;
  const completedCount = steps.filter((s) => s.status === "completed").length;
  const totalSteps = steps.length;
  const progressPct = totalSteps > 0 ? Math.round((completedCount / totalSteps) * 100) : 0;

  return (
    <div className="px-4 pb-0 max-w-2xl mx-auto w-full">
      <div className="rounded-t-xl border border-black/[0.06] border-b-0 bg-white/70 overflow-hidden">
        {!expanded ? (
          /* 收起态 */
          <button
            onClick={() => setExpanded(true)}
            className="w-full flex items-center gap-2.5 px-3 py-2 hover:bg-black/[0.02] transition-colors"
          >
            {/* 蓝色圆点 */}
            <span className="w-2 h-2 rounded-full bg-[#002fa7] shrink-0" />
            {/* 任务目标 */}
            <span className="text-[12px] font-medium text-gray-700 truncate flex-1 text-left">
              {goal}
            </span>
            {/* 完成计数 */}
            <span className="text-[10px] text-gray-400 whitespace-nowrap">
              {completedCount}/{totalSteps} 步骤完成
            </span>
            {/* 迷你进度条 */}
            <div className="w-16 h-1.5 bg-gray-100 rounded-full overflow-hidden shrink-0">
              <div
                className="h-full bg-[#002fa7] rounded-full transition-all duration-500"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            {/* 展开箭头 */}
            <ChevronDown className="w-3.5 h-3.5 text-gray-400 shrink-0" />
          </button>
        ) : (
          /* 展开态 */
          <div className="animate-fade-in">
            {/* 标题行 + 收起按钮 */}
            <div className="flex items-center gap-2 px-3 py-2 border-b border-black/[0.04]">
              <Target className="w-3.5 h-3.5" style={{ color: "#002fa7" }} />
              <span className="text-[12px] font-semibold text-gray-700 flex-1 truncate">
                {goal}
              </span>
              <button
                onClick={() => setExpanded(false)}
                className="p-0.5 rounded hover:bg-black/[0.04] transition-colors"
              >
                <ChevronUp className="w-3.5 h-3.5 text-gray-400" />
              </button>
            </div>

            {/* 完整进度条 */}
            <div className="px-3 pt-2 pb-1">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] text-gray-400">进度</span>
                <span className="text-[10px] font-medium text-[#002fa7]">{progressPct}%</span>
              </div>
              <div className="w-full h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div
                  className="h-full bg-[#002fa7] rounded-full transition-all duration-500"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            </div>

            {/* 步骤列表 */}
            <div className="px-3 py-1.5 space-y-1">
              {steps.map((step, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-2 py-1"
                  style={{
                    background: step.status === "in_progress" ? "#f0f4ff" : "transparent",
                    padding: step.status === "in_progress" ? "4px 8px" : "4px 8px",
                    borderRadius: 6,
                  }}
                >
                  <div className="shrink-0 mt-0.5">
                    <StepStatusIcon status={step.status} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div
                      className="text-[11px] leading-relaxed"
                      style={{
                        color: step.status === "in_progress" ? "#002fa7" : step.status === "completed" ? "#999" : "#666",
                        fontWeight: step.status === "in_progress" ? 500 : 400,
                      }}
                    >
                      {step.description}
                    </div>
                    {step.result_summary && (
                      <div className="text-[10px] text-gray-400 mt-0.5 truncate">
                        {step.result_summary}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* 产物列表 */}
            {artifacts.length > 0 && (
              <div className="px-3 py-1.5 border-t border-black/[0.03]">
                <div className="flex items-center gap-1 mb-1">
                  <FileText className="w-3 h-3 text-gray-400" />
                  <span className="text-[10px] font-medium text-gray-400">产物</span>
                </div>
                {artifacts.map((path, idx) => (
                  <div key={idx} className="text-[10px] text-gray-500 pl-4 py-0.5 font-mono truncate">
                    {path}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
