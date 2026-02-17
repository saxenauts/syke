"use client";

import { motion } from "framer-motion";
import Link from "next/link";

const stats = [
  { value: "3,225", label: "events" },
  { value: "4", label: "sources" },
  { value: "94.3%", label: "accuracy" },
  { value: "$0.60", label: "per run" },
];

export default function ResearchHero() {
  return (
    <section className="relative px-6 pt-28 pb-20 overflow-hidden">
      <div className="absolute inset-0 -z-10">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_#a78bfa08_0%,_transparent_70%)]" />
      </div>

      <motion.div
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.6 }}
        className="mx-auto max-w-4xl"
      >
        <Link
          href="/"
          className="inline-flex items-center gap-1 text-xs font-mono text-muted hover:text-foreground transition-colors mb-8"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
          Back to product
        </Link>

        <h1 className="text-4xl font-light tracking-tight sm:text-5xl">
          Technical Details
        </h1>
        <p className="mt-4 text-lg text-dim font-light max-w-2xl">
          Live data from a real Syke deployment — 4 sources perceived by Opus 4.6 with the Agent SDK.
          Anonymized. The agent explores your footprint interactively, not via prompt dump.
        </p>

        <div className="mt-8 flex flex-wrap gap-3">
          {stats.map((stat) => (
            <div
              key={stat.label}
              className="flex items-center gap-2 rounded-full border border-border px-4 py-2 text-sm bg-surface/50"
            >
              <span className="font-mono font-semibold text-claude">
                {stat.value}
              </span>
              <span className="text-dim">{stat.label}</span>
            </div>
          ))}
        </div>
      </motion.div>
    </section>
  );
}
