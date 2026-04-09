"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Brain, AlertTriangle, Clock } from "lucide-react";
import type { Mem0RetrievalResult } from "@/lib/store";

interface Props {
  memories: Mem0RetrievalResult[];
}

/** 记忆类型对应的颜色和标签 */
const TYPE_STYLES: Record<string, { color: string; bg: string; label: string }> = {
  user: { color: "text-blue-600", bg: "bg-blue-100", label: "用户偏好" },
  feedback: { color: "text-amber-600", bg: "bg-amber-100", label: "行为反馈" },
  project: { color: "text-emerald-600", bg: "bg-emerald-100", label: "项目上下文" },
  reference: { color: "text-purple-600", bg: "bg-purple-100", label: "外部引用" },
  unknown: { color: "text-gray-600", bg: "bg-gray-100", label: "记忆" },
};

/** 新鲜度对应的图标和提示 */
const FRESHNESS_STYLES: Record<string, { icon: typeof Clock; className: string }> = {
  fresh: { icon: Clock, className: "text-emerald-500" },
  recent: { icon: Clock, className: "text-yellow-500" },
  aging: { icon: AlertTriangle, className: "text-orange-500" },
  stale: { icon: AlertTriangle, className: "text-red-500" },
};

export default function Mem0Card({ memories }: Props) {
  const [expanded, setExpanded] = useState(false);

  if (memories.length === 0) return null;

  return (
    <div className="mt-1.5 mb-2 rounded-xl border border-emerald-200/60 bg-emerald-50/40 overflow-hidden animate-fade-in-scale">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] hover:bg-emerald-50/60 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-emerald-400" />
        ) : (
          <ChevronRight className="w-3 h-3 text-emerald-400" />
        )}
        <div className="w-5 h-5 rounded flex items-center justify-center bg-emerald-100">
          <Brain className="w-3 h-3 text-emerald-600" />
        </div>
        <span className="font-medium text-emerald-700">智能记忆</span>
        <span className="text-[10px] text-emerald-400 ml-1">
          {memories.length} 条
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-2.5 space-y-2 border-t border-emerald-100/60 pt-2">
          {memories.map((m, idx) => {
            const typeStyle = TYPE_STYLES[m.memory_type || "unknown"] || TYPE_STYLES.unknown;
            const freshnessStyle = FRESHNESS_STYLES[m.freshness || "fresh"] || FRESHNESS_STYLES.fresh;
            const FreshnessIcon = freshnessStyle.icon;

            return (
              <div key={idx} className="space-y-1">
                {/* 头部：类型标签 + 置信度 + 新鲜度 */}
                <div className="flex items-center gap-2 text-[10px]">
                  <span className={`px-1.5 py-0.5 rounded font-medium ${typeStyle.bg} ${typeStyle.color}`}>
                    {typeStyle.label}
                  </span>
                  {m.confidence !== undefined && (
                    <span className="text-emerald-500">置信度: {m.confidence}</span>
                  )}
                  {m.freshness && m.freshness !== "fresh" && (
                    <span className={`flex items-center gap-0.5 ${freshnessStyle.className}`}>
                      <FreshnessIcon className="w-2.5 h-2.5" />
                      {m.freshness === "recent" ? "较新" : m.freshness === "aging" ? "老化" : "过时"}
                    </span>
                  )}
                </div>

                {/* 记忆内容 */}
                <pre className="p-2 bg-white/70 rounded-lg text-[11px] text-gray-600 font-mono whitespace-pre-wrap leading-relaxed max-h-32 overflow-y-auto border border-emerald-100/40">
                  {m.text}
                </pre>

                {/* 原因和适用场景（折叠） */}
                {(m.why || m.how_to_apply) && (
                  <div className="text-[10px] text-gray-400 space-y-0.5 pl-1">
                    {m.why && <div><span className="font-medium text-gray-500">原因:</span> {m.why}</div>}
                    {m.how_to_apply && <div><span className="font-medium text-gray-500">适用:</span> {m.how_to_apply}</div>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
