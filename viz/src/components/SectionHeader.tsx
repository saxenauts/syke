"use client";

import { motion, useInView } from "framer-motion";
import { useRef } from "react";

interface SectionHeaderProps {
  title: string;
  subtitle?: string;
  act?: string;
}

export default function SectionHeader({ title, subtitle, act }: SectionHeaderProps) {
  const ref = useRef(null);
  const isInView = useInView(ref, { once: true, margin: "-80px" });

  return (
    <motion.div
      ref={ref}
      initial={{ y: 30, opacity: 0 }}
      animate={isInView ? { y: 0, opacity: 1 } : {}}
      transition={{ duration: 0.6 }}
      className="text-center mb-12"
    >
      {act && (
        <div className="text-xs font-mono tracking-[0.2em] uppercase text-muted mb-4">
          {act}
        </div>
      )}
      <h2 className="text-3xl font-light tracking-tight sm:text-4xl lg:text-5xl">
        {title}
      </h2>
      {subtitle && (
        <p className="mt-4 text-dim max-w-2xl mx-auto text-lg font-light">
          {subtitle}
        </p>
      )}
    </motion.div>
  );
}
