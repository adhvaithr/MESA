const PATHS: Record<string, React.ReactNode> = {
  person:  <><circle cx="12" cy="8" r="3.5"/><path d="M5 20c1.5-3.5 4-5 7-5s5.5 1.5 7 5"/></>,
  id:      <><rect x="3.5" y="5" width="17" height="14" rx="2"/><circle cx="9" cy="11" r="2"/><path d="M5.5 16.5c.8-1.6 2-2.4 3.5-2.4s2.7.8 3.5 2.4"/><path d="M14.5 10h4.5M14.5 13h4M14.5 16h3"/></>,
  pin:     <><path d="M12 21s7-6.3 7-12a7 7 0 1 0-14 0c0 5.7 7 12 7 12z"/><circle cx="12" cy="9" r="2.5"/></>,
  box:     <><path d="M3.5 7.5 12 4l8.5 3.5v9L12 20l-8.5-3.5z"/><path d="M3.5 7.5 12 11l8.5-3.5"/><path d="M12 11v9"/></>,
  check:   <><circle cx="12" cy="12" r="8.5"/><path d="m8.5 12.2 2.5 2.5 4.5-5"/></>,
  truck:   <><path d="M2.5 7h11v9h-11z"/><path d="M13.5 10h4l3 3v3h-7z"/><circle cx="6.5" cy="17.5" r="1.8"/><circle cx="17" cy="17.5" r="1.8"/></>,
  play:    <><path d="M7 5.5v13l11-6.5z" fill="currentColor"/></>,
  pause:   <><rect x="6.5" y="5.5" width="3.5" height="13" rx=".7" fill="currentColor" stroke="none"/><rect x="14" y="5.5" width="3.5" height="13" rx=".7" fill="currentColor" stroke="none"/></>,
  skip:    <><path d="m6 6 7 6-7 6z M14 5.5v13" fill="currentColor"/></>,
  rewind:  <><path d="m18 6-7 6 7 6z M10 5.5v13" fill="currentColor"/></>,
  chev:    <><path d="m6 9 6 6 6-6"/></>,
  sun:     <><circle cx="12" cy="12" r="3.5"/><path d="M12 3v1.8M12 19.2V21M3 12h1.8M19.2 12H21M5.6 5.6l1.3 1.3M17.1 17.1l1.3 1.3M5.6 18.4l1.3-1.3M17.1 6.9l1.3-1.3"/></>,
  moon:    <><path d="M20 14a8 8 0 1 1-9.5-10A6.5 6.5 0 0 0 20 14z"/></>,
  dot:     <><circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/></>,
  bolt:    <><path d="m13 3-7 11h5l-1 7 7-11h-5z" fill="currentColor" stroke="none"/></>,
  wave:    <><path d="M3 12h2l1-4 2 8 2-12 2 16 2-10 2 6 2-2h2"/></>,
  arrow:   <><path d="M5 12h14M13 6l6 6-6 6"/></>,
};

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  const s = {
    width: size, height: size, strokeWidth: 1.6,
    stroke: 'currentColor', fill: 'none',
    strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  };
  return <svg viewBox="0 0 24 24" {...s}>{PATHS[name]}</svg>;
}

export function fmtTime(s: number) {
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}
