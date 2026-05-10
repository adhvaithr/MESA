import { Icon } from './Icon';
import { CALLER_TYPES } from '@/lib/staticData';

interface CallerType {
  id: string;
  confidence: number;
  rationale: string;
  classifiedAt: number;
}

export function CallerTypeStrip({ callerType, currentT }: {
  callerType: CallerType | null; currentT: number;
}) {
  const fresh = callerType != null && currentT - callerType.classifiedAt < 2.0;
  return (
    <div className={`cts ${callerType ? 'is-classified' : ''} ${fresh ? 'is-fresh' : ''}`}>
      <div className="cts-label">
        <Icon name="bolt" size={12} />
        <span>Caller classification</span>
        {!callerType && <span className="cts-status mono">listening…</span>}
        {callerType && (
          <span className="cts-status mono">
            <span className="dot ok" /> classified {callerType.classifiedAt.toFixed(1)}s ·{' '}
            confidence {(callerType.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>
      <div className="cts-options">
        {CALLER_TYPES.map((ct) => {
          const active = callerType && callerType.id === ct.id;
          const dimmed = callerType && !active;
          return (
            <div key={ct.id} className={`cts-opt ${active ? 'is-active' : ''} ${dimmed ? 'is-dim' : ''}`}>
              <div className="cts-check">
                {active
                  ? <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>
                  : <span className="cts-empty" />}
              </div>
              <div className="cts-icon"><Icon name={ct.icon} size={14} /></div>
              <div className="cts-text">
                <div className="cts-name">{ct.label}</div>
                <div className="cts-desc">{ct.desc}</div>
              </div>
            </div>
          );
        })}
      </div>
      {callerType && (
        <div className="cts-rationale">
          <span className="cts-rat-label mono">rationale</span>
          <span className="cts-rat-text">{callerType.rationale}</span>
        </div>
      )}
    </div>
  );
}
