'use client';

import { motion } from 'framer-motion';
import { sourceColors } from '@/lib/colors';

interface EventDotProps {
  x: number;
  y: number;
  source: string;
  delay?: number;
  toolName?: string;
  argsDisplay?: string;
  resultDisplay?: string;
}

export default function EventDot({
  x,
  y,
  source,
  delay = 0,
  toolName,
  argsDisplay,
  resultDisplay,
}: EventDotProps) {
  const color = sourceColors[source] || '#8b8b94';

  return (
    <motion.g
      initial={{ scale: 0, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{
        duration: 0.3,
        type: 'spring',
        delay,
      }}
    >
      <motion.circle
        cx={x}
        cy={y}
        r={6}
        fill={color}
        whileHover={{ r: 8, opacity: 0.8 }}
        style={{ cursor: 'pointer' }}
      />
      <title>
        {toolName && `${toolName}\n`}
        {argsDisplay && `${argsDisplay}\n`}
        {resultDisplay && resultDisplay}
      </title>
    </motion.g>
  );
}
