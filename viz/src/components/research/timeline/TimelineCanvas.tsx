'use client';

import { useMemo } from 'react';
import SourceLane from './SourceLane';
import EventDot from './EventDot';
import ThemeThread from './ThemeThread';
import { TimelineEvent, TopicBubble as TopicBubbleType, ThemeThread as ThemeThreadType, timeToX, LANE_Y_POSITIONS } from './processReplayData';
import { colors } from '@/lib/colors';

interface TimelineCanvasProps {
  events: TimelineEvent[];
  topics: TopicBubbleType[];
  threads: ThemeThreadType[];
  duration: number;
  currentTime: number;
}

const SOURCES = ['claude-web', 'claude-code', 'claude-cowork', 'gmail', 'chatgpt', 'github'];

export default function TimelineCanvas({
  events,
  threads,
  duration,
  currentTime,
}: TimelineCanvasProps) {
  // Filter visible events and threads based on currentTime
  const visibleEvents = useMemo(
    () => events.filter((e) => e.time_s <= currentTime),
    [events, currentTime]
  );

  const visibleThreads = useMemo(
    () => threads.filter((t) => t.firstSeen <= currentTime),
    [threads, currentTime]
  );

  // Check which lanes are currently active
  const activeSources = useMemo(() => {
    const active = new Set<string>();
    visibleEvents.forEach((e) => {
      if (e.source) active.add(e.source);
    });
    return active;
  }, [visibleEvents]);

  // Coverage percentage
  const currentCoverage = useMemo(() => {
    const event = [...events].reverse().find((e) => e.time_s <= currentTime);
    return event?.coverage || 0;
  }, [events, currentTime]);

  return (
    <div className="w-full">
      {/* Coverage meter */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium" style={{ color: colors.text }}>
            Coverage Progress
          </span>
          <span className="text-sm font-mono" style={{ color: colors.accent }}>
            {currentCoverage}%
          </span>
        </div>
        <div
          className="h-2 rounded-full overflow-hidden"
          style={{ backgroundColor: colors.border }}
        >
          <div
            className="h-full transition-all duration-300 rounded-full"
            style={{
              width: `${currentCoverage}%`,
              backgroundColor: colors.accent,
            }}
          />
        </div>
      </div>

      {/* SVG Canvas */}
      <svg
        viewBox="0 0 1100 700"
        className="w-full"
        style={{ backgroundColor: colors.bg }}
      >
        {/* Timeline axis */}
        <line
          x1={100}
          y1={50}
          x2={1000}
          y2={50}
          stroke={colors.border}
          strokeWidth={1}
        />

        {/* Time markers */}
        {[0, 47, 95, 142, 189].map((time) => {
          const x = timeToX(time, duration);
          return (
            <g key={time}>
              <line
                x1={x}
                y1={45}
                x2={x}
                y2={55}
                stroke={colors.muted}
                strokeWidth={1}
              />
              <text
                x={x}
                y={35}
                textAnchor="middle"
                fill={colors.dim}
                fontSize="10"
              >
                {time}s
              </text>
            </g>
          );
        })}

        {/* Current time indicator */}
        <line
          x1={timeToX(currentTime, duration)}
          y1={50}
          x2={timeToX(currentTime, duration)}
          y2={650}
          stroke={colors.accent}
          strokeWidth={2}
          strokeOpacity={0.3}
          strokeDasharray="4 4"
        />

        {/* Source lanes */}
        {SOURCES.map((source) => (
          <SourceLane
            key={source}
            source={source}
            y={LANE_Y_POSITIONS[source]}
            width={900}
            isActive={activeSources.has(source)}
          />
        ))}

        {/* Stitch threads (draw after lanes, before event dots) */}
        {visibleThreads.map((thread) => (
          <ThemeThread
            key={thread.id}
            fromX={thread.fromX}
            fromY={thread.fromY}
            toX={thread.toX}
            toY={thread.toY}
            color={thread.color}
            label={thread.label}
          />
        ))}

        {/* Event dots */}
        {visibleEvents.map((event, i) => {
          if (!event.source) return null;
          const laneY = LANE_Y_POSITIONS[event.source];
          if (!laneY) return null;

          return (
            <EventDot
              key={`${event.time_s}-${i}`}
              x={timeToX(event.time_s, duration)}
              y={laneY}
              source={event.source}
              delay={0}
              toolName={event.tool_name}
              argsDisplay={event.args_display}
              resultDisplay={event.result_display}
            />
          );
        })}
      </svg>
    </div>
  );
}
