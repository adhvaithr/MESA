'use client';
import { useRef, useState, useMemo } from 'react';
import { Icon, fmtTime } from './Icon';
import type { TimelineEvent } from '@/lib/types';

export function Playback({ t, total, playing, onTogglePlay, onSeek, timeline }: {
  t: number; total: number; playing: boolean;
  onTogglePlay: () => void; onSeek: (t: number) => void;
  timeline: TimelineEvent[];
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);

  const onMouseDown = (e: React.MouseEvent) => {
    const handle = (ev: MouseEvent) => {
      if (!trackRef.current) return;
      const rect = trackRef.current.getBoundingClientRect();
      const x = Math.max(0, Math.min(rect.width, ev.clientX - rect.left));
      onSeek((x / rect.width) * total);
    };
    handle(e.nativeEvent);
    const onMove = (ev: MouseEvent) => handle(ev);
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  };

  const toolMarkers = useMemo(() => timeline.filter((e) => e.kind === 'tool'), [timeline]);
  const pct = (t / total) * 100;
  const skip = (delta: number) => onSeek(Math.max(0, Math.min(total, t + delta)));

  return (
    <div className="playback">
      <div className="pb-controls">
        <button className="pb-btn ghost" onClick={() => skip(-5)} aria-label="Back 5s">
          <Icon name="rewind" size={14} /><span className="pb-num">5</span>
        </button>
        <button className="pb-btn primary" onClick={onTogglePlay} aria-label={playing ? 'Pause' : 'Play'}>
          <Icon name={playing ? 'pause' : 'play'} size={16} />
        </button>
        <button className="pb-btn ghost" onClick={() => skip(5)} aria-label="Forward 5s">
          <span className="pb-num">5</span><Icon name="skip" size={14} />
        </button>
      </div>
      <div className="pb-track-wrap">
        <div
          ref={trackRef}
          className="pb-track"
          onMouseDown={onMouseDown}
          onMouseMove={(e) => {
            if (!trackRef.current) return;
            const rect = trackRef.current.getBoundingClientRect();
            setHover(((e.clientX - rect.left) / rect.width) * total);
          }}
          onMouseLeave={() => setHover(null)}
        >
          <div className="pb-fill" style={{ width: `${pct}%` }} />
          {toolMarkers.map((m, i) => (
            <div
              key={i}
              className={`pb-marker ${m.t <= t ? 'is-fired' : ''}`}
              style={{ left: `${(m.t / total) * 100}%` }}
              title={(m as { name?: string }).name}
            />
          ))}
          <div className="pb-handle" style={{ left: `${pct}%` }} />
          {hover != null && (
            <div className="pb-tooltip" style={{ left: `${(hover / total) * 100}%` }}>
              {fmtTime(hover)}
            </div>
          )}
        </div>
        <div className="pb-times">
          <span className="mono">{fmtTime(t)}</span>
          <span className="mono pb-total">{fmtTime(total)}</span>
        </div>
      </div>
    </div>
  );
}
