'use client';

import { motion } from 'framer-motion';

interface ThemeThreadProps {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  color: string;
  label: string;
}

export default function ThemeThread({
  fromX,
  fromY,
  toX,
  toY,
  color,
  label,
}: ThemeThreadProps) {
  // S-curve between two lane positions; control points bow left (behind current time indicator)
  const midX = Math.min(fromX, toX) - 30;
  const path = `M ${fromX} ${fromY} C ${midX} ${fromY}, ${midX} ${toY}, ${toX} ${toY}`;

  return (
    <motion.path
      d={path}
      stroke={color}
      strokeOpacity={0.35}
      strokeWidth={1.5}
      fill="none"
      initial={{ pathLength: 0, opacity: 0 }}
      animate={{ pathLength: 1, opacity: 1 }}
      transition={{ duration: 0.8, ease: 'easeOut' }}
    >
      <title>{label}</title>
    </motion.path>
  );
}
