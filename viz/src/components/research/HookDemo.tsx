"use client";

import { motion, useInView, AnimatePresence } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import SectionHeader from "../SectionHeader";
import hookData from "@/data/hook-demo.json";

const statusColors: Record<string, string> = {
  neutral: "#8b8b94",
  error: "#f87171",
  warning: "#fbbf24",
  success: "#34d399",
};

const statusBg: Record<string, string> = {
  neutral: "#8b8b9410",
  error: "#f8717115",
  warning: "#fbbf2415",
  success: "#34d39915",
};

const iconMap: Record<string, React.ReactNode> = {
  send: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
    </svg>
  ),
  shield: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  ),
  search: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" />
      <path d="M21 21l-4.35-4.35" />
    </svg>
  ),
  check: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6L9 17l-5-5" />
    </svg>
  ),
};

export default function HookDemo() {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });
  const [activeStep, setActiveStep] = useState(-1);

  useEffect(() => {
    if (!isInView) return;
    const steps = hookData.steps;
    let i = 0;
    const interval = setInterval(() => {
      if (i < steps.length) {
        setActiveStep(i);
        i++;
      } else {
        clearInterval(interval);
      }
    }, 1200);
    return () => clearInterval(interval);
  }, [isInView]);

  return (
    <section className="mx-auto max-w-4xl px-6 py-32">
      <SectionHeader
        title="Coverage gate in action"
        subtitle="The agent can't skip platforms. A hooks-based quality gate ensures completeness."
      />

      <motion.div
        ref={ref}
        initial={{ y: 40, opacity: 0 }}
        animate={isInView ? { y: 0, opacity: 1 } : {}}
        transition={{ duration: 0.7 }}
        className="grid gap-4 sm:grid-cols-2"
      >
        <AnimatePresence>
          {hookData.steps.map((step, i) => {
            const isActive = i <= activeStep;
            const isCurrent = i === activeStep;
            const isPast = isActive && !isCurrent;
            const color = statusColors[step.status];

            return (
              <motion.div
                key={step.id}
                initial={{ opacity: 0, scale: 0.95 }}
                animate={isActive ? { opacity: 1, scale: 1 } : { opacity: 0.3, scale: 0.95 }}
                transition={{ duration: 0.4 }}
                className="rounded-xl border p-5 transition-colors"
                style={{
                  borderColor: isCurrent ? `${color}55` : isPast ? `${color}28` : '#2a2e3d',
                  backgroundColor: isCurrent ? statusBg[step.status] : isPast ? `${color}08` : '#1a1d2740',
                }}
              >
                <div className="flex items-center gap-3 mb-3">
                  <div className="relative h-8 w-8">
                    <div
                      className="h-8 w-8 rounded-lg flex items-center justify-center"
                      style={{ backgroundColor: `${color}20`, color }}
                    >
                      {iconMap[step.icon]}
                    </div>
                    {isPast && (
                      <div
                        className="absolute -top-1 -right-1 h-3 w-3 rounded-full border-2"
                        style={{ backgroundColor: color, borderColor: '#0f1117' }}
                      />
                    )}
                  </div>
                  <div>
                    <div className="text-xs font-mono text-muted">
                      Step {i + 1}
                    </div>
                    <div className="text-sm font-medium" style={{ color: isActive ? '#e4e4e7' : '#5a5b65' }}>
                      {step.title}
                    </div>
                  </div>
                </div>

                <p className="text-xs text-dim leading-relaxed mb-3">
                  {step.description}
                </p>

                <div
                  className="rounded-md px-3 py-2 font-mono text-[11px] leading-relaxed overflow-x-auto"
                  style={{
                    backgroundColor: '#0f1117',
                    color: isActive ? color : '#5a5b65',
                  }}
                >
                  {step.code}
                </div>
              </motion.div>
            );
          })}
        </AnimatePresence>
      </motion.div>
    </section>
  );
}
