"use client";

import { motion, useInView, AnimatePresence } from "framer-motion";
import { useRef, useState } from "react";
import {
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceArea,
  ReferenceDot,
  ResponsiveContainer,
  Bar,
  ComposedChart,
} from "recharts";
import SectionHeader from "../SectionHeader";
import metaRun from "@/data/meta-run.json";
import strategies from "@/data/strategies.json";

const versionAvgScores = metaRun.avg_score_by_version;

const chartData = metaRun.per_run.map((r) => ({
  run: r.run,
  score: r.score,
  cost: r.cost,
  useful: r.useful,
  strategy: r.strategy_v,
  versionAvg: versionAvgScores[r.strategy_v],
}));

const strategyBands = [
  { x1: 0.5, x2: 3.5, fill: "#8b8b9410", label: "v1: Concept Search", color: "#8b8b94" },
  { x1: 3.5, x2: 6.5, fill: "#34d39910", label: "v2: Topic Expansion", color: "#34d399" },
  { x1: 6.5, x2: 9.5, fill: "#60a5fa10", label: "v3: Entity Discovery", color: "#60a5fa" },
  { x1: 9.5, x2: 12.5, fill: "#f472b610", label: "v4: Refined Ranking", color: "#f472b6" },
];

interface ChartTooltipProps {
  active?: boolean;
  payload?: Array<{ payload: (typeof chartData)[0] }>;
}

function ChartTooltip({ active, payload }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface p-3 text-xs shadow-xl">
      <div className="font-semibold text-foreground">Run {d.run}</div>
      <div className="text-claude font-mono mt-1">{d.score}% accuracy</div>
      <div className="text-blue-400 font-mono">{d.useful} useful searches</div>
      <div className="text-dim font-mono">${d.cost.toFixed(2)} cost</div>
    </div>
  );
}

const versionColors = ["#8b8b94", "#34d399", "#60a5fa", "#f472b6"];

