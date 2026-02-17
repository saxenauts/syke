'use client';

import { motion } from 'framer-motion';
import { createBezierPath } from './processReplayData';

interface ConnectionThreadProps {
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  delay?: number;
  isVisible?: boolean;
}

export default function ConnectionThread({
  fromX,
  fromY,
  toX,
  toY,
  delay = 0,
  isVisible = true,
}: ConnectionThreadProps) {
  const path = createBezierPath(fromX, fromY, toX, toY);

  if (!isVisible) return null;

  return (
    <motion.path
      d={path}
      stroke="#2a2e3d"
      strokeOpacity={0.2}
      strokeWidth={1}
      fill="none"
      initial={{ pathLength: 0 }}
      animate={{ pathLength: 1 }}
      transition={{
        duration: 0.6,
        ease: 'easeOut',
        delay,
      }}
    />
  );
}
