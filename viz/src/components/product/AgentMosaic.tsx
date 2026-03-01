"use client";

import { motion, useInView } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import SectionHeader from "../SectionHeader";

// ── Types & Data ──────────────────────────────────────────────────────────────

interface Agent {
  id: string;
  brand: string;
  context: string;
  color: string;
  dur: number;
  delay: number;
  lines: string[];
}

const AGENTS: Agent[] = [
  {
    id: "claude",
    brand: "Claude Code",
    context: "project",
    color: "#ccff00",
    dur: 22,
    delay: 0,
    lines: [
      'read_file("middleware.ts")',
      "  → JWT validation, role checks",
      "  → 3 patterns match",
      "",
      'edit_file("routes/login.ts")',
      "  + token refresh logic",
      "  + error handling updated",
      "  ✓ 3 files changed",
      "",
      'run_tests("auth")',
      "  auth.test.ts    ✓ 23",
      "  routes.test.ts  ✓ 14",
      "  37 passed · 1.4s",
      "",
      'create_memory("auth layer done")',
      "  → linked: api-redesign",
    ],
  },
  {
    id: "cursor",
    brand: "Cursor",
    context: "research",
    color: "#f59e0b",
    dur: 26,
    delay: 0.5,
    lines: [
      'web_search("attention 2026")',
      "  [1] FlashAttention-3",
      "      Shah et al. · 2025",
      "  [2] Ring Attention",
      "      Liu et al. · 2025",
      "  [3] PagedAttention v2",
      "      Kwon et al.",
      "",
      "embed_documents(3)",
      "  recall@10: 0.94",
      "  precision@5: 0.88",
      "",
      "synthesize_notes()",
      '  "Ring attn: 10M ctx',
      '   linear memory scale..."',
      "",
      'create_memory("attn survey")',
      "  → 3 papers indexed",
    ],
  },
  {
    id: "opencode",
    brand: "OpenCode",
    context: "life",
    color: "#60a5fa",
    dur: 20,
    delay: 1.0,
    lines: [
      "sync_health_data()",
      "  steps  8,247 / 10,000",
      "  sleep  7h 42m",
      "  deep   2h 12m",
      "  water  2.1L / 3.0L",
      "",
      'read_calendar("tomorrow")',
      "  10am  standup",
      "  2pm   design review",
      "  5pm   1:1 with lead",
      "",
      "log_meals()",
      "  oats + berries  320 cal",
      "  salmon poke     480 cal",
      "  stir-fry        550 cal",
      "",
      'create_memory("week 12 health")',
      "  streak: 12 days active",
    ],
  },
  {
    id: "codex",
    brand: "Codex",
    context: "work",
    color: "#34d399",
    dur: 24,
    delay: 1.5,
    lines: [
      "fetch_inbox()",
      "  14 unread · 3 flagged",
      "",
      '→ sam: "PR review needed"',
      '  reply("LGTM, ship it.")',
      "  ✓ sent",
      "",
      '→ alex: "API integration"',
      "  draft_reply(spec.pdf)",
      "  ✓ queued",
      "",
      "summarize_threads()",
      "  3 action items extracted",
      "  2 follow-ups scheduled",
      "",
      'create_memory("inbox zero tue")',
      "  → 14 processed in 4m",
    ],
  },
];

// ── CSS keyframes (injected once, hoisted to <head> by React 19) ──────────────

const KEYFRAMES = `
  @keyframes mosaic-sweep {
    from { top: 0%; }
    to   { top: 100%; }
  }
  @keyframes mosaic-blink {
    0%   { opacity: 1; }
    50%  { opacity: 0; }
    100% { opacity: 1; }
  }
`;

// ── Constants & helpers ───────────────────────────────────────────────────────

const LINE_H = 17; // 10px font-size × 1.7 line-height

