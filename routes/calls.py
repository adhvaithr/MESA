import asyncio
import logging
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from supabase import create_client

router = APIRouter(prefix="/api")
logger = logging.getLogger("uvicorn.error")
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY"),
)


@router.get("/calls")
async def list_calls():
    try:
        resp = await asyncio.to_thread(
            lambda: supabase.table("calls")
            .select("id, started_at, ended_at, duration_ms, caller_number, agent_name, tool_call_count, intent")
            .order("started_at", desc=True)
            .limit(50)
            .execute()
        )
        return resp.data or []
    except Exception as exc:
        logger.exception("list_calls failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/calls/{call_id}")
async def get_call(call_id: str):
    try:
        call_resp = await asyncio.to_thread(
            lambda: supabase.table("calls")
            .select("*")
            .eq("id", call_id)
            .maybe_single()
            .execute()
        )
        call_row = call_resp.data if call_resp else None
        if not call_row:
            raise HTTPException(status_code=404, detail="Call not found")

        events_resp = await asyncio.to_thread(
            lambda: supabase.table("call_events")
            .select("*")
            .eq("call_id", call_id)
            .order("t")
            .execute()
        )
        events = events_resp.data or []

        total_duration = 0.0
        if call_row.get("duration_ms"):
            total_duration = call_row["duration_ms"] / 1000
        elif call_row.get("ended_at") and call_row.get("started_at"):
            try:
                s = datetime.fromisoformat(call_row["started_at"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(call_row["ended_at"].replace("Z", "+00:00"))
                total_duration = (e - s).total_seconds()
            except Exception:
                pass

        meta = {
            "callId": call_row["id"],
            "startedAt": call_row.get("started_at", ""),
            "agent": call_row.get("agent_name", "Alex · MESA"),
            "callerNumber": call_row.get("caller_number", "Unknown"),
            "totalDuration": total_duration,
            "channel": "Inbound · Voice",
            "intent": call_row.get("intent", ""),
            "language": "English",
            "region": call_row.get("region", ""),
        }

        timeline = []
        for ev in events:
            kind = ev.get("kind")
            if kind == "turn":
                timeline.append({
                    "t": ev["t"],
                    "kind": "turn",
                    "speaker": ev.get("speaker", "agent"),
                    "text": ev.get("text", ""),
                })
            elif kind == "tool":
                timeline.append({
                    "t": ev["t"],
                    "kind": "tool",
                    "name": ev.get("tool_name", ""),
                    "args": ev.get("args") or {},
                    "result": ev.get("result") or {},
                    "durationMs": ev.get("duration_ms") or 0,
                    "writes": ev.get("writes") or [],
                })
            elif kind == "classify":
                timeline.append({
                    "t": ev["t"],
                    "kind": "classify",
                    "callerType": ev.get("caller_type", ""),
                    "confidence": ev.get("confidence", 1.0),
                    "rationale": ev.get("rationale", ""),
                    "writes": ev.get("writes") or [],
                })

        return {"meta": meta, "timeline": timeline}

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get_call failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
