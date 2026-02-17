'use client';

import { useMemo } from 'react';
import { colors } from '@/lib/colors';

interface ReplayStep {
  time_s: number;
  type: string;
  tool_name?: string;
  args_display?: string;
  result_display?: string;
  thinking_text?: string;
  topics_discovered?: string[];
  synthesized_insight?: string;
  coverage: {
    sources: Record<string, boolean | undefined>;
    pct: number;
  };
}

interface AgentActivityPanelProps {
  steps: ReplayStep[];
  currentTime: number;
}

export default function AgentActivityPanel({ steps, currentTime }: AgentActivityPanelProps) {
  const visibleSteps = useMemo(
    () => steps.filter((s) => s.time_s <= currentTime),
    [steps, currentTime]
  );

  const coverage = useMemo(() => {
    const last = [...visibleSteps].reverse().find((s) => s.coverage);
    return last?.coverage.pct ?? 0;
  }, [visibleSteps]);

  const lastThinking = useMemo(() => {
    const last = [...visibleSteps].reverse().find((s) => s.type === 'thinking' && s.thinking_text);
    return last?.thinking_text ?? null;
  }, [visibleSteps]);

  const lastTool = useMemo(() => {
    const last = [...visibleSteps].reverse().find((s) => s.type === 'tool_call');
    return last ?? null;
  }, [visibleSteps]);

  const lastResult = useMemo(() => {
    const last = [...visibleSteps].reverse().find((s) => s.type === 'tool_result');
    return last ?? null;
  }, [visibleSteps]);

  const latestInsight = useMemo(() => {
    const last = [...visibleSteps].reverse().find((s) => s.synthesized_insight);
    return last?.synthesized_insight ?? null;
  }, [visibleSteps]);

  const allTopics = useMemo(() => {
    const seen = new Set<string>();
    const ordered: string[] = [];
    visibleSteps.forEach((s) => {
      s.topics_discovered?.forEach((t) => {
        if (!seen.has(t)) {
          seen.add(t);
          ordered.push(t);
        }
      });
    });
    return ordered;
  }, [visibleSteps]);

  const isAlmaActive = useMemo(() => {
    if (!lastResult) return false;
    const text = (lastResult.result_display ?? '') + (lastResult.args_display ?? '');
    return /ALMA|meta.?learn/i.test(text);
  }, [lastResult]);

  return (
    <div
      className="rounded-lg overflow-hidden text-sm"
      style={{
        backgroundColor: colors.surface,
        border: `1px solid ${colors.border}`,
      }}
    >
      {/* Coverage bar */}
      <div className="px-4 py-3" style={{ borderBottom: `1px solid ${colors.border}` }}>
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-semibold tracking-widest uppercase" style={{ color: colors.dim }}>
            Coverage
          </span>
          <span className="text-xs font-mono" style={{ color: colors.accent }}>
            {coverage}%
          </span>
        </div>
        <div className="h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: colors.border }}>
          <div
            className="h-full transition-all duration-300 rounded-full"
            style={{ width: `${coverage}%`, backgroundColor: colors.accent }}
          />
        </div>
      </div>

      {/* Agent reasoning */}
      {lastThinking && (
        <div className="px-4 py-3" style={{ borderBottom: `1px solid ${colors.border}` }}>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-semibold tracking-widest uppercase" style={{ color: colors.dim }}>
              Agent Reasoning
            </span>
            {isAlmaActive && (
              <span
                className="text-xs px-1.5 py-0.5 rounded font-semibold"
                style={{ backgroundColor: '#78350f', color: colors.warning }}
              >
                META-LEARNING
              </span>
            )}
          </div>
          <p className="italic leading-relaxed" style={{ color: colors.text }}>
            &ldquo;{lastThinking}&rdquo;
          </p>
        </div>
      )}

      {/* Last tool call + result */}
      {(lastTool || lastResult) && (
        <div className="px-4 py-3" style={{ borderBottom: `1px solid ${colors.border}` }}>
          {lastTool && (
            <div className="mb-1.5">
              <span style={{ color: colors.accent }}>→ </span>
              <span className="font-mono font-medium" style={{ color: colors.text }}>
                {lastTool.tool_name}
              </span>
              {lastTool.args_display && (
                <div className="mt-0.5 font-mono text-xs" style={{ color: colors.dim }}>
                  {lastTool.args_display}
                </div>
              )}
            </div>
          )}
          {lastResult && lastResult.result_display && (
            <div className="flex gap-1.5">
              <span style={{ color: colors.success }}>✓</span>
              <span style={{ color: colors.dim }}>{lastResult.result_display}</span>
            </div>
          )}
        </div>
      )}

      {/* Synthesis insight */}
      {latestInsight && (
        <div className="px-4 py-3" style={{ borderBottom: `1px solid ${colors.border}`, backgroundColor: colors.surface2 }}>
          <div className="flex items-center gap-1.5 mb-2">
            <span style={{ color: colors.warning }}>✦</span>
            <span className="text-xs font-semibold tracking-widest uppercase" style={{ color: colors.warning }}>
              Synthesis
            </span>
          </div>
          <p className="leading-relaxed" style={{ color: colors.text }}>
            {latestInsight}
          </p>
        </div>
      )}

    </div>
  );
}