function lineColor(line: string, c: string): string {
  const t = line.trimStart();
  if (t.startsWith("create_memory")) return c;
  if (/^[a-z_]+\(/.test(t)) return `${c}cc`;
  if (t.startsWith("→ ") && !t.startsWith("  →")) return `${c}aa`;
  if (t.includes("✓")) return "rgba(74,222,128,0.75)";
  if (t.startsWith("  +") || t.startsWith("  →")) return `${c}66`;
  return "rgba(148,163,184,0.4)";
}

// ── Hook: stepped discrete scroll ────────────────────────────────────────────
// tick: 0 → N → 0 → N ... (inclusive of N for seamless reset with doubled content)
// At tick N, translate = -N*LINE_H, shows doubled_lines[N] = lines[0] = same as tick 0.
// Reset to 0 is invisible — both N and 0 show lines[0] at top.

function useTickedScroll(
  N: number,
  msPerTick: number,
  delayMs: number,
  active: boolean
): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!active) return;
    let iv: ReturnType<typeof setInterval> | undefined;
    const to = setTimeout(() => {
      iv = setInterval(
        () => setTick((t) => (t + 1 > N ? 0 : t + 1)),
        msPerTick
      );
    }, delayMs);
    return () => {
      clearTimeout(to);
      clearInterval(iv);
    };
  }, [active, N, msPerTick, delayMs]);
  return tick;
}

// ── Terminal — radio receiver tuned to a frequency ───────────────────────────

function Terminal({
  agent,
  index,
  isInView,
}: {
  agent: Agent;
  index: number;
  isInView: boolean;
}) {
  const c = agent.color;
  const N = agent.lines.length;
  const msPerTick = Math.round((agent.dur * 1000) / N);
  const tick = useTickedScroll(N, msPerTick, agent.delay * 1000 + 1200, isInView);

  return (
    // Entry: instant pop, staggered by 0.15s — metronome rhythm
    <motion.div
      initial={{ opacity: 0 }}
      animate={isInView ? { opacity: 1 } : {}}
      transition={{ duration: 0.15, delay: index * 0.15 }}
    >
      {/* Context token — LIFE LAYER */}
      <p className="font-mono-term text-[12px] text-white/90 mb-2 tracking-wide">
        {agent.context}
      </p>

      {/* Terminal body — 16:10 laptop aspect */}
      <div
        className="aspect-[16/10] overflow-hidden relative flex flex-col"
        style={{ background: "#0B1221", border: `1px solid ${c}14` }}
      >
        {/* macOS dots */}
        <div
          className="flex items-center gap-[3px] px-2 py-[5px] shrink-0"
          style={{ borderBottom: `1px solid ${c}0a`, background: `${c}04` }}
        >
          <div className="w-[5px] h-[5px] rounded-full" style={{ background: "#ef444430" }} />
          <div className="w-[5px] h-[5px] rounded-full" style={{ background: "#eab30830" }} />
          <div className="w-[5px] h-[5px] rounded-full" style={{ background: "#22c55e30" }} />
        </div>

        {/* Discrete stepped scroll — no transition, instant line jump */}
        <div className="flex-1 overflow-hidden">
          <div
            className="px-2.5 pt-2 font-mono-term text-[10px] leading-[1.7]"
            style={{ transform: `translateY(-${tick * LINE_H}px)` }}
          >
            {[...agent.lines, ...agent.lines].map((line, j) => (
              <div
                key={j}
                className="truncate"
                style={{ color: lineColor(line, c) }}
              >
                {line || "\u00A0"}
              </div>
            ))}
          </div>
        </div>

        {/* Static CRT scanlines */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              "repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.025) 2px,rgba(0,0,0,0.025) 4px)",
          }}
        />

        {/* Moving sweep — steps(20), discrete positions top→bottom */}
        <div
          className="absolute inset-x-0 h-[2px] pointer-events-none"
          style={{
            background: `linear-gradient(90deg, transparent, ${c}10, transparent)`,
            animation: `mosaic-sweep ${6 + index * 0.7}s steps(20) ${index * 1.5}s infinite`,
          }}
        />
      </div>

      {/* Brand name — AGENT IDENTITY, prominent, below terminal */}
      <p
        className="mt-2 font-mono-term text-[13px] font-semibold tracking-wide"
        style={{ color: c }}
      >
        {agent.brand}
      </p>
    </motion.div>
  );
}

