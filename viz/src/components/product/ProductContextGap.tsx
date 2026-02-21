"use client";

import { motion, useInView, AnimatePresence } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";
import SectionHeader from "../SectionHeader";

// ── Types ──

type Fragment = { text: string; highlight?: boolean; source?: string };

type AnimationEvent =
  | { type: "typing"; role: "user"; text: string }
  | { type: "paste"; role: "user"; text: string }
  | { type: "thinking"; role: "ai"; text: string; durationMs: number }
  | { type: "tool_call"; role: "ai"; tool: string; query: string; result: string; durationMs: number }
  | { type: "streaming"; role: "ai"; content: string | Fragment[] }
  | { type: "pause"; durationMs: number };

type PasteRange = { start: number; end: number; active: boolean };

type RenderedBlock = {
  id: number;
  role: "user" | "ai";
  blockType: "message" | "thinking" | "tool_call";
  visibleText: string;
  fullText: string;
  fragments?: Fragment[];
  visibleCharCount: number;
  isComplete: boolean;
  isTyping: boolean;
  pasteRanges: PasteRange[];
  toolName?: string;
  toolQuery?: string;
  toolResult?: string;
};

// ── Constants ──

const CHAR_DELAY = 30;
const STREAM_DELAY = 40;
const STREAM_CHARS = 6;
const PASTE_FLASH = 600;

// ── Timeline data ──

const memoryTimeline: AnimationEvent[] = [
  {
    type: "typing",
    role: "user",
    text: "Does my research from last night change our approach to the data pipeline?",
  },
  { type: "pause", durationMs: 500 },
  {
    type: "thinking",
    role: "ai",
    text: "I don\u2019t have context on last night\u2019s research. Let me check your recent activity...",
    durationMs: 1500,
  },
  {
    type: "tool_call",
    role: "ai",
    tool: "Notion",
    query: "data pipeline research notes",
    result: "No recent pages found",
    durationMs: 2500,
  },
  {
    type: "tool_call",
    role: "ai",
    tool: "Web Search",
    query: "event sourcing CQRS data pipeline",
    result: "12 generic results",
    durationMs: 2200,
  },
  { type: "pause", durationMs: 400 },
  {
    type: "thinking",
    role: "ai",
    text: "Nothing specific to your situation. Let me check team channels...",
    durationMs: 1200,
  },
  {
    type: "tool_call",
    role: "ai",
    tool: "Slack",
    query: "#data-team pipeline",
    result: "Found migration timeline thread",
    durationMs: 2800,
  },
  {
    type: "tool_call",
    role: "ai",
    tool: "Email",
    query: "data pipeline deadline",
    result: "1 thread \u2014 March deadline mentioned",
    durationMs: 2000,
  },
  {
    type: "tool_call",
    role: "ai",
    tool: "GitHub",
    query: "open PRs data-pipeline",
    result: "PR #82 \u2014 3 review comments",
    durationMs: 2500,
  },
  { type: "pause", durationMs: 500 },
  {
    type: "thinking",
    role: "ai",
    text: "Piecing together what I found across these sources...",
    durationMs: 1800,
  },
  {
    type: "thinking",
    role: "ai",
    text: "The PR review and Slack thread seem related...",
    durationMs: 1500,
  },
  {
    type: "streaming",
    role: "ai",
    content:
      "From what I can find: there\u2019s an active PR #82 with review feedback about batch inserts, a Slack thread about migration timelines, and an email mentioning a March deadline.\n\nI couldn\u2019t find your specific research from last night though. Could you share what you were looking at? That would help me connect it to what the team\u2019s been discussing.",
  },
];

