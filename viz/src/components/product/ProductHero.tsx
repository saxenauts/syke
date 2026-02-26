"use client";
import { Suspense, lazy, useState } from "react";
const LatentFluid = lazy(() => import("@/components/LatentFluid"));

// Small platform indicator pills shown in the install box header
const platforms = [
  { label: "Claude Code", color: "#ccff00" },
  { label: "Claude Desktop", color: "#a78bfa" },
  { label: "OpenCode", color: "#60a5fa" },
  { label: "Cursor", color: "#f59e0b" },
  { label: "+ more", color: "#4a5568" },
];

export default function ProductHero() {
  const [copiedAgent, setCopiedAgent] = useState(false);
  const [copiedManual, setCopiedManual] = useState(false);


  const agentPrompt = "here, set it up, dev.mysyke.com, make no mistakes";
  const manualCommand = "uvx syke setup --yes";

  const copy = (text: string, setter: (v: boolean) => void) => {
    navigator.clipboard.writeText(text);
    setter(true);
    setTimeout(() => setter(false), 2000);
  };

  return (
    <section
      id="hero"
      aria-label="Hero Section"
      className="relative min-h-screen flex flex-col items-center justify-center overflow-hidden px-4 pt-20 pb-16"
    >
      <Suspense fallback={null}>
        <LatentFluid />
      </Suspense>

      <div className="relative z-10 text-center space-y-8 max-w-5xl mx-auto mix-blend-screen">
        {/* Version badge */}
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-white/10 bg-white/5 backdrop-blur-md">
          <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-acid)] animate-pulse" aria-hidden="true" />
          <span className="font-mono-term text-xs tracking-wider text-gray-400">v0.4.3 // LIVE</span>
        </div>

        {/* Logo */}
        <h1 className="font-serif-display text-5xl sm:text-7xl md:text-[10rem] tracking-tighter text-white leading-[0.85]">
          Syke<span className="text-acid">.</span>
        </h1>

        {/* Tagline — ask + record verbs */}
        <div className="space-y-1">
          <p className="font-mono-term text-base md:text-xl text-white tracking-wide">
            Observe everything.{" "}
            <span className="text-acid">Ask anything.</span>
          </p>
          <p className="font-mono-term text-base md:text-xl text-gray-500">
            Cross-web agentic memory. Your one identity for all your AI.
          </p>
        </div>

        {/* CTAs */}
        <div className="pt-6 flex flex-col items-center gap-4 w-full max-w-lg mx-auto">
          {/* Agent prompt box */}
          <div className="w-full">
            {/* Header with platform indicators */}
            <div className="flex flex-wrap items-center gap-2 px-4 py-2 rounded-t border border-b-0 border-white/10 bg-white/[0.03]">
              <span className="font-mono-term text-[9px] text-gray-600 uppercase tracking-widest mr-1">works with</span>
              {platforms.map((p) => (
                <span key={p.label} className="flex items-center gap-1 font-mono-term text-[9px] text-gray-500">
                  <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: p.color }} />
                  {p.label}
                </span>
              ))}
            </div>
            {/* Prompt */}
            <div className="relative">
              <div className="w-full rounded-b border border-white/10 bg-[#050000]/60 backdrop-blur-sm p-4 font-mono-term text-xs md:text-sm text-gray-300 text-left leading-relaxed">
                {agentPrompt}
              </div>
              <button
                onClick={() => copy(agentPrompt, setCopiedAgent)}
                className="absolute right-2 bottom-2 rounded border border-white/10 bg-[#050A14] px-2.5 py-1 text-[10px] font-mono-term text-gray-500 hover:text-[var(--accent-acid)] hover:border-[var(--accent-acid)]/40 transition-all"
              >
                {copiedAgent ? "Copied!" : "Copy"}
              </button>
            </div>
            <p className="mt-1.5 text-[10px] font-mono-term text-gray-600 uppercase tracking-widest text-center">
              Paste into Claude Code, Cursor, or any agent with terminal access.
            </p>
          </div>

          <span className="text-gray-700 font-mono-term text-xs">or</span>

          {/* Manual command */}
          <div className="w-full relative">
            <div className="w-full rounded border border-[var(--accent-acid)]/20 bg-[#050000]/60 backdrop-blur-sm p-4 font-mono-term text-sm text-[var(--accent-acid)] text-left">
              <span className="text-gray-600">$ </span>{manualCommand}
            </div>
            <button
              onClick={() => copy(manualCommand, setCopiedManual)}
              className="absolute right-2 bottom-2 rounded border border-white/10 bg-[#050A14] px-2.5 py-1 text-[10px] font-mono-term text-gray-500 hover:text-[var(--accent-acid)] hover:border-[var(--accent-acid)]/40 transition-all"
            >
              {copiedManual ? "Copied!" : "Copy"}
            </button>
          </div>

          {/* Secondary links */}
          <div className="flex flex-wrap items-center justify-center gap-4 pt-2">
            <a
              href="https://github.com/saxenauts/syke"
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono-term text-xs text-gray-500 hover:text-[var(--accent-acid)] transition-colors underline underline-offset-4 decoration-white/20 hover:decoration-[var(--accent-acid)]"
            >
              saxenauts/syke →
            </a>
            <span className="text-gray-700">·</span>
            <a
              href="https://syke-docs.vercel.app"
              target="_blank"
              rel="noopener noreferrer"
              className="font-mono-term text-xs text-gray-500 hover:text-white transition-colors uppercase tracking-widest"
            >
              Read the Docs →
            </a>
          </div>
        </div>
      </div>

      {/* Scroll indicator */}
      <div className="absolute bottom-8 inset-x-0 flex justify-center z-10">
        <div className="text-gray-600 animate-bounce-slow" aria-hidden="true">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </div>
      </div>
    </section>
  );
}