// ── SykePipe — signal decoder ─────────────────────────────────────────────────

function SykePipe({ isInView }: { isInView: boolean }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={isInView ? { opacity: 1 } : {}}
      transition={{ duration: 0.15, delay: 0.6 }}
    >
      {/* Data pipes — 4 lines, discrete pulsing dots */}
      <div className="relative h-10">
        {AGENTS.map((a, i) => {
          const pct = 12.5 + i * 25;
          return (
            <div key={a.id}>
              {/* Connection line — instant draw, linear */}
              <motion.div
                className="absolute top-0 bottom-0 w-px"
                style={{
                  left: `${pct}%`,
                  background: `linear-gradient(to bottom, ${a.color}55, rgba(204,255,0,0.25))`,
                }}
                initial={{ scaleY: 0, transformOrigin: "top" }}
                animate={isInView ? { scaleY: 1 } : {}}
                transition={{ duration: 0.3, delay: 0.5 + i * 0.1, ease: "linear" }}
              />
              {/* Dot — 3 discrete positions, not smooth */}
              <motion.div
                className="absolute w-[3px] h-[3px] rounded-full"
                style={{ left: `calc(${pct}% - 1px)`, background: a.color }}
                animate={
                  isInView ? { y: [0, 13, 27, 40], opacity: [0.9, 0.65, 0.3, 0] } : {}
                }
                transition={{
                  duration: 1.2,
                  repeat: Infinity,
                  times: [0, 0.33, 0.66, 1],
                  ease: "linear",
                  delay: 0.8 + i * 0.22,
                }}
              />
            </div>
          );
        })}
      </div>

      {/* ◉ SYKE — signal decoder anchor */}
      <div className="flex items-center gap-4 mt-2">
        {/* Dashed signal traces */}
        <div className="flex-1" style={{ borderTop: "1px dashed rgba(204,255,0,0.18)" }} />
        <div className="flex items-center gap-2.5 shrink-0">
          {/* LED — hard binary blink, steps(2) */}
          <span
            className="font-mono-term text-[14px] font-bold"
            style={{
              color: "#ccff00",
              animation: "mosaic-blink 1s steps(2) infinite",
            }}
          >
            ◉
          </span>
          {/* SYKE — phosphor glow */}
          <span
            className="font-mono-term text-[14px] font-bold tracking-[0.25em]"
            style={{
              color: "#ccff00",
              textShadow:
                "0 0 4px rgba(204,255,0,0.6), 0 0 8px rgba(204,255,0,0.3)",
            }}
          >
            SYKE
          </span>
        </div>
        <div className="flex-1" style={{ borderTop: "1px dashed rgba(204,255,0,0.18)" }} />
      </div>

      <p
        className="text-center mt-3 font-mono-term text-[11px]"
        style={{ color: "rgba(148,163,184,0.35)" }}
      >
        always in context
      </p>
    </motion.div>
  );
}

// ── Export ────────────────────────────────────────────────────────────────────

export default function AgentMosaic() {
  const sectionRef = useRef<HTMLElement>(null);
  const isInView = useInView(sectionRef, { once: true, margin: "-80px" });

  return (
    <section ref={sectionRef} id="agents" className="mx-auto max-w-7xl px-6 py-16">
      {/* Keyframes — React 19 hoists <style> to <head> */}
      <style>{KEYFRAMES}</style>

      <SectionHeader
        title="You and your agents"
        subtitle="Four sessions. Three projects. One memory."
      />

      {/* 4 terminals — 2×2 mobile, 4-across desktop */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
        {AGENTS.map((agent, i) => (
          <Terminal key={agent.id} agent={agent} index={i} isInView={isInView} />
        ))}
      </div>

      <div className="mt-0">
        <SykePipe isInView={isInView} />
      </div>
    </section>
  );
}
