'use client';

import { useState, useEffect, useRef } from 'react';
import KnowledgeGraph from './timeline/KnowledgeGraph';
import TimelineControls from './timeline/TimelineControls';
import AgentActivityPanel from './AgentActivityPanel';
import { colors } from '@/lib/colors';
import SectionHeader from '../SectionHeader';

import timelineData from '@/data/timeline-demo.json';

export default function PerceptionTimeline() {
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  const hasAutoPlayed = useRef(false);

  const duration = timelineData.duration_s;

  // Auto-play on scroll into view
  useEffect(() => {
    if (hasAutoPlayed.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !hasAutoPlayed.current) {
            hasAutoPlayed.current = true;
            setTimeout(() => setIsPlaying(true), 500);
          }
        });
      },
      { threshold: 0.3 }
    );
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Animation loop at 4x speed
  useEffect(() => {
    if (!isPlaying) return;
    const interval = setInterval(() => {
      setCurrentTime((prev) => {
        const next = prev + 0.1 * 12;
        if (next >= duration) {
          setIsPlaying(false);
          return duration;
        }
        return next;
      });
    }, 100);
    return () => clearInterval(interval);
  }, [isPlaying, duration]);

  return (
    <section
      ref={containerRef}
      className="py-32 px-6"
      style={{ backgroundColor: colors.bg }}
    >
      <div className="max-w-7xl mx-auto">
        <SectionHeader
          title="A perception run, unfolding"
          subtitle="55 tool calls. 3 minutes. The agent sweeps all six sources, searches for recurring patterns, cross-references across platforms — then synthesizes a coherent identity model."
        />

        <div className="mt-16 flex flex-col lg:flex-row gap-6 items-start">
          <div className="flex-1 min-w-0">
            <KnowledgeGraph currentTime={currentTime} />

            <TimelineControls
              isPlaying={isPlaying}
              currentTime={currentTime}
              duration={duration}
              onPlayPause={() => setIsPlaying((p) => !p)}
              onReplay={() => { setCurrentTime(0); setIsPlaying(true); }}
              onSeek={(t) => setCurrentTime(t)}
            />
          </div>

          <div className="w-full lg:w-80 flex-shrink-0">
            <AgentActivityPanel steps={timelineData.steps} currentTime={currentTime} />
          </div>
        </div>
      </div>
    </section>
  );
}
