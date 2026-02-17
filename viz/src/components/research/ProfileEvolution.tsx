"use client";

import { motion, useInView, AnimatePresence } from "framer-motion";
import { useRef, useState } from "react";
import SectionHeader from "../SectionHeader";
import profiles from "@/data/profiles.json";
import { sourceColors, intensityColors } from "@/lib/colors";

const runs = [
  { key: "1" as const, label: "Run 1", subtitle: "First impression", score: null },
  { key: "5" as const, label: "Run 5", subtitle: "Peak — 94.3% accuracy", score: 94.3 },
  { key: "12" as const, label: "Run 12", subtitle: "Final synthesis", score: 88.3 },
];

type RunKey = "1" | "5" | "12";

const anchors: Record<RunKey, string> = {
  "1": profiles.evolution.run_1_anchor,
  "5": profiles.evolution.run_5_anchor,
  "12": profiles.evolution.run_12_anchor,
};

const runData = profiles.run_data as Record<
  RunKey,
  {
    thread_count: number;
    platform_count: number;
    threads: { name: string; intensity: string; platforms: string[]; is_new: boolean }[];
    key_discoveries: string[];
  }
>;

export default function ProfileEvolution() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-100px" });
  const [selectedRun, setSelectedRun] = useState<RunKey>("5");

  const data = runData[selectedRun];

  return (
    <section className="mx-auto max-w-4xl px-6 py-32">
      <SectionHeader
        title="Identity growing over time"
        subtitle="The same person, perceived with increasing depth"
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
      >
        {/* Run selector */}
        <div className="flex gap-2 justify-center mb-8">
          {runs.map((run) => {
            const rd = runData[run.key];
            const isSelected = selectedRun === run.key;
            return (
              <button
                key={run.key}
                onClick={() => setSelectedRun(run.key)}
                className="rounded-lg px-4 py-3 text-sm font-mono transition-all flex-1 max-w-[200px]"
                style={{
                  backgroundColor: isSelected ? "#a78bfa15" : "transparent",
                  color: isSelected ? "#a78bfa" : "#5a5b65",
                  border: `1px solid ${isSelected ? "#a78bfa30" : "#2a2e3d"}`,
                }}
              >
                <div className="font-semibold">{run.label}</div>
                <div className="text-[10px] mt-0.5 opacity-70">{run.subtitle}</div>
                <div className="mt-2 flex items-center justify-center gap-3 text-[10px]">
                  <span style={{ color: isSelected ? "#a78bfa" : "#5a5b65" }}>
                    {rd.thread_count} threads
                  </span>
                  <span style={{ color: isSelected ? "#60a5fa" : "#5a5b65" }}>
                    {rd.platform_count} platforms
                  </span>
                </div>
              </button>
            );
          })}
        </div>

        {/* Thread growth bar */}
        <div className="flex items-center gap-3 mb-8 px-4">
          <span className="text-[10px] font-mono text-muted w-12 text-right">threads</span>
          <div className="flex-1 flex items-end gap-1 h-6">
            {runs.map((run) => {
              const rd = runData[run.key];
              const isSelected = selectedRun === run.key;
              return (
                <div key={run.key} className="flex-1 flex flex-col items-center gap-1">
                  <div
                    className="w-full rounded-sm transition-all duration-300"
                    style={{
                      height: `${(rd.thread_count / 6) * 24}px`,
                      backgroundColor: isSelected ? "#a78bfa" : "#2a2e3d",
                    }}
                  />
                  <span className="text-[9px] font-mono" style={{ color: isSelected ? "#a78bfa" : "#5a5b65" }}>
                    {rd.thread_count}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="flex-1 flex items-end gap-1 h-6">
            {runs.map((run) => {
              const rd = runData[run.key];
              const isSelected = selectedRun === run.key;
              return (
                <div key={run.key} className="flex-1 flex flex-col items-center gap-1">
                  <div
                    className="w-full rounded-sm transition-all duration-300"
                    style={{
                      height: `${(rd.platform_count / 3) * 24}px`,
                      backgroundColor: isSelected ? "#60a5fa" : "#2a2e3d",
                    }}
                  />
                  <span className="text-[9px] font-mono" style={{ color: isSelected ? "#60a5fa" : "#5a5b65" }}>
                    {rd.platform_count}p
                  </span>
                </div>
              );
            })}
          </div>
          <span className="text-[10px] font-mono text-muted w-12">platforms</span>
        </div>

        <div className="rounded-2xl border border-claude/15 bg-surface/40 p-8">
          <div className="flex items-center justify-between mb-6">
            <div className="text-xs font-mono text-muted uppercase tracking-wider">
              Identity Profile — {runs.find((r) => r.key === selectedRun)?.label}
            </div>
            <div className="text-xs font-mono text-muted">
              {profiles.events_count.toLocaleString()} events · {profiles.model}
            </div>
          </div>

          {/* Identity Anchor */}
          <div className="mb-6">
            <div className="text-xs font-mono text-claude/50 uppercase tracking-wider mb-3">
              Identity Anchor
            </div>
            <AnimatePresence mode="wait">
              <motion.p
                key={selectedRun}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.3 }}
                className="text-foreground/90 leading-relaxed text-[15px]"
              >
                {anchors[selectedRun]}
              </motion.p>
            </AnimatePresence>
          </div>

          {/* Key discoveries this run */}
          <AnimatePresence mode="wait">
            <motion.div
              key={`discoveries-${selectedRun}`}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.25 }}
              className="mb-6"
            >
              <div className="text-xs font-mono text-claude/50 uppercase tracking-wider mb-3">
                Discovered this run
              </div>
              <div className="flex flex-wrap gap-2">
                {data.key_discoveries.map((d) => (
                  <span
                    key={d}
                    className="text-xs px-3 py-1 rounded-full border border-green-500/20 bg-green-500/[0.05] text-green-400/80"
                  >
                    {d}
                  </span>
                ))}
              </div>
            </motion.div>
          </AnimatePresence>

          {/* Active Threads — all runs, new badges */}
          <AnimatePresence mode="wait">
            <motion.div
              key={`threads-${selectedRun}`}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.25, delay: 0.05 }}
              className="mb-6"
            >
              <div className="text-xs font-mono text-claude/50 uppercase tracking-wider mb-3">
                Active Threads
              </div>
              <div className="flex flex-wrap gap-2">
                {data.threads.map((thread) => (
                  <div
                    key={thread.name}
                    className="rounded-full px-3 py-1 text-xs font-medium border flex items-center gap-1.5"
                    style={{
                      borderColor: `${intensityColors[thread.intensity]}30`,
                      color: intensityColors[thread.intensity],
                      backgroundColor: `${intensityColors[thread.intensity]}10`,
                    }}
                  >
                    {thread.is_new && (
                      <span
                        className="text-[9px] font-bold px-1 rounded"
                        style={{
                          backgroundColor: `${intensityColors[thread.intensity]}30`,
                          color: intensityColors[thread.intensity],
                        }}
                      >
                        NEW
                      </span>
                    )}
                    <span>{thread.name}</span>
                    <span className="opacity-50">
                      {thread.platforms.map((p) => (
                        <span
                          key={p}
                          className="inline-block h-1.5 w-1.5 rounded-full ml-0.5"
                          style={{ backgroundColor: sourceColors[p] }}
                        />
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            </motion.div>
          </AnimatePresence>

          {/* Voice snippet — run 5 only, it's the richest */}
          {selectedRun === "5" && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <div className="text-xs font-mono text-claude/50 uppercase tracking-wider mb-2">
                Voice Patterns
              </div>
              <p className="text-sm text-dim italic mb-3">
                &ldquo;{profiles.voice_snippet.tone}&rdquo;
              </p>
              <div className="space-y-2">
                {profiles.voice_snippet.examples.map((ex, i) => (
                  <div
                    key={i}
                    className="text-sm text-foreground/60 pl-3 border-l-2 border-claude/15"
                  >
                    &ldquo;{ex}&rdquo;
                  </div>
                ))}
              </div>
            </motion.div>
          )}

          <div className="mt-6 pt-4 border-t border-border flex items-center gap-4">
            <span className="text-xs text-muted">Perceived from</span>
            {profiles.sources.map((s) => (
              <span
                key={s}
                className="text-xs font-mono"
                style={{ color: sourceColors[s] }}
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      </motion.div>
    </section>
  );
}
