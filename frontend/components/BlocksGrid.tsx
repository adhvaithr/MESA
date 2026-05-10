'use client';
import { useState } from 'react';
import { Icon } from './Icon';
import { BLOCKS } from '@/lib/staticData';
import type { BlockState } from '@/lib/types';

function ListValue({ items, kind }: { items: unknown; kind: string }) {
  if (!Array.isArray(items)) return <span className="row-val">{String(items)}</span>;
  return (
    <div className="list">
      {(items as Record<string, unknown>[]).map((it, i) => (
        <div key={i} className="list-item">
          {kind === 'nearby' && (
            <>
              <div className="li-line1">
                <span className="li-name">{String(it.name ?? '')}</span>
                <span className="li-tag mono">{String(it.distance_mi ?? '')} mi</span>
              </div>
              <div className="li-line2 mono">{String(it.address ?? '')} · {String(it.phone ?? '')}</div>
            </>
          )}
          {kind === 'available' && (
            <>
              <div className="li-line1">
                <span className="li-name">{String(it.food_type ?? '')}</span>
                <span className="li-tag mono">{String(it.id ?? '')}</span>
              </div>
              <div className="li-line2"><span className="mono">{String(it.quantity ?? '')}</span> · {String(it.donor ?? '')}</div>
              <div className="li-line3 mono muted">until {String(it.pickup_until ?? '')}</div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}

function Block({ def, data, isRecent, autoExpand, currentT }: {
  def: typeof BLOCKS[0]; data: BlockState | undefined;
  isRecent: boolean; autoExpand: boolean; currentT: number;
}) {
  const populated = !!data;
  const [openManual, setOpenManual] = useState<boolean | null>(null);
  const open = openManual !== null ? openManual : (autoExpand ? populated : false);
  const fieldCount = data ? Object.keys(data).length : 0;

  return (
    <div className={`block ${populated ? 'is-pop' : 'is-empty'} ${isRecent ? 'is-recent' : ''} ${open ? 'is-open' : ''}`}>
      <button className="block-hdr" onClick={() => setOpenManual(!open)}>
        <div className="block-icon"><Icon name={def.icon} size={14} /></div>
        <div className="block-titles">
          <div className="block-title">{def.title}</div>
          <div className="block-sub mono">{def.subtitle}()</div>
        </div>
        <div className="block-status">
          {populated
            ? <span className="status-pill ok"><span className="dot ok" />{fieldCount}/{def.fields.length}</span>
            : <span className="status-pill pending"><span className="dot pending" />awaiting</span>}
        </div>
        <div className="block-chev"><Icon name="chev" size={14} /></div>
      </button>
      <div className="block-body" style={{ maxHeight: open ? 1200 : 0 }}>
        <div className="block-body-inner">
          {!populated && (
            <div className="block-skeleton">
              {def.fields.slice(0, 3).map((f) => (
                <div key={f} className="skel-row">
                  <span className="skel-key mono">{f}</span>
                  <span className="skel-val" />
                </div>
              ))}
            </div>
          )}
          {populated && def.fields.map((f) => {
            const cell = data![f];
            if (!cell) return (
              <div key={f} className="row row-pending">
                <span className="row-key mono">{f}</span>
                <span className="row-val muted">—</span>
              </div>
            );
            const isList = def.listField === f;
            const fresh = currentT - cell.writtenAt < 1.6;
            return (
              <div key={f} className={`row ${fresh ? 'is-fresh' : ''} ${isList ? 'row-list' : ''}`}>
                <span className="row-key mono">{f}</span>
                {isList
                  ? <ListValue items={cell.value} kind={def.id} />
                  : <span className="row-val">{String(cell.value)}</span>}
                {fresh && <span className="row-tag">just now</span>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export function BlocksGrid({ state, recent, autoExpand, currentT }: {
  state: Record<string, BlockState>; recent: Set<string>;
  autoExpand: boolean; currentT: number;
}) {
  return (
    <section className="panel blocks">
      <header className="panel-hdr">
        <h2>Database writes</h2>
        <div className="panel-hdr-meta">
          <span className="chip">Supabase · live</span>
          <span className="chip">{Object.keys(state).length}/{BLOCKS.length} tables touched</span>
        </div>
      </header>
      <div className="blocks-grid">
        {BLOCKS.map((b) => (
          <Block
            key={b.id}
            def={b}
            data={state[b.id]}
            isRecent={recent.has(b.id)}
            autoExpand={autoExpand}
            currentT={currentT}
          />
        ))}
      </div>
    </section>
  );
}
