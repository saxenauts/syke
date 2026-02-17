"use client";

import { motion, useInView, AnimatePresence } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import SectionHeader from "../SectionHeader";
import CoverageMeter from "./CoverageMeter";
import replayData from "@/data/perception-replay.json";
import { sourceColors } from "@/lib/colors";

type Step = (typeof replayData.steps)[number];

function ToolCallIcon({ tool }: { tool: string }) {
  const iconMap: Record<string, string> = {
    browse_timeline: ">>",
    search_footprint: "??",
    read_event: "[]",
    cross_reference: "<>",
    write_section: "=>",
    check_coverage: "ok",
  };
  return (
    <span className="font-mono text-[10px] opacity-70">
      {iconMap[tool] || ".."}
    </span>
  );
}

function StepEntry({ step, isNew }: { step: Step; isNew: boolean }) {
  const isThinking = step.type === "thinking";
  const isHook = step.type === "hook";
  const isToolCall = step.type === "tool_call";
  const isResult = step.type === "tool_result";

  const argsStr = step.args_display ?? "";
  const sourceMatch = argsStr.match(/source=(\S+)/);
  const sourceColor = sourceMatch ? sourceColors[sourceMatch[1]] : undefined;

  return (
    <motion.div
      initial={isNew ? { opacity: 0, y: 12 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className={`flex gap-3 py-2 px-3 rounded-lg ${
        isThinking ? "bg-claude/[0.04]" : isHook ? "bg-success/[0.06]" : ""
      }`}
    >
      <div className="text-[10px] font-mono text-muted w-8 pt-0.5 shrink-0">
        {step.time_s}s
      </div>

      <div className="flex-1 min-w-0">
        {isThinking && (
          <p className="text-sm text-dim italic leading-relaxed">
            {step.thinking_text}
          </p>
        )}

        {isToolCall && (
          <div className="flex items-center gap-2">
            <span
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono"
              style={{
                backgroundColor: sourceColor ? `${sourceColor}15` : '#242836',
                color: sourceColor || '#e4e4e7',
              }}
            >
              <ToolCallIcon tool={step.tool_name!} />
              {step.tool_name}
            </span>
            <span className="text-xs text-muted font-mono truncate">
              {step.args_display}
            </span>
          </div>
        )}

        {isResult && (
          <div>
            <div className="text-xs text-dim">
              {step.result_display}
            </div>
            {step.topics_discovered && step.topics_discovered.length > 0 && (
              <div className="flex gap-1.5 mt-1 flex-wrap">
                {step.topics_discovered.map((t) => (
                  <span
                    key={t}
                    className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-accent/10 text-accent"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {isHook && (
          <div className="flex items-center gap-2">
            <span className="text-xs font-mono text-success">
              {step.result_display}
            </span>
          </div>
        )}
      </div>
    </motion.div>
  );
}

export default function PerceptionReplay() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-100px" });
  const [currentStep, setCurrentStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const steps = replayData.steps;
  const visibleSteps = steps.slice(0, currentStep + 1);
  const currentCoverage = visibleSteps[visibleSteps.length - 1]?.coverage ?? {
    sources: {},
    pct: 0,
  };

  useEffect(() => {
    if (isInView && currentStep === 0 && !isPlaying) {
      const timeout = setTimeout(() => setIsPlaying(true), 500);
      return () => clearTimeout(timeout);
    }
  }, [isInView, currentStep, isPlaying]);

  useEffect(() => {
    if (!isPlaying) return;
    intervalRef.current = setInterval(() => {
      setCurrentStep((prev) => {
        if (prev >= steps.length - 1) {
          setIsPlaying(false);
          return prev;
        }
        return prev + 1;
      });
    }, 800);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isPlaying, steps.length]);

  const feedRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [currentStep]);

  return (
    <section className="mx-auto max-w-6xl px-6 py-32">
      <SectionHeader
        act="The Process"
        title="Watch the agent think"
        subtitle={`${replayData.duration_s}s of Opus 4.6 exploring a digital footprint with 6 MCP tools`}
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
        className="grid gap-6 lg:grid-cols-[1fr_280px]"
      >
        <div className="rounded-xl border border-border bg-surface/30 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <div className="flex items-center gap-2">
              <div className={`h-2 w-2 rounded-full ${isPlaying ? "bg-success animate-pulse" : "bg-muted"}`} />
              <span className="text-xs font-mono text-dim">
                Agent Activity
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => {
                  if (currentStep >= steps.length - 1) {
                    setCurrentStep(0);
                    setIsPlaying(true);
                  } else {
                    setIsPlaying(!isPlaying);
                  }
                }}
                className="text-xs font-mono text-muted hover:text-foreground transition-colors px-2 py-1 rounded border border-border"
              >
                {currentStep >= steps.length - 1 ? "replay" : isPlaying ? "pause" : "play"}
              </button>
              <span className="text-[10px] font-mono text-muted">
                {currentStep + 1}/{steps.length}
              </span>
            </div>
          </div>

          <div
            ref={feedRef}
            className="h-[420px] overflow-y-auto p-2 space-y-1"
          >
            <AnimatePresence>
              {visibleSteps.map((step, i) => (
                <StepEntry
                  key={i}
                  step={step}
                  isNew={i === currentStep}
                />
              ))}
            </AnimatePresence>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-surface/30 p-5 h-fit lg:sticky lg:top-20">
          <CoverageMeter
            sources={currentCoverage.sources as Record<string, boolean>}
            pct={currentCoverage.pct}
          />

          <div className="mt-6 pt-4 border-t border-border space-y-3">
            <div className="flex justify-between text-xs">
              <span className="text-muted">Tool calls</span>
              <span className="font-mono text-foreground">
                {visibleSteps.filter((s) => s.type === "tool_call").length}
              </span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-muted">Topics found</span>
              <span className="font-mono text-foreground">
                {[
                  ...new Set(
                    visibleSteps.flatMap((s) => s.topics_discovered ?? [])
                  ),
                ].length}
              </span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-muted">Connections</span>
              <span className="font-mono text-foreground">
                {visibleSteps.reduce(
                  (acc, s) => acc + (s.connections?.length ?? 0),
                  0
                )}
              </span>
            </div>
          </div>

          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex justify-between text-xs mb-2">
              <span className="text-muted">Elapsed</span>
              <span className="font-mono text-foreground">
                {visibleSteps[visibleSteps.length - 1]?.time_s ?? 0}s / {replayData.duration_s}s
              </span>
            </div>
            <div className="h-1 rounded-full bg-surface-2 overflow-hidden">
              <motion.div
                className="h-full rounded-full bg-claude/60"
                animate={{
                  width: `${
                    ((visibleSteps[visibleSteps.length - 1]?.time_s ?? 0) /
                      replayData.duration_s) *
                    100
                  }%`,
                }}
                transition={{ duration: 0.3 }}
              />
            </div>
          </div>
        </div>
      </motion.div>
    </section>
  );
}
