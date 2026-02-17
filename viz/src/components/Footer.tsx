import Link from "next/link";

interface FooterProps {
  mode?: "light" | "dark";
}

export default function Footer({ mode = "light" }: FooterProps) {
  const isDark = mode === "dark";

  return (
    <footer className="border-t border-border py-16 px-6">
      <div className="mx-auto max-w-6xl">
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
          <Link href="/" className="flex items-center gap-2">
            <div className={`h-5 w-5 rounded-md flex items-center justify-center ${
              isDark ? "bg-[#a78bfa]" : "bg-accent"
            }`}>
              <span className="text-[10px] font-bold text-white">S</span>
            </div>
            <span className="text-sm font-semibold">syke</span>
          </Link>
          <div className="text-xs text-muted text-center sm:text-right">
            MIT Licensed · By Utkarsh Saxena
          </div>
        </div>
        <div className="mt-6 flex flex-wrap justify-center gap-4 text-xs text-muted">
          {isDark && (
            <>
              <span>6,500 source lines</span>
              <span className="text-border">·</span>
              <span>3,225 events</span>
              <span className="text-border">·</span>
            </>
          )}
          <span>8 MCP tools</span>
          <span className="text-border">·</span>
          <a
            href="https://syke-docs.vercel.app"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground transition-colors"
          >
            Docs
          </a>
          <span className="text-border">·</span>
          <a
            href="https://pypi.org/project/syke/"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground transition-colors"
          >
            PyPI
          </a>
          <span className="text-border">·</span>
          <a
            href="https://github.com/saxenauts/syke"
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-foreground transition-colors"
          >
            github.com/saxenauts/syke
          </a>
        </div>
      </div>
    </footer>
  );
}
