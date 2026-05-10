import { Icon, fmtTime } from './Icon';
import type { CallMeta } from '@/lib/types';

function KV({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="kv">
      <div className="kv-label">{label}</div>
      <div className={`kv-value ${mono ? 'mono' : ''}`}>{value}</div>
    </div>
  );
}

export function Header({
  meta, t, total, toolCount, theme, onToggleTheme,
}: {
  meta: CallMeta; t: number; total: number;
  toolCount: number; theme: string; onToggleTheme: () => void;
}) {
  const live = t < total;
  return (
    <header className="hdr">
      <div className="hdr-left">
        <div className="brand">
          <div className="brand-mark">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 12h2l1.5-4 2.5 8 2-12 2.5 16 2-10 1.5 6 1.5-4h3"/>
            </svg>
          </div>
          <div>
            <div className="brand-name">MESA<span className="brand-dot">·</span><span className="brand-sub">Console</span></div>
            <div className="brand-meta">Voice agent monitoring</div>
          </div>
        </div>
        <div className="hdr-divider" />
        <div className="call-meta">
          <div className="cm-row">
            <span className={`live-pill ${live ? 'is-live' : 'is-done'}`}>
              <span className="live-dot" />
              {live ? 'LIVE' : 'ENDED'}
            </span>
            <span className="cm-id">{meta.callId}</span>
          </div>
          <div className="cm-row cm-row-sm">
            <span>{meta.channel}</span>
            <span className="sep">·</span>
            <span>{meta.intent}</span>
          </div>
        </div>
      </div>
      <div className="hdr-right">
        <KV label="Caller" value={meta.callerNumber} mono />
        <KV label="Region" value={meta.region} />
        <KV label="Agent" value={meta.agent} />
        <KV label="Tools fired" value={String(toolCount).padStart(2, '0')} />
        <KV label="Elapsed" value={`${fmtTime(t)} / ${fmtTime(total)}`} mono />
        <button className="theme-btn" onClick={onToggleTheme} aria-label="Toggle theme">
          <Icon name={theme === 'light' ? 'moon' : 'sun'} size={16} />
        </button>
      </div>
    </header>
  );
}