const sykeResponseFragments: Fragment[] = [
  { text: "Your " },
  {
    text: "ChatGPT session on event sourcing",
    highlight: true,
    source: "AI conversations",
  },
  {
    text: " already has the answer. Snapshots every 1000 events \u2014 and the ",
  },
  {
    text: "blog post you bookmarked",
    highlight: true,
    source: "bookmarks",
  },
  { text: " on partitioned streams solves the 10k/sec ceiling. " },
  {
    text: "Sarah\u2019s PR #82 comment",
    highlight: true,
    source: "code review",
  },
  {
    text: " about batch inserts \u201cfighting the database\u201d is the symptom; event sourcing is the fix.\n\nThe ",
  },
  {
    text: "March deadline from the team email",
    highlight: true,
    source: "team email",
  },
  { text: " gives you 6 weeks. " },
  {
    text: "Slack thread",
    highlight: true,
    source: "team chat",
  },
  {
    text: " confirms the team\u2019s aligned on migration. Since you prefer bottom-up architecture:\n\n\u2610 Define event schema from current batch insert shape\n\u2610 Partition strategy (by tenant \u2014 Sarah\u2019s PR has the access patterns)\n\u2610 Snapshot interval: 1000 events (per your ChatGPT analysis)\n\u2610 Migration plan aligned with March deadline",
  },
];

const sykeTimeline: AnimationEvent[] = [
  {
    type: "typing",
    role: "user",
    text: "Does my research from last night change our approach?",
  },
  { type: "pause", durationMs: 300 },
  {
    type: "thinking",
    role: "ai",
    text: "Connecting: ChatGPT session \u2192 blog bookmark \u2192 GitHub PR #82 \u2192 Slack thread \u2192 team email \u2192 reading history",
    durationMs: 1800,
  },
  { type: "streaming", role: "ai", content: sykeResponseFragments },
];

const sourceLegend = [
  "AI conversations",
  "bookmarks",
  "code review",
  "team email",
  "team chat",
];

// ── Animation Hook ──