export default function ALMALearning() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-100px" });
  const [selectedVersion, setSelectedVersion] = useState(3);

  const currentStrategy = strategies.versions[selectedVersion];

  return (
    <section className="mx-auto max-w-6xl px-6 py-32">
      <SectionHeader
        act="Learning"
        title="The search strategy gets smarter"
        subtitle="12 runs, 4 strategy evolutions — ALMA's relevance climbs from rank 4 to rank 1. The v3 dip is exploration cost: new entity searches tested, pruned, replaced."
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
        className="grid gap-8 lg:grid-cols-[1fr_320px]"
      >
        <div>
          <div className="flex flex-wrap gap-4 mb-6">
            {strategyBands.map((band) => (
              <div key={band.label} className="flex items-center gap-2 text-xs">
                <div
                  className="h-2 w-2 rounded-sm"
                  style={{ backgroundColor: band.color }}
                />
                <span className="text-muted">{band.label}</span>
              </div>
            ))}
          </div>

          <div className="h-72 rounded-xl border border-border bg-surface/30 p-4">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 10, right: 20, bottom: 10, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2e3d" />
                {strategyBands.map((band) => (
                  <ReferenceArea
                    key={band.label}
                    x1={band.x1}
                    x2={band.x2}
                    fill={band.fill}
                    strokeOpacity={0}
                  />
                ))}
                <XAxis
                  dataKey="run"
                  tick={{ fill: "#8b8b94", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "#2a2e3d" }}
                />
                <YAxis
                  domain={[75, 100]}
                  tick={{ fill: "#8b8b94", fontSize: 11 }}
                  tickLine={false}
                  axisLine={{ stroke: "#2a2e3d" }}
                  tickFormatter={(v) => `${v}%`}
                />
                <Tooltip content={<ChartTooltip />} />
                <Bar dataKey="useful" fill="#60a5fa20" radius={[2, 2, 0, 0]} yAxisId="right" />
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  domain={[0, 10]}
                  tick={{ fill: "#8b8b94", fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => `${v}`}
                />
                <Line
                  type="stepAfter"
                  dataKey="versionAvg"
                  stroke="#a78bfa"
                  strokeWidth={1}
                  strokeDasharray="5 3"
                  strokeOpacity={0.35}
                  dot={false}
                  activeDot={false}
                  legendType="none"
                />
                <Line
                  type="monotone"
                  dataKey="score"
                  stroke="#a78bfa"
                  strokeWidth={2}
                  dot={{ fill: "#a78bfa", r: 3, strokeWidth: 0 }}
                  activeDot={{ r: 5, fill: "#a78bfa" }}
                />
                <ReferenceDot
                  x={5}
                  y={94.3}
                  r={7}
                  fill="#a78bfa"
                  stroke="#a78bfa33"
                  strokeWidth={10}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4 flex flex-wrap justify-center gap-3">
            <span className="inline-flex items-center gap-2 rounded-full border border-green-500/20 bg-green-500/[0.05] px-4 py-1.5 text-xs">
              <span className="font-mono font-bold text-green-400">67% cheaper</span>
              <span className="text-dim">per run vs cold-start</span>
            </span>
            <span className="inline-flex items-center gap-2 rounded-full border border-blue-400/20 bg-blue-400/[0.05] px-4 py-1.5 text-xs">
              <span className="font-mono font-bold text-blue-400">0 LLM calls</span>
              <span className="text-dim">for learning</span>
            </span>
            <span className="inline-flex items-center gap-2 rounded-full border border-purple-400/20 bg-purple-400/[0.05] px-4 py-1.5 text-xs">
              <span className="font-mono font-bold text-purple-400">3 runs</span>
              <span className="text-dim">to first strategy</span>
            </span>
          </div>
          <div className="mt-3 text-center">
            <span className="inline-flex items-center gap-2 rounded-full border border-claude/20 bg-claude/[0.05] px-4 py-1.5 text-sm">
              <span className="font-mono font-bold text-claude">94.3%</span>
              <span className="text-dim">
                peak at Run 5 — $0.60, cheaper than the $1.80 baseline
              </span>
            </span>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-surface/30 p-5">
          <div className="text-xs font-mono text-muted uppercase tracking-wider mb-4">
            Search Strategy
          </div>

          <div className="flex gap-1 mb-5">
            {strategies.versions.map((v, i) => (
              <button
                key={v.version}
                onClick={() => setSelectedVersion(i)}
                className="flex-1 rounded-md px-2 py-1.5 text-[11px] font-mono transition-colors flex flex-col items-center"
                style={{
                  backgroundColor: selectedVersion === i ? `${versionColors[i]}20` : 'transparent',
                  color: selectedVersion === i ? versionColors[i] : '#5a5b65',
                  border: `1px solid ${selectedVersion === i ? `${versionColors[i]}40` : '#2a2e3d'}`,
                }}
              >
                v{v.version}
                {v.dead_ends_pruned > 0 && (
                  <span className="text-[9px] opacity-60 leading-none mt-0.5">
                    {v.dead_ends_pruned} pruned
                  </span>
                )}
              </button>
            ))}
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={selectedVersion}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="space-y-2"
            >
              <div className="text-xs text-dim mb-2">
                {currentStrategy.key_insight}
              </div>
              {currentStrategy.searches.slice(0, 8).map((s, i) => {
                const isALMA = s.query === "ALMA";
                return (
                  <div key={s.query} className="flex items-center gap-2">
                    <span className="text-[10px] font-mono text-muted w-4">
                      {i + 1}
                    </span>
                    <span
                      className={`flex-1 text-xs font-mono truncate ${
                        isALMA ? "text-claude font-semibold" : "text-dim"
                      }`}
                    >
                      {s.query}
                    </span>
                    <div className="w-16 h-1 rounded-full bg-surface-2 overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${s.relevance * 100}%`,
                          backgroundColor: versionColors[selectedVersion],
                        }}
                      />
                    </div>
                    <span className="text-[10px] font-mono text-muted w-8 text-right">
                      {(s.relevance * 100).toFixed(0)}%
                    </span>
                  </div>
                );
              })}

              <div className="pt-3 mt-3 border-t border-border text-[10px] font-mono text-muted">
                {currentStrategy.searches.length} queries · ${currentStrategy.total_cost_usd.toFixed(2)} cumulative
              </div>
            </motion.div>
          </AnimatePresence>

          <div className="mt-5 pt-4 border-t border-border">
            <div className="text-[10px] font-mono text-muted uppercase tracking-wider mb-3">
              ALMA&apos;s Rank
            </div>
            <div className="flex items-center justify-between">
              {strategies.alma_journey.ranks.map((rank, i) => (
                <div key={rank.version} className="flex flex-col items-center">
                  <div
                    className="text-lg font-bold font-mono"
                    style={{ color: versionColors[i] }}
                  >
                    #{rank.rank}
                  </div>
                  <div className="text-[10px] text-muted">v{rank.version}</div>
                </div>
              ))}
            </div>
            <div className="mt-2 text-[10px] text-muted text-center">
              Relevance: 0.596 → 0.732
            </div>
            <div className="mt-1 text-[10px] text-claude/70 text-center italic">
              concept search &gt; entity search
            </div>
          </div>
        </div>
      </motion.div>

      <div className="mt-8 text-center">
        <a
          href="https://github.com/saxenauts/syke/tree/main/experiments/perception"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-claude transition-colors"
        >
          View implementation &amp; methodology
          <span aria-hidden="true">&rarr;</span>
        </a>
      </div>
    </section>
  );
}
