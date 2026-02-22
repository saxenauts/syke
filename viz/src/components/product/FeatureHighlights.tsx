"use client";

import { motion, useInView } from "framer-motion";
import { useRef } from "react";

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
    description: "3 tools — ask, read, record. Works with any MCP client. Claude Code, Cursor, or your own agent.",
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
      <div className="text-center mb-12">
        <h2 className="font-serif-display text-3xl font-normal tracking-tight sm:text-4xl lg:text-5xl text-white">
          Built for{" "}
          <span className="text-[var(--accent-acid)]">the AI-native</span>
        </h2>
        <p className="mt-4 text-gray-400 max-w-2xl mx-auto text-base md:text-lg font-mono-term font-light leading-relaxed">
          Simple, private, and extensible. Context that works the way you do.
        </p>
      </div>

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
            className="rounded-xl border border-white/8 bg-[#0B1221] p-6 hover:border-[var(--accent-acid)]/20 transition-colors"
          >
            <div className="text-gray-600 mb-4">
              {feature.icon}
            </div>
            <h3 className="font-mono-term text-sm font-medium mb-2 text-white">{feature.title}</h3>
            <p className="font-mono-term text-xs text-gray-500 leading-relaxed">
              {feature.description}
            </p>
          </motion.div>
        ))}
      </motion.div>
    </section>
  );
}