function useConversationAnimation(events: AnimationEvent[]) {
  const [blocks, setBlocks] = useState<RenderedBlock[]>([]);
  const [phase, setPhase] = useState<"idle" | "playing" | "done">("idle");

  const blocksRef = useRef<RenderedBlock[]>([]);
  const timersRef = useRef<Set<ReturnType<typeof setTimeout> | ReturnType<typeof setInterval>>>(new Set());
  const nextIdRef = useRef(0);
  const startedRef = useRef(false);

  const sync = useCallback(() => {
    setBlocks(
      blocksRef.current.map((b) => ({
        ...b,
        pasteRanges: b.pasteRanges.map((r) => ({ ...r })),
      }))
    );
  }, []);

  const clearAllTimers = useCallback(() => {
    timersRef.current.forEach((id) => {
      clearTimeout(id as ReturnType<typeof setTimeout>);
      clearInterval(id as ReturnType<typeof setInterval>);
    });
    timersRef.current.clear();
  }, []);

  // Mutable ref for the recursive event processor
  const processRef = useRef<(idx: number) => void>(() => {});

  processRef.current = (idx: number) => {
    if (idx >= events.length) {
      const last = blocksRef.current[blocksRef.current.length - 1];
      if (last && !last.isComplete) {
        last.isComplete = true;
        last.isTyping = false;
      }
      sync();
      setPhase("done");
      return;
    }

    const event = events[idx];
    const next = () => processRef.current(idx + 1);

    const getLastBlock = () =>
      blocksRef.current[blocksRef.current.length - 1];
    const canMerge = (role: string) => {
      const last = getLastBlock();
      return last && last.role === role && last.blockType === "message";
    };

    switch (event.type) {
      case "typing": {
        let block: RenderedBlock;
        if (canMerge(event.role)) {
          block = getLastBlock();
          block.fullText += event.text;
        } else {
          block = {
            id: nextIdRef.current++,
            role: event.role,
            blockType: "message",
            visibleText: "",
            fullText: event.text,
            isComplete: false,
            isTyping: false,
            pasteRanges: [],
            visibleCharCount: 0,
          };
          blocksRef.current.push(block);
        }

        block.isTyping = true;
        let charIdx = block.visibleText.length;
        const targetLen = block.fullText.length;

        const interval = setInterval(() => {
          if (charIdx >= targetLen) {
            clearInterval(interval);
            timersRef.current.delete(interval);
            block.isTyping = false;
            sync();
            next();
            return;
          }
          charIdx++;
          block.visibleText = block.fullText.slice(0, charIdx);
          block.visibleCharCount = charIdx;
          sync();
        }, CHAR_DELAY);
        timersRef.current.add(interval);
        sync();
        break;
      }

      case "paste": {
        let block: RenderedBlock;
        if (canMerge(event.role)) {
          block = getLastBlock();
        } else {
          block = {
            id: nextIdRef.current++,
            role: event.role,
            blockType: "message",
            visibleText: "",
            fullText: "",
            isComplete: false,
            isTyping: false,
            pasteRanges: [],
            visibleCharCount: 0,
          };
          blocksRef.current.push(block);
        }

        const start = block.fullText.length;
        block.fullText += event.text;
        block.visibleText = block.fullText;
        block.visibleCharCount = block.visibleText.length;
        const end = block.fullText.length;
        const range: PasteRange = { start, end, active: true };
        block.pasteRanges.push(range);
        sync();

        const flashTimer = setTimeout(() => {
          range.active = false;
          timersRef.current.delete(flashTimer);
          sync();
        }, PASTE_FLASH);
        timersRef.current.add(flashTimer);

        next();
        break;
      }

      case "thinking": {
        const block: RenderedBlock = {
          id: nextIdRef.current++,
          role: event.role,
          blockType: "thinking",
          visibleText: event.text,
          fullText: event.text,
          isComplete: false,
          isTyping: false,
          pasteRanges: [],
          visibleCharCount: event.text.length,
        };
        blocksRef.current.push(block);
        sync();

        const timer = setTimeout(() => {
          block.isComplete = true;
          timersRef.current.delete(timer);
          sync();
          next();
        }, event.durationMs);
        timersRef.current.add(timer);
        break;
      }

      case "tool_call": {
        const block: RenderedBlock = {
          id: nextIdRef.current++,
          role: event.role,
          blockType: "tool_call",
          visibleText: "",
          fullText: "",
          isComplete: false,
          isTyping: false,
          pasteRanges: [],
          visibleCharCount: 0,
          toolName: event.tool,
          toolQuery: event.query,
          toolResult: event.result,
        };
        blocksRef.current.push(block);
        sync();

        const timer = setTimeout(() => {
          block.isComplete = true;
          timersRef.current.delete(timer);
          sync();
          next();
        }, event.durationMs);
        timersRef.current.add(timer);
        break;
      }

      case "streaming": {
        const isFragmented = typeof event.content !== "string";
        const fragments = isFragmented
          ? (event.content as Fragment[])
          : undefined;
        const fullText = isFragmented
          ? (event.content as Fragment[]).map((f) => f.text).join("")
          : (event.content as string);

        const block: RenderedBlock = {
          id: nextIdRef.current++,
          role: "ai",
          blockType: "message",
          visibleText: "",
          fullText,
          fragments,
          isComplete: false,
          isTyping: false,
          pasteRanges: [],
          visibleCharCount: 0,
        };
        blocksRef.current.push(block);

        const totalChars = fullText.length;
        let revealed = 0;

        const interval = setInterval(() => {
          revealed = Math.min(revealed + STREAM_CHARS, totalChars);
          block.visibleCharCount = revealed;
          block.visibleText = fullText.slice(0, revealed);

          if (revealed >= totalChars) {
            clearInterval(interval);
            timersRef.current.delete(interval);
            block.isComplete = true;
            sync();
            next();
          } else {
            sync();
          }
        }, STREAM_DELAY);
        timersRef.current.add(interval);
        sync();
        break;
      }

      case "pause": {
        const timer = setTimeout(() => {
          timersRef.current.delete(timer);
          next();
        }, event.durationMs);
        timersRef.current.add(timer);
        break;
      }
    }
  };

  const start = useCallback(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    setPhase("playing");
    processRef.current(0);
  }, []);

  const reset = useCallback(() => {
    clearAllTimers();
    blocksRef.current = [];
    nextIdRef.current = 0;
    startedRef.current = false;
    setBlocks([]);
    setPhase("idle");
  }, [clearAllTimers]);

  useEffect(() => {
    return () => clearAllTimers();
  }, [clearAllTimers]);

  return { blocks, phase, start, reset };
}

// ── Render helpers ──

function renderFragments(fragments: Fragment[], visibleCharCount: number) {
  let remaining = visibleCharCount;
  return fragments.map((frag, i) => {
    if (remaining <= 0) return null;
    const visibleLen = Math.min(remaining, frag.text.length);
    remaining -= frag.text.length;
    const visibleText = frag.text.slice(0, visibleLen);

    if (frag.highlight) {
      return (
        <span
          key={i}
          className="text-accent"
          style={{ borderBottom: "1px dashed var(--color-accent-dim)" }}
        >
          {visibleText}
        </span>
      );
    }
    return <span key={i}>{visibleText}</span>;
  });
}

