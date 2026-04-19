"use client";

import { useState, useEffect, useRef } from "react";
import {
  ChevronDown, ChevronRight, Terminal, Code, Globe,
  FileText, Search, Loader2, CheckCircle2, ShieldAlert, Check, X,
} from "lucide-react";
import type { ToolCall } from "@/lib/store";
import { useApp } from "@/lib/store";

const TOOL_META: Record<string, { icon: React.ElementType; color: string; bg: string }> = {
  terminal:              { icon: Terminal,  color: "#6b7280", bg: "#f3f4f6" },
  python_repl:           { icon: Code,     color: "#2563eb", bg: "#eff6ff" },
  fetch_url:             { icon: Globe,    color: "#059669", bg: "#ecfdf5" },
  read_file:             { icon: FileText, color: "#d97706", bg: "#fffbeb" },
  search_knowledge_base: { icon: Search,   color: "#7c3aed", bg: "#f5f3ff" },
};

const DEFAULT_TIMEOUT = 30;

// ── 审批按钮 + 倒计时 ──────────────────────────────────

function ApprovalButtons({
  toolCallId,
  messageId,
  timeout,
}: {
  toolCallId: string;
  messageId: string;
  timeout: number;
}) {
  const { approveToolCall, rejectToolCall } = useApp();
  const [remaining, setRemaining] = useState(timeout);
  const actedRef = useRef(false);

  useEffect(() => {
    if (actedRef.current) return;
    const timer = setInterval(() => {
      setRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          if (!actedRef.current) {
            actedRef.current = true;
            rejectToolCall(messageId, toolCallId);
          }
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(timer);
  }, [messageId, toolCallId, rejectToolCall]);

  const handleApprove = () => {
    if (actedRef.current) return;
    actedRef.current = true;
    approveToolCall(messageId, toolCallId);
  };

  const handleReject = () => {
    if (actedRef.current) return;
    actedRef.current = true;
    rejectToolCall(messageId, toolCallId);
  };

  const acted = actedRef.current;

  return (
    <div className="flex items-center gap-2 pt-1.5">
      <button
        onClick={handleApprove}
        disabled={acted}
        className="inline-flex items-center gap-1 px-3 py-1 rounded-lg text-[11px] font-medium
          bg-emerald-50 text-emerald-700 hover:bg-emerald-100
          disabled:opacity-50 disabled:cursor-not-allowed transition-colors
          dark:bg-emerald-900/30 dark:text-emerald-400 dark:hover:bg-emerald-900/50"
      >
        <Check className="w-3 h-3" />
        批准
      </button>
      <button
        onClick={handleReject}
        disabled={acted}
        className="inline-flex items-center gap-1 px-3 py-1 rounded-lg text-[11px] font-medium
          bg-red-50 text-red-700 hover:bg-red-100
          disabled:opacity-50 disabled:cursor-not-allowed transition-colors
          dark:bg-red-900/30 dark:text-red-400 dark:hover:bg-red-900/50"
      >
        <X className="w-3 h-3" />
        拒绝
      </button>
      <span className="text-[11px] text-gray-400 dark:text-gray-500 ml-auto tabular-nums">
        {acted ? "处理中..." : `${remaining}s`}
      </span>
    </div>
  );
}

// ── ThoughtChain ─────────────────────────────────────

interface Props {
  toolCalls: ToolCall[];
  messageId: string;
}

export default function ThoughtChain({ toolCalls, messageId }: Props) {
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  if (toolCalls.length === 0) return null;

  return (
    <div className="mb-2 space-y-1">
      {toolCalls.map((tc, idx) => {
        const meta = TOOL_META[tc.tool] || TOOL_META.terminal;
        const Icon = meta.icon;
        const isPending = tc.status === "pending_approval";
        const isOpen = expanded[idx] ?? isPending;

        return (
          <div
            key={idx}
            className={`rounded-xl border overflow-hidden animate-fade-in-scale ${
              isPending
                ? "border-amber-300/60 bg-amber-50/40 dark:border-amber-600/40 dark:bg-amber-900/20"
                : "border-black/[0.04] bg-white/50 dark:border-white/[0.06] dark:bg-white/[0.03]"
            }`}
          >
            <button
              onClick={() => setExpanded((p) => ({ ...p, [idx]: !p[idx] }))}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] hover:bg-black/[0.02] dark:hover:bg-white/[0.02] transition-colors"
            >
              {isOpen
                ? <ChevronDown className="w-3 h-3 text-gray-400 dark:text-gray-500" />
                : <ChevronRight className="w-3 h-3 text-gray-400 dark:text-gray-500" />
              }
              <div
                className="w-5 h-5 rounded flex items-center justify-center"
                style={{ background: meta.bg }}
              >
                <Icon className="w-3 h-3" style={{ color: meta.color }} />
              </div>
              <span className="font-medium text-gray-600 dark:text-gray-300">{tc.tool}</span>
              <span className="ml-auto">
                {tc.status === "pending_approval"
                  ? <ShieldAlert className="w-3.5 h-3.5 text-amber-500" />
                  : tc.status === "running"
                    ? <Loader2 className="w-3.5 h-3.5 text-amber-500 animate-spin" />
                    : <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                }
              </span>
            </button>
            {isOpen && (
              <div className="px-3 pb-2 text-[11px] space-y-1.5 border-t border-black/[0.03] dark:border-white/[0.06] pt-1.5">
                {tc.input && (
                  <div>
                    <span className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">Input</span>
                    <pre className="mt-0.5 p-2 bg-gray-50/80 dark:bg-gray-800/50 rounded-lg overflow-x-auto whitespace-pre-wrap text-gray-600 dark:text-gray-400 font-mono leading-relaxed">
                      {tc.input}
                    </pre>
                  </div>
                )}
                {tc.output && (
                  <div>
                    <span className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">Output</span>
                    <pre className="mt-0.5 p-2 bg-gray-50/80 dark:bg-gray-800/50 rounded-lg overflow-x-auto whitespace-pre-wrap text-gray-600 dark:text-gray-400 font-mono max-h-36 overflow-y-auto leading-relaxed">
                      {tc.output}
                    </pre>
                  </div>
                )}
                {isPending && tc.toolCallId && (
                  <ApprovalButtons
                    toolCallId={tc.toolCallId}
                    messageId={messageId}
                    timeout={tc.timeoutSeconds ?? DEFAULT_TIMEOUT}
                  />
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
