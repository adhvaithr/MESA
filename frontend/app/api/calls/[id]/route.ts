import { NextResponse } from 'next/server';

const BACKEND = process.env.BACKEND_URL ?? 'http://localhost:8000';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    const res = await fetch(`${BACKEND}/api/calls/${id}`, { cache: 'no-store' });
    if (!res.ok) return NextResponse.json({ error: 'not found' }, { status: 404 });
    const data = await res.json();
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ error: 'backend unreachable' }, { status: 502 });
  }
}
