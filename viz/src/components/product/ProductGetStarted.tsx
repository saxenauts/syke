"use client";
import { motion, useInView } from "framer-motion";
import { useRef, useState } from "react";
import { Github, Book } from "lucide-react";

const INSTALL_LINES = [
  { type: "cmd",     text: "uvx syke setup --yes" },
  { type: "dim",     text: "# Detects claude login automatically" },
  { type: "dim",     text: "# Ingesting: Claude Code · GitHub · Gmail · ChatGPT" },
  { type: "blank",   text: "" },
  { type: "dim",     text: "Ingesting... 847 events" },
  { type: "dim",     text: "Synthesizing..." },
  { type: "blank",   text: "" },
  { type: "success", text: "✓ Memex built. Context distributed." },
  { type: "acid",    text: "> You are now always in context." },
];

export default function ProductGetStarted() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-100px" });
  const [copied, setCopied] = useState(false);

  return (
    <section id="connect" className="py-24 px-4 flex flex-col items-center">
      <div className="max-w-xl w-full">
        <div className="mb-8 text-center space-y-2">
          <h3 className="font-serif-display text-3xl text-white">Get started.</h3>
          <p className="font-mono-term text-[10px] text-gray-500 uppercase tracking-widest">
            One command. Works with any MCP client.
          </p>
        </div>

        <motion.div
          ref={ref}
          initial={{ opacity: 0 }}
          animate={isInView ? { opacity: 1 } : {}}
          transition={{ duration: 0.15 }}
        >
          {/* Terminal window */}
          <div className="bg-[#020408] border border-white/10 rounded-lg overflow-hidden shadow-2xl">
            {/* Header dots */}
            <div className="bg-white/5 px-4 py-2.5 flex items-center gap-2 border-b border-white/5">
              <div className="w-2.5 h-2.5 rounded-full bg-red-500/50" />
              <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/50" />
              <div className="w-2.5 h-2.5 rounded-full bg-green-500/50" />
              <span className="ml-auto font-mono-term text-[10px] text-gray-600">install_syke.sh</span>
            </div>

            {/* Terminal body */}
            <div className="p-6 font-mono-term text-sm space-y-1.5">
              {INSTALL_LINES.map((line, i) => {
                if (line.type === "blank") return <div key={i} className="h-2" />;
                if (line.type === "cmd") return (
                  <div key={i} className="flex items-center justify-between">
                    <span>
                      <span className="text-gray-600">$ </span>
                      <span className="text-[var(--accent-acid)] font-medium">{line.text}</span>
                    </span>
                    <button
                      onClick={() => { navigator.clipboard.writeText(line.text); setCopied(true); setTimeout(() => setCopied(false), 2000); }}
                      className="ml-4 border border-white/10 rounded px-2 py-0.5 text-[10px] text-gray-600 hover:text-[var(--accent-acid)] hover:border-[var(--accent-acid)]/30 transition-all shrink-0"
                    >
                      {copied ? "Copied!" : "Copy"}
                    </button>
                  </div>
                );
                if (line.type === "dim")     return <div key={i} className="text-gray-600 italic">{line.text}</div>;
                if (line.type === "success") return <div key={i} className="text-green-400">{line.text}</div>;
                if (line.type === "acid")    return <div key={i} className="text-[var(--accent-acid)]">{line.text}</div>;
                return null;
              })}
            </div>
          </div>

          {/* Links */}
          <div className="mt-8 flex flex-wrap justify-center gap-6 font-mono-term text-xs text-gray-500">
            <a
              href="https://github.com/saxenauts/syke"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-[var(--accent-acid)] transition-colors flex items-center gap-2"
            >
              <Github size={12} />
              saxenauts/syke →
            </a>
            <a
              href="https://syke-docs.vercel.app"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors flex items-center gap-2 uppercase tracking-widest"
            >
              <Book size={12} />
              Read the Docs →
            </a>
            <a
              href="https://pypi.org/project/syke/"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors uppercase tracking-widest"
            >
              PyPI →
            </a>
          </div>
        </motion.div>
      </div>
    </section>
  );
}
