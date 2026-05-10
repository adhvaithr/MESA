'use client';
import { useEffect, useRef, useState } from 'react';
import { Icon, fmtTime } from './Icon';
import type { TimelineEvent, TurnEvent, ToolEvent } from '@/lib/types';

function TurnBubble({ ev, fresh }: { ev: TurnEvent; fresh: boolean }) {
  return (
    <div className={`bubble bubble-${ev.speaker} ${fresh ? 'is-fresh' : ''}`}>
      <div className="bubble-meta">
        <span className="bubble-speaker">{ev.speaker === 'agent' ? 'Alex · Agent' : 'Caller'}</span>
        <span className="bubble-time mono">{fmtTime(ev.t)}</span>
      </div>
      <div className="bubble-body">{ev.text}</div>
    </div>
  );
}

function ToolCard({ ev, fresh }: { ev: ToolEvent; fresh: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`toolcard ${fresh ? 'is-fresh' : ''}`}>
      <div className="toolcard-line">
        <div className="toolcard-icon"><Icon name="bolt" size={11} /></div>
        <div className="toolcard-name mono">{ev.name}</div>
        <div className="toolcard-args mono">
          ({Object.entries(ev.args).map(([k, v], i) => (
            <span key={k}>
              {i > 0 && <span className="muted">, </span>}
              <span className="muted">{k}:</span>{' '}
              <span className="argv">{typeof v === 'string' ? `"${v}"` : String(v)}</span>
            </span>
          ))})
        </div>
        <button className="toolcard-toggle" onClick={() => setOpen(!open)} aria-label="Toggle details">
          <Icon name="chev" size={12} />
        </button>
        <span className="toolcard-time mono">{fmtTime(ev.t)}</span>
      </div>
      <div className="toolcard-meta">
        <span className="dot ok" /> resolved <span className="mono">{ev.durationMs}ms</span>
        <span className="sep">·</span>
        <span>writes <strong>{(ev.writes || []).length}</strong> field{(ev.writes || []).length === 1 ? '' : 's'}</span>
      </div>
      {open && (
        <pre className="toolcard-result mono">
          {JSON.stringify(ev.result, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function Transcript({ events, showTools, currentT, totalDuration }: {
  events: TimelineEvent[]; showTools: boolean; currentT: number; totalDuration: number;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [events.length]);

  const filtered = showTools ? events : events.filter((e) => e.kind !== 'tool');
  const turns = events.filter((e) => e.kind === 'turn').length;
  const tools = events.filter((e) => e.kind === 'tool').length;

  return (
    <section className="panel transcript">
      <header className="panel-hdr">
        <h2>Transcript</h2>
        <div className="panel-hdr-meta">
          <span className="chip">{turns} turns</span>
          <span className="chip chip-accent">{tools} tools</span>
        </div>
      </header>
      <div className="transcript-scroll" ref={scrollRef}>
        {filtered.length === 0 && <div className="transcript-empty">Awaiting first utterance…</div>}
        {filtered.map((ev, i) =>
          ev.kind === 'turn'
            ? <TurnBubble key={i} ev={ev} fresh={currentT - ev.t < 0.6} />
            : ev.kind === 'tool'
              ? <ToolCard key={i} ev={ev as ToolEvent} fresh={currentT - ev.t < 1.6} />
              : null
        )}
        {filtered.length > 0 && currentT < totalDuration && (
          <div className="typing"><span /><span /><span /></div>
        )}
      </div>
    </section>
  );
}
