"use client";
import React, { useState, useEffect } from "react";

const Manifesto: React.FC = () => {
  const staticText = "You are not your";
  const identities = [
    "context window",
    "chat history",
    "last session",
    "API key",
    "session ID",
  ];
  const targetWidth = 16; // length of longest identity

  const [text, setText] = useState("");
  const [isDeleting, setIsDeleting] = useState(false);
  const [loopNum, setLoopNum] = useState(0);
  const [typingSpeed, setTypingSpeed] = useState(150);

  useEffect(() => {
    const handleType = () => {
      const i = loopNum % identities.length;
      const fullText = identities[i];
      const dots = ".".repeat(Math.max(0, targetWidth - fullText.length));
      const fullWithDots = fullText + dots;

      if (!isDeleting) {
        setText(fullWithDots.substring(0, text.length + 1));
      } else {
        setText(text.substring(0, text.length - 1));
      }

      let speed = 100;
      if (isDeleting) speed = 40;
      if (!isDeleting && text === fullWithDots) { speed = 2000; setIsDeleting(true); }
      else if (isDeleting && text === "") { setIsDeleting(false); setLoopNum(loopNum + 1); speed = 500; }
      setTypingSpeed(speed);
    };

    const timer = setTimeout(handleType, typingSpeed);
    return () => clearTimeout(timer);
  }, [text, isDeleting, loopNum, typingSpeed]);

  return (
    <section className="py-24 px-4 text-center relative overflow-hidden min-h-[400px] flex flex-col justify-center items-center">
      {/* Animated background */}
      <div className="absolute inset-0 select-none pointer-events-none overflow-hidden">
        <div className="absolute inset-0 opacity-35 mix-blend-screen">
          <svg className="w-full h-full" preserveAspectRatio="none" viewBox="0 0 100 100">
            <filter id="manifesto-noise" x="-20%" y="-20%" width="140%" height="140%">
              <feTurbulence type="fractalNoise" baseFrequency="0.015" numOctaves="5" result="noise">
                <animate attributeName="baseFrequency" values="0.015;0.025;0.015" dur="15s" repeatCount="indefinite" />
              </feTurbulence>
              <feDisplacementMap in="SourceGraphic" in2="noise" scale="30" xChannelSelector="R" yChannelSelector="G" />
            </filter>
            <defs>
              <linearGradient id="manifesto-grad" x1="0%" y1="100%" x2="0%" y2="0%">
                <stop offset="0%"  stopColor="var(--accent-acid)"     stopOpacity="0.35" />
                <stop offset="30%" stopColor="var(--accent-electric)" stopOpacity="0.15" />
                <stop offset="70%" stopColor="transparent"            stopOpacity="0"    />
              </linearGradient>
            </defs>
            <rect width="100%" height="100%" fill="url(#manifesto-grad)" filter="url(#manifesto-noise)" />
          </svg>
        </div>
        <div className="absolute inset-0 opacity-10" style={{ background: "repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,255,100,0.03) 2px,rgba(0,255,100,0.03) 4px)" }} />
      </div>

      {/* Text */}
      <div className="relative z-10 w-full max-w-4xl mx-auto flex flex-col items-center justify-center">
        <div className="w-full flex items-baseline justify-center">
          <div className="w-[50%] flex justify-end pr-2">
            <span className="font-mono-term text-xl md:text-3xl text-white/90 whitespace-nowrap leading-tight">
              {staticText}
            </span>
          </div>
          <div className="w-[50%] flex justify-start pl-2">
            <span className="font-mono-term text-xl md:text-3xl whitespace-nowrap leading-tight" style={{ minWidth: "17ch", minHeight: "1.2em" }}>
              <span className="text-acid">{text}</span>
              <span className="animate-pulse text-electric">_</span>
            </span>
          </div>
        </div>
        <div className="mt-8 text-center">
          <p className="text-white/70 text-base md:text-xl font-mono-term tracking-wide">
            Syke collects the signal. Synthesizes the pattern. Distributes the you.
          </p>
        </div>
      </div>
    </section>
  );
};

export default Manifesto;
