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
    description: "Opus 4.6 explores your footprint with 6 MCP tools",
    details: "Agent SDK + extended thinking, coverage-gated quality",
    badge: "Agent SDK",
  },
  {
    title: "Distribute",
    description: "Your identity goes wherever your AI agents are",
    details: "MCP server, CLAUDE.md injection, JSON/Markdown export",
    badge: "8 MCP tools",
  },
];

const mcpTools = [
  { name: "ask(question)", desc: "Agentic natural language queries", primary: true },
  { name: "get_profile(format)", desc: "Identity in 4 formats" },
  { name: "query_timeline(...)", desc: "Events by date/source" },
  { name: "search_events(query)", desc: "Keyword search" },
  { name: "get_event(id)", desc: "Full event content" },
  { name: "push_event(...)", desc: "Federated push from any client", primary: true },
  { name: "push_events(json)", desc: "Batch push" },
  { name: "get_manifest()", desc: "Data statistics" },
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
              className="relative rounded-xl border border-border bg-surface p-5"
            >
              {i < pipeline.length - 1 && (
                <div className="hidden lg:flex absolute -right-3 top-1/2 -translate-y-1/2 z-10 text-muted">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                    <path d="M5 12H19M19 12L13 6M19 12L13 18" />
                  </svg>
                </div>
              )}

              <div className="text-xs font-mono text-muted uppercase tracking-wider mb-1">
                Step {i + 1}
              </div>
              <h3 className="text-lg font-medium mb-2">{step.title}</h3>
              <p className="text-sm text-dim leading-relaxed mb-2">
                {step.description}
              </p>
              <p className="text-xs text-muted leading-relaxed">
                {step.details}
              </p>
              <div className="mt-3 inline-block rounded-full border border-accent/20 bg-accent/[0.05] px-2.5 py-0.5 text-[10px] font-mono text-accent">
                {step.badge}
              </div>
            </motion.div>
          ))}
        </div>

        {/* MCP Tools */}
        <div className="rounded-xl border border-border bg-surface p-6 mb-8">
          <div className="text-xs font-mono text-muted uppercase tracking-wider mb-4">
            MCP Server: 8 tools
          </div>
          <div className="grid gap-2 sm:grid-cols-2">
            {mcpTools.map((tool) => (
              <div
                key={tool.name}
                className="flex items-center gap-3 rounded-lg px-3 py-2"
                style={{
                  backgroundColor: tool.primary ? 'var(--color-accent-dim)' : 'transparent',
                }}
              >
                <code className="text-xs font-mono text-accent shrink-0">
                  {tool.name}
                </code>
                <span className="text-[11px] text-muted truncate">
                  {tool.desc}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* CLI Example */}
        <div className="rounded-xl border border-border bg-surface-2 p-6 overflow-x-auto">
          <div className="text-xs font-mono text-muted uppercase tracking-wider mb-3">
            Example
          </div>
          <pre className="font-mono text-sm text-foreground/80 whitespace-pre leading-relaxed">
            {cliExample}
          </pre>
        </div>
      </motion.div>
    </section>
  );
}
