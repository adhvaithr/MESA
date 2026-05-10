import { NextResponse } from 'next/server';
import { getBackendBaseUrl } from '@/lib/backend';

const BACKEND = getBackendBaseUrl();

export async function GET() {
  try {
    const res = await fetch(`${BACKEND}/api/calls`, { cache: 'no-store' });
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json([], { status: 200 });
  }
}
