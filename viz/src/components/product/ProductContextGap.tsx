"use client";

import { motion, useInView } from "framer-motion";
import { useRef } from "react";
import SectionHeader from "../SectionHeader";

const coldResponse = `I'd be happy to help you with your project! Could you tell me more about what you're working on?

What programming language are you using? What's the goal of the project? Any specific requirements or constraints I should know about?`;

const sykeResponse = [
  { text: "Based on your ", highlight: false },
  { text: "recent coding sessions", highlight: true, label: "from coding sessions" },
  { text: ": you're building a CLI tool in Python with Click.\n\nYour ", highlight: false },
  { text: "architecture discussions", highlight: true, label: "from conversations" },
  { text: " show you're considering an agent-based approach with MCP integration, and your ", highlight: false },
  { text: "commit history", highlight: true, label: "from commit history" },
  { text: " confirms you've been iterating on the perception pipeline.\n\nHere's what I'd suggest for the next step...", highlight: false },
];

export default function ProductContextGap() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-100px" });

  return (
    <section className="mx-auto max-w-6xl px-6 py-20">
      <SectionHeader
        title="The difference context makes"
        subtitle="Same model. Same question. The only difference is Syke."
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
        className="grid gap-6 md:grid-cols-2"
      >
        {/* Cold response */}
        <div className="relative rounded-xl border border-border bg-surface p-6 opacity-60">
          <div className="mb-4 flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-muted" />
            <span className="text-xs font-mono uppercase tracking-wider text-muted">
              Cold start
            </span>
          </div>
          <div className="font-mono text-sm text-dim whitespace-pre-wrap leading-relaxed">
            {coldResponse}
          </div>
          <div className="absolute inset-0 rounded-xl bg-gradient-to-t from-background/40 to-transparent pointer-events-none" />
        </div>

        {/* Syke-enhanced response */}
        <div className="relative rounded-xl border border-accent/20 bg-accent/[0.02] p-6">
          <div className="mb-4 flex items-center gap-2">
            <div className="h-2 w-2 rounded-full bg-accent animate-pulse" />
            <span className="text-xs font-mono uppercase tracking-wider text-accent">
              Syke-enhanced
            </span>
          </div>
          <div className="font-mono text-sm text-foreground/90 whitespace-pre-wrap leading-relaxed">
            {sykeResponse.map((frag, i) =>
              frag.highlight ? (
                <span
                  key={i}
                  className="text-accent"
                  style={{ borderBottom: "1px dashed var(--color-accent-dim)" }}
                >
                  {frag.text}
                </span>
              ) : (
                <span key={i}>{frag.text}</span>
              )
            )}
          </div>
        </div>
      </motion.div>

      {/* Source legend */}
      <div className="mt-8 flex justify-center gap-6">
        {["coding sessions", "conversations", "commit history"].map((label) => (
          <div key={label} className="flex items-center gap-2 text-xs">
            <div className="h-2 w-2 rounded-full bg-accent" />
            <span className="text-muted font-mono">{label}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
