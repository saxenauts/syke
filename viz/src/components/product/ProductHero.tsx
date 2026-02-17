"use client";

import { motion } from "framer-motion";
import { useState } from "react";

export default function ProductHero() {
  const [copiedAgent, setCopiedAgent] = useState(false);
  const [copiedManual, setCopiedManual] = useState(false);
  const agentPrompt = "set it up for me, github.com/saxenauts/syke, make no mistakes";
  const manualCommand = "uvx syke setup --yes";

  const copyAgentPrompt = () => {
    navigator.clipboard.writeText(agentPrompt);
    setCopiedAgent(true);
    setTimeout(() => setCopiedAgent(false), 2000);
  };

  const copyManualCommand = () => {
    navigator.clipboard.writeText(manualCommand);
    setCopiedManual(true);
    setTimeout(() => setCopiedManual(false), 2000);
  };

  return (
    <section className="relative flex min-h-[80vh] flex-col items-center justify-center px-6 pt-14 overflow-hidden">
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
          Keeps every AI{" "}
          <span className="text-dim">in sync with you</span>
        </h1>
        <p className="mt-6 text-lg text-dim sm:text-xl max-w-2xl mx-auto font-light leading-relaxed">
          Your context is scattered across platforms. Each AI sees a slice. None see you.
        </p>
      </motion.div>

      <motion.div
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ duration: 0.8, delay: 0.5 }}
        className="mt-8 w-full max-w-2xl space-y-3"
      >
        {/* Agent prompt box */}
        <div>
          <div className="relative">
            <div className="rounded-lg border border-accent/20 bg-surface p-4 font-mono text-sm text-foreground/80 text-left leading-relaxed">
              {agentPrompt}
            </div>
            <button
              onClick={copyAgentPrompt}
              className="absolute right-2 bottom-2 rounded-md border border-border bg-surface p-1.5 text-muted hover:text-foreground transition-colors"
              title={copiedAgent ? "Copied!" : "Copy to clipboard"}
            >
              {copiedAgent ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
              )}
            </button>
          </div>
          <p className="mt-2 text-xs text-muted text-center">
            Paste into Claude Code, Cursor, or any agent with terminal access.
          </p>
        </div>

        {/* "or" separator */}
        <p className="text-center text-sm text-muted">or</p>

        {/* Manual command box */}
        <div>
          <div className="relative">
            <div className="rounded-lg border border-accent/20 bg-surface p-4 font-mono text-sm text-foreground/80 text-left leading-relaxed">
              {manualCommand}
            </div>
            <button
              onClick={copyManualCommand}
              className="absolute right-2 bottom-2 rounded-md border border-border bg-surface p-1.5 text-muted hover:text-foreground transition-colors"
              title={copiedManual ? "Copied!" : "Copy to clipboard"}
            >
              {copiedManual ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                </svg>
              )}
            </button>
          </div>
          <p className="mt-2 text-xs text-muted text-center">
            Run manually in your terminal.
          </p>
        </div>
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
