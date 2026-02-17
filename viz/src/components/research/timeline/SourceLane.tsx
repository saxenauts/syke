'use client';

import { sourceColors, sourceLabels } from '@/lib/colors';
import { colors } from '@/lib/colors';

interface SourceLaneProps {
  source: string;
  y: number;
  width: number;
  isActive?: boolean;
}

export default function SourceLane({
  source,
  y,
  width,
  isActive = false,
}: SourceLaneProps) {
  const color = sourceColors[source] || '#8b8b94';
  const label = sourceLabels[source] || source;

  return (
    <g>
      {/* Lane line */}
      <line
        x1={100}
        y1={y}
        x2={100 + width}
        y2={y}
        stroke={isActive ? color : colors.border}
        strokeWidth={isActive ? 2 : 1}
        strokeOpacity={isActive ? 0.8 : 0.3}
      />

      {/* Lane label */}
      <text
        x={20}
        y={y}
        fill={isActive ? color : colors.dim}
        fontSize="12"
        fontWeight={isActive ? '600' : '400'}
        dominantBaseline="middle"
      >
        {label}
      </text>

      {/* Source indicator dot */}
      <circle
        cx={85}
        cy={y}
        r={4}
        fill={color}
        fillOpacity={isActive ? 1 : 0.3}
      />
    </g>
  );
}
