import { Github, Twitter, Book } from "lucide-react";

export default function Footer() {
  return (
    <footer
      className="py-8 border-t border-white/10 bg-[#020509] text-gray-400 font-mono-term text-sm relative overflow-hidden"
      role="contentinfo"
    >
      <div className="absolute inset-0 bg-[var(--accent-electric)] opacity-[0.02] pointer-events-none" aria-hidden="true" />

      <div className="max-w-6xl mx-auto px-6 grid grid-cols-1 md:grid-cols-3 gap-8">
        <div className="space-y-4">
          <h4 className="text-white text-xs uppercase tracking-widest border-b border-white/10 pb-2 w-max">Project</h4>
          <p className="text-xs leading-relaxed max-w-xs text-gray-600">
            Syke is open-source agentic memory for developers. Local-first. MIT licensed.
          </p>
          <p className="text-xs text-gray-700">MIT {new Date().getFullYear()} · Utkarsh Saxena</p>
        </div>

        <nav className="space-y-4" aria-label="Network links">
          <h4 className="text-white text-xs uppercase tracking-widest border-b border-white/10 pb-2 w-max">Network</h4>
          <ul className="space-y-2 text-xs">
            <li>
              <a href="https://github.com/saxenauts/syke" target="_blank" rel="noopener noreferrer"
                className="hover:text-[var(--accent-acid)] transition-colors flex items-center gap-2">
                <Github size={12} aria-hidden="true" /> Source Code
              </a>
            </li>
            <li>
              <a href="https://twitter.com/saxenauts" target="_blank" rel="noopener noreferrer"
                className="hover:text-[var(--accent-acid)] transition-colors flex items-center gap-2">
                <Twitter size={12} aria-hidden="true" /> @saxenauts
              </a>
            </li>
            <li>
              <a href="https://pypi.org/project/syke/" target="_blank" rel="noopener noreferrer"
                className="hover:text-[var(--accent-acid)] transition-colors">
                PyPI
              </a>
            </li>
          </ul>
        </nav>

        <nav className="space-y-4" aria-label="Documentation">
          <h4 className="text-white text-xs uppercase tracking-widest border-b border-white/10 pb-2 w-max">Manual</h4>
          <ul className="space-y-2 text-xs">
            <li>
              <a href="https://syke-docs.vercel.app" target="_blank" rel="noopener noreferrer"
                className="hover:text-[var(--accent-acid)] transition-colors flex items-center gap-2">
                <Book size={12} aria-hidden="true" /> Documentation
              </a>
            </li>
            <li>
              <a href="https://syke-docs.vercel.app/changelog" target="_blank" rel="noopener noreferrer"
                className="hover:text-[var(--accent-acid)] transition-colors">
                Changelog
              </a>
            </li>
            <li>
              <a href="https://syke-docs.vercel.app/architecture" target="_blank" rel="noopener noreferrer"
                className="hover:text-[var(--accent-acid)] transition-colors">
                Architecture
              </a>
            </li>
          </ul>
        </nav>
      </div>
    </footer>
  );
}
