"use client";
import React, { useState, useEffect } from "react";

const LatentFluid: React.FC = () => {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    const mql = window.matchMedia("(max-width: 768px)");
    setIsMobile(mql.matches);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  // Mobile: lightweight CSS gradient only — no SVG filters, no GPU jank
  if (isMobile) {
    return (
      <div className="absolute inset-0 z-0 pointer-events-none opacity-50">
        <div
          className="absolute inset-0"
          style={{
            background: `
              radial-gradient(ellipse 60% 50% at 25% 30%, var(--accent-electric) 0%, transparent 70%),
              radial-gradient(ellipse 50% 60% at 75% 70%, var(--accent-acid) 0%, transparent 70%)
            `,
            opacity: 0.15,
            filter: "blur(40px)",
          }}
        />
        <div
          className="absolute inset-0 opacity-60"
          style={{ background: "radial-gradient(circle, transparent 30%, var(--bg-primary) 100%)" }}
        />
      </div>
    );
  }

  // Desktop: full SVG animation with feTurbulence + blur circles
  return (
    <div className="absolute inset-0 z-0 overflow-hidden pointer-events-none opacity-65 mix-blend-screen animate-breathe">
      <svg className="w-full h-full opacity-90" preserveAspectRatio="none" viewBox="0 0 100 100">
        <filter id="noise" x="-20%" y="-20%" width="140%" height="140%">
          <feTurbulence type="fractalNoise" baseFrequency="0.015" numOctaves="5" result="noise">
            <animate attributeName="baseFrequency" values="0.015;0.025;0.015" dur="15s" repeatCount="indefinite" />
          </feTurbulence>
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="25" xChannelSelector="R" yChannelSelector="G" />
        </filter>
        <defs>
          <linearGradient id="fluid-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%"   stopColor="var(--accent-electric)" stopOpacity="0.2" />
            <stop offset="50%"  stopColor="var(--bg-primary)"      stopOpacity="0"   />
            <stop offset="100%" stopColor="var(--accent-acid)"     stopOpacity="0.2" />
          </linearGradient>
        </defs>
        <rect width="100%" height="100%" fill="url(#fluid-gradient)" filter="url(#noise)" />
        <circle cx="20" cy="30" r="25" fill="var(--accent-electric)" style={{ filter: "blur(60px)" }} opacity="0.5">
          <animate attributeName="cx" values="20;40;20" dur="8s"  repeatCount="indefinite" />
          <animate attributeName="cy" values="30;10;30" dur="12s" repeatCount="indefinite" />
        </circle>
        <circle cx="80" cy="70" r="30" fill="var(--accent-acid)" style={{ filter: "blur(70px)" }} opacity="0.45">
          <animate attributeName="cx" values="80;60;80" dur="10s" repeatCount="indefinite" />
          <animate attributeName="cy" values="70;90;70" dur="14s" repeatCount="indefinite" />
        </circle>
      </svg>
      <div className="absolute inset-0 opacity-60" style={{ background: "radial-gradient(circle, transparent 30%, var(--bg-primary) 100%)" }} />
    </div>
  );
};

export default LatentFluid;
