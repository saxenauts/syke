'use client';

import { motion } from 'framer-motion';
import { colors } from '@/lib/colors';

interface TopicBubbleProps {
  x: number;
  y: number;
  label: string;
  sources: string[];
  delay?: number;
}

export default function TopicBubble({
  x,
  y,
  label,
  sources,
  delay = 0,
}: TopicBubbleProps) {
  const radius = 28;

  return (
    <motion.g
      initial={{ scale: 0, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{
        type: 'spring',
        stiffness: 200,
        damping: 20,
        delay,
      }}
    >
      <motion.circle
        cx={x}
        cy={y}
        r={radius}
        fill={colors.accent}
        fillOpacity={0.15}
        stroke={colors.accent}
        strokeWidth={1.5}
        whileHover={{ fillOpacity: 0.25, strokeWidth: 2 }}
        style={{ cursor: 'pointer' }}
      />
      <text
        x={x}
        y={y}
        textAnchor="middle"
        dominantBaseline="middle"
        fill={colors.text}
        fontSize="11"
        fontWeight="500"
        style={{ pointerEvents: 'none' }}
      >
        {label}
      </text>
      <title>{`${label}\nSources: ${sources.join(', ')}`}</title>
    </motion.g>
  );
}