function renderWithPasteRanges(text: string, pasteRanges: PasteRange[]) {
  if (pasteRanges.length === 0) return text;

  const sorted = [...pasteRanges].sort((a, b) => a.start - b.start);
  const parts: React.ReactNode[] = [];
  let lastEnd = 0;

  sorted.forEach((range, i) => {
    if (range.start > text.length) return;
    const clampedEnd = Math.min(range.end, text.length);

    if (range.start > lastEnd) {
      parts.push(
        <span key={`t-${i}`}>{text.slice(lastEnd, range.start)}</span>
      );
    }
    parts.push(
      <span
        key={`p-${i}`}
        className={
          range.active
            ? "bg-accent/15 text-accent font-mono text-xs rounded px-1 py-0.5 transition-colors duration-300"
            : "text-muted font-mono text-xs transition-colors duration-300"
        }
      >
        {text.slice(range.start, clampedEnd)}
      </span>
    );
    lastEnd = clampedEnd;
  });

  if (lastEnd < text.length) {
    parts.push(<span key="end">{text.slice(lastEnd)}</span>);
  }

  return <>{parts}</>;
}

// ── Sub-components ──

function TypingCursor() {
  return (
    <span
      className="inline-block w-[2px] h-[14px] bg-foreground/70 ml-[1px] align-middle"
      style={{ animation: "blink 1.06s step-end infinite" }}
    />
  );
}

function StreamCursor() {
  return (
    <span
      className="inline-block w-[2px] h-[14px] bg-foreground/40 ml-[1px] align-middle"
      style={{ animation: "blink 1.06s step-end infinite" }}
    />
  );
}

function MessageBubble({
  block,
  variant,
}: {
  block: RenderedBlock;
  variant: "memory" | "syke";
}) {
  const isUser = block.role === "user";
  const aiLabel = variant === "syke" ? "Syke" : "AI";

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      {isUser ? (
        <div className="border-l-2 border-accent/30 pl-4">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted mb-1.5">
            You
          </div>
          <div className="font-mono text-sm text-foreground leading-relaxed whitespace-pre-wrap">
            {renderWithPasteRanges(block.visibleText, block.pasteRanges)}
            {block.isTyping && <TypingCursor />}
          </div>
        </div>
      ) : (
        <div className="pl-4">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted mb-1.5">
            {aiLabel}
          </div>
          <div
            className={`font-mono text-sm leading-relaxed whitespace-pre-wrap ${
              variant === "memory" ? "text-dim" : "text-foreground/90"
            }`}
          >
            {block.fragments
              ? renderFragments(block.fragments, block.visibleCharCount)
              : block.visibleText}
            {!block.isComplete && block.visibleCharCount > 0 && (
              <StreamCursor />
            )}
          </div>
        </div>
      )}
    </motion.div>
  );
}

function ThinkingBubble({ block }: { block: RenderedBlock }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="pl-4 flex items-start gap-2"
    >
      <div
        className={`h-1.5 w-1.5 rounded-full mt-1.5 shrink-0 ${
          block.isComplete ? "bg-muted/40" : "bg-muted animate-pulse"
        }`}
      />
      <span
        className={`text-sm italic ${
          block.isComplete ? "text-muted/50" : "text-muted"
        }`}
      >
        {block.visibleText}
      </span>
    </motion.div>
  );
}

function ToolCallBubble({ block }: { block: RenderedBlock }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="pl-4 space-y-1"
    >
      <div className="flex items-center gap-2">
        <div
          className={`h-1.5 w-1.5 rounded-full shrink-0 ${
            block.isComplete ? "bg-muted/40" : "bg-muted animate-pulse"
          }`}
        />
        <span className="px-2 py-0.5 rounded text-xs font-mono bg-[#111827] text-gray-400">
          {block.toolName}
        </span>
        <span className="text-muted font-mono text-xs truncate">
          &quot;{block.toolQuery}&quot;
        </span>
      </div>
      {block.isComplete && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="text-xs text-dim pl-5 font-mono"
        >
          &rarr; {block.toolResult}
        </motion.div>
      )}
    </motion.div>
  );
}

