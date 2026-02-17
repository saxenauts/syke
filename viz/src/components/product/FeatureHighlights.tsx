"use client";

import { motion, useInView } from "framer-motion";
import { useRef } from "react";
import SectionHeader from "../SectionHeader";

const features = [
  {
    title: "Privacy First",
    description: "All data stays local in SQLite on your machine. Nothing leaves unless you run perception.",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      </svg>
    ),
  },
  {
    title: "Open Source",
    description: "MIT licensed. Inspect every line. No telemetry, no tracking, no surprises.",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="16 18 22 12 16 6" />
        <polyline points="8 6 2 12 8 18" />
      </svg>
    ),
  },
  {
    title: "MCP Native",
    description: "8 tools, works with any MCP client. Claude Code, Claude Desktop, or your own agent.",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2L2 7l10 5 10-5-10-5z" />
        <path d="M2 17l10 5 10-5" />
        <path d="M2 12l10 5 10-5" />
      </svg>
    ),
  },
  {
    title: "One Perception, Every Agent",
    description: "Run Syke once. Your identity follows you to every AI tool, every conversation.",
    icon: (
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="10" />
        <path d="M12 16v-4M12 8h.01" />
      </svg>
    ),
  },
];

export default function FeatureHighlights() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });

  return (
    <section id="features" className="mx-auto max-w-5xl px-6 py-20">
      <SectionHeader
        title="Built for developers"
        subtitle="Simple, private, and extensible. Context that works the way you do."
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
        className="grid gap-6 sm:grid-cols-2"
      >
        {features.map((feature, i) => (
          <motion.div
            key={feature.title}
            initial={{ y: 20, opacity: 0 }}
            animate={isInView ? { y: 0, opacity: 1 } : {}}
            transition={{ duration: 0.4, delay: i * 0.08 }}
            className="rounded-xl border border-border bg-surface-2 p-6 shadow-sm hover:shadow-md transition-shadow"
          >
            <div className="text-dim mb-4">
              {feature.icon}
            </div>
            <h3 className="text-base font-semibold mb-2">{feature.title}</h3>
            <p className="text-sm text-dim leading-relaxed">
              {feature.description}
            </p>
          </motion.div>
        ))}
      </motion.div>
    </section>
  );
}
