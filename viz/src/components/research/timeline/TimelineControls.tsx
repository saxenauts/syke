'use client';

import { colors } from '@/lib/colors';

interface TimelineControlsProps {
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  onPlayPause: () => void;
  onReplay: () => void;
  onSeek: (time: number) => void;
}

export default function TimelineControls({
  isPlaying,
  currentTime,
  duration,
  onPlayPause,
  onReplay,
  onSeek,
}: TimelineControlsProps) {
  const isComplete = currentTime >= duration;

  return (
    <div className="flex items-center gap-6 mt-8">
      {/* Play/Pause/Replay */}
      <button
        onClick={isComplete ? onReplay : onPlayPause}
        className="px-6 py-2 rounded-lg font-medium transition-colors"
        style={{
          backgroundColor: colors.accent,
          color: colors.bg,
        }}
      >
        {isComplete ? 'Replay' : isPlaying ? 'Pause' : 'Play'}
      </button>

      {/* Time display */}
      <div className="text-sm font-mono" style={{ color: colors.dim }}>
        {Math.round(currentTime)}s / {duration}s
      </div>

      {/* Scrubber */}
      <div className="flex-1">
        <input
          type="range"
          min="0"
          max={duration}
          step="0.1"
          value={currentTime}
          onChange={(e) => onSeek(parseFloat(e.target.value))}
          className="w-full h-2 rounded-lg cursor-pointer"
          style={{
            background: `linear-gradient(to right, ${colors.accent} 0%, ${colors.accent} ${(currentTime / duration) * 100}%, ${colors.border} ${(currentTime / duration) * 100}%, ${colors.border} 100%)`,
          }}
        />
      </div>
    </div>
  );
}
