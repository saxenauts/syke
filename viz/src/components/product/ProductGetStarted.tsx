"use client";

import { motion, useInView } from "framer-motion";
import { useRef, useState } from "react";
import SectionHeader from "../SectionHeader";

export default function ProductGetStarted() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-100px" });
  const [copied, setCopied] = useState(false);
  const [copiedAgent, setCopiedAgent] = useState(false);

  const command = "uvx syke setup --yes";
  const agentPrompt = "Study this and set it up for me: github.com/saxenauts/syke — run uvx syke setup --yes";

  const handleCopy = () => {
    navigator.clipboard.writeText(command);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const copyAgentPrompt = () => {
    navigator.clipboard.writeText(agentPrompt);
    setCopiedAgent(true);
    setTimeout(() => setCopiedAgent(false), 2000);
  };

  return (
    <section id="get-started" className="mx-auto max-w-4xl px-6 py-32">
      <SectionHeader
        title="Get started"
        subtitle="One command to perceive. Works with Claude Code, Claude Desktop, any MCP client."
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
        className="space-y-8"
      >
        {/* Install command */}
        <div className="relative group">
          <div className="rounded-lg border border-border bg-surface-2 p-4 font-mono text-sm text-foreground/90 text-left">
            <span className="text-muted select-none">$ </span>
            {command}
          </div>
          <button
            onClick={handleCopy}
            className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md border border-border bg-surface px-2.5 py-1 text-xs text-muted hover:text-foreground transition-colors"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>

        {/* Agentic install */}
        <div className="rounded-xl border border-border bg-surface-2 p-5">
          <div className="flex items-center gap-2 mb-3">
            <div className="h-2 w-2 rounded-full bg-accent" />
            <span className="text-xs font-mono text-muted uppercase tracking-wider">
              Or tell your agent
            </span>
          </div>
          <div className="relative">
            <div className="rounded-lg border border-accent/20 bg-surface p-4 font-mono text-sm text-foreground/80 text-left leading-relaxed">
              {agentPrompt}
            </div>
            <button
              onClick={copyAgentPrompt}
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-md border border-border bg-surface px-2.5 py-1 text-xs text-muted hover:text-foreground transition-colors"
            >
              {copiedAgent ? "Copied!" : "Copy"}
            </button>
          </div>
          <p className="mt-2 text-xs text-muted">
            Paste into Claude Code, Cursor, or any agent with terminal access.
          </p>
        </div>

        {/* YouTube Video */}
        <div className="mt-8">
          <div className="relative rounded-xl overflow-hidden border border-border bg-surface-2" style={{ paddingBottom: '56.25%', height: 0 }}>
            <iframe
              src="https://www.youtube.com/embed/56oDe8uPJB4"
              title="Syke Demo"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
              className="absolute top-0 left-0 w-full h-full border-0"
            ></iframe>
          </div>
        </div>

        {/* CTA buttons */}
        <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
          <a
            href="https://syke-docs.vercel.app"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg bg-accent px-6 py-3 text-sm font-medium text-white hover:bg-accent/90 transition-colors"
          >
            → Docs
          </a>
          <a
            href="https://github.com/saxenauts/syke"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-6 py-3 text-sm font-medium text-dim hover:text-foreground hover:border-foreground/20 transition-colors"
          >
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
            </svg>
            GitHub
          </a>
          <a
            href="https://pypi.org/project/syke/"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg border border-border px-6 py-3 text-sm font-medium text-dim hover:text-foreground hover:border-foreground/20 transition-colors"
          >
            PyPI
          </a>
        </div>
      </motion.div>
    </section>
  );
}
