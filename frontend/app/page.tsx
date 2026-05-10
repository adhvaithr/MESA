import { CallMonitor } from '@/components/CallMonitor';
import { SAMPLE_META, SAMPLE_TIMELINE } from '@/lib/staticData';
import type { CallMeta, TimelineEvent } from '@/lib/types';
import { getBackendBaseUrl } from '@/lib/backend';

const BACKEND = getBackendBaseUrl();

async function fetchCallData(callId?: string): Promise<{ meta: CallMeta; timeline: TimelineEvent[] } | null> {
  try {
    let id = callId;
    if (!id) {
      const listRes = await fetch(`${BACKEND}/api/calls`, { cache: 'no-store' });
      if (!listRes.ok) return null;
      const list = await listRes.json();
      if (!list.length) return null;
      id = list[0].id;
    }
    const res = await fetch(`${BACKEND}/api/calls/${id}`, { cache: 'no-store' });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ callId?: string }>;
}) {
  const { callId } = await searchParams;
  const data = await fetchCallData(callId);

  const meta: CallMeta = data?.meta ?? SAMPLE_META;
  const timeline: TimelineEvent[] = data?.timeline ?? SAMPLE_TIMELINE;

  return <CallMonitor meta={meta} timeline={timeline} />;
}
