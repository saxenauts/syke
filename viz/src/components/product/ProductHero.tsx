"use client";

import { motion } from "framer-motion";
import Link from "next/link";

export default function ProductHero() {
  return (
    <section className="relative flex min-h-[90vh] flex-col items-center justify-center px-6 pt-14 overflow-hidden">
      {/* Subtle purple radial glow */}
      <div className="absolute inset-0 -z-10">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,_#6C5CE708_0%,_transparent_70%)]" />
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 h-[500px] w-[500px] rounded-full bg-accent/[0.03] blur-[120px]" />
      </div>

      <motion.div
        initial={{ y: 30, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.8, delay: 0.2 }}
        className="max-w-3xl text-center"
      >
        <h1 className="text-5xl font-light tracking-tight sm:text-6xl lg:text-7xl">
          Your AI doesn&apos;t{" "}
          <span className="text-dim">know you</span>
        </h1>
        <p className="mt-6 text-lg text-dim sm:text-xl max-w-2xl mx-auto font-light leading-relaxed">
          Every conversation starts cold. Syke synthesizes your digital footprint
          into psyche-level context — one perception, every AI agent knows you.
        </p>
      </motion.div>

      <motion.div
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.8, delay: 0.5 }}
        className="mt-12 flex flex-col sm:flex-row items-center gap-4"
      >
        <a
          href="#get-started"
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-6 py-3 text-sm font-medium text-white hover:bg-accent/90 transition-colors"
        >
          Get Started
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M5 12H19M12 5l7 7-7 7" />
          </svg>
        </a>
        <Link
          href="/research"
          className="inline-flex items-center gap-2 rounded-lg border border-border px-6 py-3 text-sm font-medium text-dim hover:text-foreground hover:border-foreground/20 transition-colors"
        >
          Technical Details
        </Link>
      </motion.div>

      {/* Scroll indicator */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.5 }}
        className="absolute bottom-10"
      >
        <motion.div
          animate={{ y: [0, 8, 0] }}
          transition={{ repeat: Infinity, duration: 2.5, ease: "easeInOut" }}
          className="h-8 w-5 rounded-full border border-muted/40 flex items-start justify-center pt-1.5"
        >
          <div className="h-1.5 w-1 rounded-full bg-muted/60" />
        </motion.div>
      </motion.div>
    </section>
  );
}
