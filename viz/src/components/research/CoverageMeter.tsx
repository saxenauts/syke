"use client";

import { motion } from "framer-motion";
import { sourceColors, sourceLabels } from "@/lib/colors";

interface CoverageMeterProps {
  sources: Record<string, boolean>;
  pct: number;
}

const allSources = ["claude-code", "chatgpt", "github"] as const;

export default function CoverageMeter({ sources, pct }: CoverageMeterProps) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between text-xs">
        <span className="font-mono text-muted uppercase tracking-wider">Source Coverage</span>
        <span className="font-mono text-foreground">{pct}%</span>
      </div>

      {/* Overall bar */}
      <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden">
        <motion.div
          className="h-full rounded-full bg-accent"
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
        />
      </div>

      {/* Per-source indicators */}
      <div className="space-y-2">
        {allSources.map((source) => {
          const active = sources[source] ?? false;
          const color = sourceColors[source];
          return (
            <div key={source} className="flex items-center gap-3">
              <div
                className="h-2 w-2 rounded-full transition-all duration-300"
                style={{
                  backgroundColor: active ? color : '#2a2e3d',
                  boxShadow: active ? `0 0 8px ${color}40` : 'none',
                }}
              />
              <span
                className="text-xs font-mono transition-colors duration-300"
                style={{ color: active ? color : '#5a5b65' }}
              >
                {sourceLabels[source]}
              </span>
              <div className="flex-1 h-px" style={{ backgroundColor: active ? `${color}30` : '#2a2e3d' }} />
              <span className="text-[10px] font-mono" style={{ color: active ? color : '#5a5b65' }}>
                {active ? 'explored' : 'pending'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
