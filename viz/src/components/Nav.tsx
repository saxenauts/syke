"use client";

import { motion } from "framer-motion";
import Link from "next/link";

interface NavProps {
  mode?: "light" | "dark";
}

const productSections = [
  { label: "Platforms", href: "#platforms" },
  { label: "Features", href: "#features" },
  { label: "Architecture", href: "#architecture" },
];

const researchSections = [
  { label: "Process", href: "#process" },
  { label: "Learning", href: "#learning" },
];

export default function Nav({ mode = "light" }: NavProps) {
  const isDark = mode === "dark";
  const sections = isDark ? researchSections : productSections;

  return (
    <motion.nav
      initial={{ y: -20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.5 }}
      className={`fixed top-0 left-0 right-0 z-50 border-b backdrop-blur-xl ${
        isDark
          ? "border-border/50 bg-background/80"
          : "border-border bg-background/90"
      }`}
    >
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
        <Link href="/" className="flex items-center gap-2">
          <div className={`h-6 w-6 rounded-md flex items-center justify-center ${
            isDark ? "bg-[#a78bfa]" : "bg-accent"
          }`}>
            <span className="text-xs font-bold text-white">S</span>
          </div>
          <span className="font-semibold tracking-tight">syke</span>
        </Link>

        <div className="hidden sm:flex items-center gap-6">
          {sections.map((s) => (
            <a
              key={s.label}
              href={s.href}
              className="text-xs font-mono text-muted hover:text-foreground transition-colors"
            >
              {s.label}
            </a>
          ))}

          {isDark ? (
            <Link
              href="/"
              className="text-xs font-mono text-muted hover:text-foreground transition-colors"
            >
              Product
            </Link>
          ) : (
            <Link
              href="/research"
              className="text-xs font-mono text-muted hover:text-foreground transition-colors"
            >
              Technical Details
            </Link>
          )}

          <a
            href="https://syke-docs.vercel.app"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs font-mono text-muted hover:text-foreground transition-colors"
          >
            Docs
          </a>
        </div>

        <a
          href="https://github.com/saxenauts/syke"
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 text-sm text-dim hover:text-foreground transition-colors"
        >
          <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24">
            <path d="M12 0c-6.626 0-12 5.373-12 12 0 5.302 3.438 9.8 8.207 11.387.599.111.793-.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 1.008-.322 3.301 1.23.957-.266 1.983-.399 3.003-.404 1.02.005 2.047.138 3.006.404 2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 .319.192.694.801.576 4.765-1.589 8.199-6.086 8.199-11.386 0-6.627-5.373-12-12-12z" />
          </svg>
          GitHub
        </a>
      </div>
    </motion.nav>
  );
}
