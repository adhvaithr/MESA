'use client';
import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Header } from './Header';
import { Playback } from './Playback';
import { CallerTypeStrip } from './CallerTypeStrip';
import { Transcript } from './Transcript';
import { BlocksGrid } from './BlocksGrid';
import type { CallMeta, TimelineEvent, ToolEvent, AppState, TweakValues } from '@/lib/types';

function computeState(timeline: TimelineEvent[], t: number): AppState {
  const state: Record<string, Record<string, { value: unknown; writtenAt: number; tool: string }>> = {};
  const toolEvents: ToolEvent[] = [];
  let callerType: AppState['callerType'] = null;

  for (const ev of timeline) {
    if (ev.t > t) break;
    if (ev.kind === 'tool') {
      toolEvents.push(ev);
      for (const w of ev.writes ?? []) {
        if (!state[w.block]) state[w.block] = {};
        state[w.block][w.field] = { value: w.value, writtenAt: ev.t, tool: ev.name };
      }
    } else if (ev.kind === 'classify') {
      callerType = { id: ev.callerType, confidence: ev.confidence, rationale: ev.rationale, classifiedAt: ev.t };
    }
  }
  return { state, toolEvents, callerType };
}

const TWEAK_DEFAULTS: TweakValues = {
  theme: 'light',
  density: 'comfortable',
  pulseIntensity: 'subtle',
  autoExpand: true,
  showInlineTools: true,
  playbackSpeed: 1,
};

export function CallMonitor({ meta, timeline }: { meta: CallMeta; timeline: TimelineEvent[] }) {
  const [tweaks, setTweaks] = useState<TweakValues>(TWEAK_DEFAULTS);
  const setTweak = useCallback(<K extends keyof TweakValues>(key: K, val: TweakValues[K]) => {
    setTweaks((prev) => ({ ...prev, [key]: val }));
  }, []);

  const total = meta.totalDuration;
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(true);
  const lastTickRef = useRef(performance.now());

  useEffect(() => {
    if (!playing) return;
    let raf: number;
    lastTickRef.current = performance.now();
    const tick = (now: number) => {
      const dt = (now - lastTickRef.current) / 1000;
      lastTickRef.current = now;
      setT((prev) => {
        const next = prev + dt * tweaks.playbackSpeed;
        if (next >= total) { setPlaying(false); return total; }
        return next;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, tweaks.playbackSpeed, total]);

  const togglePlay = () => {
    if (t >= total) setT(0);
    setPlaying((p) => !p);
  };

  const { state, toolEvents, callerType } = useMemo(() => computeState(timeline, t), [timeline, t]);

  const visibleEvents = useMemo(() => timeline.filter((e) => e.t <= t), [timeline, t]);

  const recentBlocks = useMemo(() => {
    const recent = new Set<string>();
    for (const ev of timeline) {
      if (ev.kind !== 'tool' || ev.t > t) continue;
      if (t - ev.t < 1.6) {
        for (const w of ev.writes ?? []) recent.add(w.block);
      }
    }
    return recent;
  }, [timeline, t]);

  const { theme, density, pulseIntensity, autoExpand, showInlineTools, playbackSpeed } = tweaks;

  return (
    <div className={`app theme-${theme} density-${density} pulse-${pulseIntensity}`}>
      <Header
        meta={meta} t={t} total={total}
        toolCount={toolEvents.length}
        theme={theme}
        onToggleTheme={() => setTweak('theme', theme === 'light' ? 'dark' : 'light')}
      />
      <Playback
        t={t} total={total} playing={playing}
        onTogglePlay={togglePlay} onSeek={setT}
        timeline={timeline}
      />
      <CallerTypeStrip callerType={callerType} currentT={t} />
      <main className="main">
        <Transcript
          events={visibleEvents}
          showTools={showInlineTools}
          currentT={t}
          totalDuration={total}
        />
        <BlocksGrid
          state={state}
          recent={recentBlocks}
          autoExpand={autoExpand}
          currentT={t}
        />
      </main>

      {/* Tweaks panel — same controls, now a simple settings drawer */}
      <details className="tweaks-details">
        <summary className="tweaks-summary">⚙ Settings</summary>
        <div className="tweaks-body">
          <label>Theme
            <select value={theme} onChange={(e) => setTweak('theme', e.target.value as 'light' | 'dark')}>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
          <label>Density
            <select value={density} onChange={(e) => setTweak('density', e.target.value as 'comfortable' | 'compact')}>
              <option value="comfortable">Comfortable</option>
              <option value="compact">Compact</option>
            </select>
          </label>
          <label>Pulse
            <select value={pulseIntensity} onChange={(e) => setTweak('pulseIntensity', e.target.value as TweakValues['pulseIntensity'])}>
              <option value="none">None</option>
              <option value="subtle">Subtle</option>
              <option value="strong">Strong</option>
            </select>
          </label>
          <label>Speed
            <input type="range" min={0.5} max={4} step={0.5} value={playbackSpeed}
              onChange={(e) => setTweak('playbackSpeed', Number(e.target.value))} />
            {playbackSpeed}x
          </label>
          <label>
            <input type="checkbox" checked={autoExpand}
              onChange={(e) => setTweak('autoExpand', e.target.checked)} />
            {' '}Auto-expand blocks
          </label>
          <label>
            <input type="checkbox" checked={showInlineTools}
              onChange={(e) => setTweak('showInlineTools', e.target.checked)} />
            {' '}Show tool calls in transcript
          </label>
        </div>
      </details>
    </div>
  );
}
