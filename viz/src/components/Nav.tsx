"use client";
import Link from "next/link";
import { Github, BookOpen } from "lucide-react";

export default function Nav() {
  const scrollToConnect = () => {
    document.getElementById("connect")?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <nav
      className="fixed top-0 left-0 right-0 z-50 px-6 py-5 md:px-12 backdrop-blur-sm"
      role="navigation"
      aria-label="Main navigation"
    >
      <div className="max-w-7xl mx-auto flex items-center justify-between">
        <Link href="/" className="font-serif-display text-2xl tracking-tighter text-white" aria-label="Syke Home">
          Syke<span className="text-acid">.</span>
        </Link>

        <div className="flex items-center gap-8 font-mono-term text-[10px] tracking-widest text-gray-500 uppercase">
          <div className="hidden md:flex items-center gap-6">
            <a
              href="https://syke-docs.vercel.app"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors flex items-center gap-2"
            >
              <BookOpen size={14} aria-hidden="true" />
              <span>Docs</span>
            </a>
            <a
              href="https://github.com/saxenauts/syke"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-white transition-colors flex items-center gap-2"
            >
              <Github size={14} aria-hidden="true" />
              <span>Source</span>
            </a>
          </div>
          <button
            onClick={scrollToConnect}
            className="px-4 py-2 border border-gray-800 rounded-full hover:border-[var(--accent-acid)] hover:text-[var(--accent-acid)] transition-all"
            aria-label="Get started"
          >
            Connect
          </button>
        </div>
      </div>
    </nav>
  );
}