function AnimatedChat({
  blocks,
  phase,
  variant,
}: {
  blocks: RenderedBlock[];
  phase: "idle" | "playing" | "done";
  variant: "memory" | "syke";
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const isMemory = variant === "memory";

  // Auto-scroll to bottom on content change
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  });

  return (
    <div
      className={`rounded-xl border transition-colors duration-500 ${
        isMemory
          ? "border-white/10 bg-[#0B1221] p-6"
          : phase === "done"
            ? "border-success/30 bg-[#0B1221] p-6"
            : "border-[var(--accent-acid)]/20 bg-[#0B1221] p-6"
      }`}
    >
      {/* Header */}
      <div className="mb-5 flex items-center gap-2">
        <div
          className={`h-2 w-2 rounded-full transition-colors duration-500 ${
            phase === "done"
              ? "bg-success"
              : phase === "playing"
                ? isMemory
                  ? "bg-muted animate-pulse"
                  : "bg-accent animate-pulse"
                : isMemory
                  ? "bg-muted"
                  : "bg-accent"
          }`}
        />
        <span
          className={`text-xs font-mono uppercase tracking-wider ${
            isMemory ? "text-gray-500" : "text-[var(--accent-acid)]"
          }`}
        >
          {isMemory ? "With memory" : "With Syke"}
        </span>
      </div>

      {/* Scrollable chat area */}
      <div
        ref={scrollRef}
        className="max-h-[480px] overflow-y-auto space-y-4"
      >
        {blocks.map((block) =>
          block.blockType === "tool_call" ? (
            <ToolCallBubble key={block.id} block={block} />
          ) : block.blockType === "thinking" ? (
            <ThinkingBubble key={block.id} block={block} />
          ) : (
            <MessageBubble key={block.id} block={block} variant={variant} />
          )
        )}
      </div>
    </div>
  );
}

// ── Main Component ──

export default function ProductContextGap() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: false, margin: "-100px" });

  const hasStartedRef = useRef(false);

  const memory = useConversationAnimation(memoryTimeline);
  const syke = useConversationAnimation(sykeTimeline);

  // Auto-play when section scrolls into view
  useEffect(() => {
    if (isInView && !hasStartedRef.current) {
      const timer = setTimeout(() => {
        hasStartedRef.current = true;
        memory.start();
        syke.start();
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [isInView, memory.start, syke.start]);

  const bothDone = memory.phase === "done" && syke.phase === "done";

  const handleReplay = () => {
    memory.reset();
    syke.reset();
    hasStartedRef.current = false;
    setTimeout(() => {
      hasStartedRef.current = true;
      memory.start();
      syke.start();
    }, 200);
  };

  return (
    <section className="mx-auto max-w-6xl px-6 py-20">
      <SectionHeader
        title="What memory should be"
        subtitle="Not what you said. Who you are."
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
        className="grid gap-6 md:grid-cols-2 items-start"
      >
        <AnimatedChat
          blocks={memory.blocks}
          phase={memory.phase}
          variant="memory"
        />
        <AnimatedChat
          blocks={syke.blocks}
          phase={syke.phase}
          variant="syke"
        />
      </motion.div>

      {/* Replay button */}
      <AnimatePresence>
        {bothDone && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.4 }}
            className="flex justify-center mt-6"
          >
            <button
              onClick={handleReplay}
              className="flex items-center gap-2 text-xs font-mono text-gray-500 hover:text-white border border-white/10 rounded-lg px-4 py-2 transition-colors"
            >
              <svg
                width="12"
                height="12"
                viewBox="0 0 12 12"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path
                  d="M1.5 1.5V4.5H4.5"
                  stroke="currentColor"
                  strokeWidth="1.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
                <path
                  d="M2.1 7.5A4.5 4.5 0 1 0 2.75 3.5L1.5 4.5"
                  stroke="currentColor"
                  strokeWidth="1.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              Replay
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Source legend */}
      <div className="mt-8 flex flex-wrap justify-center gap-6">
        {sourceLegend.map((label) => (
          <div key={label} className="flex items-center gap-2 text-xs">
            <div className="h-2 w-2 rounded-full bg-[var(--accent-acid)]" />
            <span className="text-gray-500 font-mono">{label}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
