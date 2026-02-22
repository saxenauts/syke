"use client";

import { motion, useInView } from "framer-motion";
import { useRef } from "react";
import SectionHeader from "../SectionHeader";

const pipeline = [
  {
    title: "Ingest",
    description: "Reads your digital footprint from multiple platforms",
    details: "Claude Code, Claude Desktop, GitHub, Gmail, chat exports",
    badge: "4 sources",
  },
  {
    title: "Perceive",
    description: "Opus 4.6 explores your footprint with 3 MCP tools",
    details: "Agent SDK + extended thinking, coverage-gated quality",
    badge: "Agent SDK",
  },
  {
    title: "Distribute",
    description: "Your identity goes wherever your AI agents are",
    details: "MCP server, CLAUDE.md injection, JSON/Markdown export",
    badge: "3 MCP tools",
  },
];

const mcpTools = [
  { name: "get_live_context()", desc: "Synthesized identity profile", primary: true },
  { name: "ask(question)", desc: "Agentic natural language queries", primary: true },
  { name: "record(observation)", desc: "Push observations from any client", primary: true },
];

const cliExample = `$ uvx syke setup --yes

Detecting sources...
  Claude Code: 47 sessions found
  GitHub: <username> (142 repos)
  Gmail: authorized via gog

Ingesting... 2,847 events
Perceiving... Opus 4.6 + Agent SDK
  Coverage: 100% (3/3 sources)

Done. Profile written to ~/.syke/`;

export default function ProductArchitecture() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-100px" });

  return (
    <section id="architecture" className="mx-auto max-w-5xl px-6 py-20">
      <SectionHeader
        title="How it fits together"
        subtitle="Three steps from scattered conversations to unified identity"
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
      >
        {/* Pipeline */}
        <div className="grid gap-4 lg:grid-cols-3 mb-12">
          {pipeline.map((step, i) => (
            <motion.div
              key={step.title}
              initial={{ y: 20, opacity: 0 }}
              animate={isInView ? { y: 0, opacity: 1 } : {}}
              transition={{ duration: 0.5, delay: i * 0.1 }}
              className="relative rounded-xl border border-white/8 bg-[#0B1221] p-5"
            >
              {i < pipeline.length - 1 && (
                <div className="hidden lg:flex absolute -right-3 top-1/2 -translate-y-1/2 z-10 text-gray-700">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                    <path d="M5 12H19M19 12L13 6M19 12L13 18" />
                  </svg>
                </div>
              )}

              <div className="font-mono-term text-[10px] text-gray-600 uppercase tracking-widest mb-1">
                Step {i + 1}
              </div>
              <h3 className="font-serif-display text-lg font-normal mb-2 text-white">{step.title}</h3>
              <p className="font-mono-term text-sm text-gray-400 leading-relaxed mb-2">
                {step.description}
              </p>
              <p className="font-mono-term text-xs text-gray-600 leading-relaxed">
                {step.details}
              </p>
              <div className="mt-3 inline-block rounded-full border border-[var(--accent-acid)]/20 bg-[var(--accent-acid)]/5 px-2.5 py-0.5 text-[10px] font-mono text-[var(--accent-acid)]">
                {step.badge}
              </div>
            </motion.div>
          ))}
        </div>

        {/* MCP Tools */}
        <div className="rounded-xl border border-white/8 bg-[#0B1221] p-6 mb-8">
          <div className="font-mono-term text-[10px] text-gray-600 uppercase tracking-widest mb-4">
            MCP Server: 3 tools
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {mcpTools.map((tool) => (
              <div
                key={tool.name}
                className="flex items-center gap-3 rounded-lg px-3 py-2"
                style={{
                  backgroundColor: tool.primary ? 'rgba(204,255,0,0.08)' : 'transparent',
                }}
              >
                <code className="text-xs font-mono text-[var(--accent-acid)] shrink-0">
                  {tool.name}
                </code>
                <span className="text-[11px] text-gray-500 truncate">
                  {tool.desc}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* CLI Example */}
        <div className="rounded-xl border border-white/8 bg-[#0B1221] p-6 overflow-x-auto">
          <div className="font-mono-term text-[10px] text-gray-600 uppercase tracking-widest mb-3">
            Example
          </div>
          <pre className="font-mono text-sm text-gray-300 whitespace-pre leading-relaxed">
            {cliExample}
          </pre>
        </div>
      </motion.div>
    </section>
  );
}
