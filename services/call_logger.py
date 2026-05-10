import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("uvicorn.error")


def _strip_test(name: str) -> str:
    return name[5:] if name.startswith("test_") else name


def _caller_status(result: dict) -> str:
    role = result.get("role", "unknown")
    reg = result.get("registration_status", "unregistered")
    if reg == "unregistered":
        return "New caller"
    return f"{role.replace('_', ' ').title()} · {reg}"


def _matched_record(result: dict) -> str:
    if result.get("user"):
        return str(result["user"].get("id", "—"))
    if result.get("donor"):
        return str(result["donor"].get("id", "—"))
    if result.get("food_bank"):
        return str(result["food_bank"].get("id", "—"))
    return "—"


def _now_display() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


TOOL_WRITE_MAP = {
    "identify_caller": lambda args, result: [
        {"block": "caller", "field": "phone",          "value": args.get("phone", "")},
        {"block": "caller", "field": "status",         "value": _caller_status(result)},
        {"block": "caller", "field": "matched_record", "value": _matched_record(result)},
    ],
    "register_new_user": lambda args, result: [
        {"block": "user", "field": "user_id",        "value": result.get("user_id", "")},
        {"block": "user", "field": "phone",          "value": args.get("phone", "")},
        {"block": "user", "field": "zip_code",       "value": args.get("zip_code", "")},
        {"block": "user", "field": "household_size", "value": str(args.get("household_size", ""))},
        {"block": "user", "field": "language",       "value": args.get("lang", "en")},
        {"block": "user", "field": "created_at",     "value": _now_display()},
    ],
    "register_donor": lambda args, result: [
        {"block": "user", "field": "phone",    "value": args.get("phone", "")},
        {"block": "user", "field": "name",     "value": args.get("name", "")},
        {"block": "user", "field": "zip_code", "value": args.get("zip", "")},
    ],
    "register_food_bank": lambda args, result: [
        {"block": "user", "field": "user_id",  "value": result.get("food_bank_id", "")},
        {"block": "user", "field": "phone",    "value": args.get("phone", "")},
        {"block": "user", "field": "zip_code", "value": args.get("zip_code", "")},
    ],
    "get_nearby_food_banks": lambda args, result: [
        {"block": "nearby", "field": "search_zip", "value": args.get("zip", "")},
        {"block": "nearby", "field": "results",    "value": result.get("nearby_food_banks", [])},
    ],
    "get_available_food": lambda args, result: [
        {"block": "available", "field": "search_zip",  "value": args.get("zip", "")},
        {"block": "available", "field": "income_tier", "value": args.get("income_tier", "")},
        {"block": "available", "field": "listings",    "value": result.get("listings_raw", [])},
    ],
    "claim_food_listing": lambda args, result: [
        {"block": "claim", "field": "listing_id",  "value": result.get("listing_id", "")},
        {"block": "claim", "field": "food_type",   "value": result.get("food_type", "")},
        {"block": "claim", "field": "pickup_hint", "value": args.get("pickup_hint", "")},
        {"block": "claim", "field": "phone",       "value": args.get("phone", "")},
    ],
    "claim_food_listing_by_id": lambda args, result: [
        {"block": "claim", "field": "listing_id", "value": result.get("listing_id", "")},
        {"block": "claim", "field": "food_type",  "value": result.get("food_type", "")},
        {"block": "claim", "field": "phone",      "value": args.get("phone", "")},
    ],
    "request_food_from_food_bank": lambda args, result: [
        {"block": "request", "field": "request_id",      "value": result.get("request_id") or ""},
        {"block": "request", "field": "recipient_phone", "value": args.get("recipient_phone", "")},
        {"block": "request", "field": "listing_id",      "value": args.get("listing_id", "")},
        {"block": "request", "field": "food_bank",       "value": result.get("food_bank_name", "")},
        {"block": "request", "field": "food_bank_phone", "value": args.get("food_bank_phone", "")},
        {"block": "request", "field": "pickup_window",   "value": str(result.get("pickup_time") or "")},
    ],
    "verify_organization": lambda args, result: [
        {"block": "caller", "field": "status",
         "value": "Verified" if result.get("all_passed") else "Verification Failed"},
    ],
}


def compute_t(message: dict) -> float:
    call = message.get("call", {})
    started_at_str = call.get("startedAt")
    if not started_at_str:
        return 0.0
    try:
        started = datetime.fromisoformat(started_at_str.replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
    except Exception:
        return 0.0


async def log_call_start(supabase, message: dict) -> None:
    try:
        call = message.get("call", {})
        call_id = call.get("id")
        if not call_id:
            return
        started_at = call.get("startedAt", datetime.now(timezone.utc).isoformat())
        await asyncio.to_thread(
            lambda: supabase.table("calls").upsert(
                {"id": call_id, "started_at": started_at},
                on_conflict="id",
            ).execute()
        )
    except Exception as exc:
        logger.exception("call_logger: log_call_start failed: %s", exc)


async def log_call_end(supabase, message: dict) -> None:
    try:
        call = message.get("call", {})
        call_id = call.get("id")
        if not call_id:
            return

        ended_at = call.get("endedAt")
        started_at = call.get("startedAt")
        duration_ms = None
        if ended_at and started_at:
            try:
                s = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                e = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                duration_ms = int((e - s).total_seconds() * 1000)
            except Exception:
                pass

        customer = call.get("customer", {})
        caller_number = customer.get("number") if isinstance(customer, dict) else None

        count_resp = await asyncio.to_thread(
            lambda: supabase.table("call_events")
            .select("id", count="exact")
            .eq("call_id", call_id)
            .eq("kind", "tool")
            .execute()
        )
        tool_call_count = count_resp.count or 0

        await asyncio.to_thread(
            lambda: supabase.table("calls").update({
                "ended_at": ended_at,
                "duration_ms": duration_ms,
                "caller_number": caller_number,
                "tool_call_count": tool_call_count,
            }).eq("id", call_id).execute()
        )
    except Exception as exc:
        logger.exception("call_logger: log_call_end failed: %s", exc)


async def log_turn_event(supabase, call_id: str, t: float, speaker: str, text: str) -> None:
    try:
        await asyncio.to_thread(
            lambda: supabase.table("call_events").insert({
                "call_id": call_id,
                "t": t,
                "kind": "turn",
                "speaker": speaker,
                "text": text,
            }).execute()
        )
    except Exception as exc:
        logger.exception("call_logger: log_turn_event failed: %s", exc)


async def log_tool_event(
    supabase,
    call_id: str,
    t: float,
    tool_name: str,
    args: dict,
    result: dict,
    duration_ms: int,
) -> None:
    try:
        bare_name = _strip_test(tool_name)
        write_fn = TOOL_WRITE_MAP.get(bare_name)
        writes = write_fn(args, result) if write_fn else []

        await asyncio.to_thread(
            lambda: supabase.table("call_events").insert({
                "call_id": call_id,
                "t": t,
                "kind": "tool",
                "tool_name": bare_name,
                "args": args,
                "result": result,
                "duration_ms": duration_ms,
                "writes": writes,
            }).execute()
        )

        # Emit a classify event when identify_caller reveals the caller's role
        if bare_name == "identify_caller":
            role = result.get("role", "unknown")
            if role and role != "unknown":
                await log_classify_event(supabase, call_id, t + 1.4, role)

    except Exception as exc:
        logger.exception("call_logger: log_tool_event failed: %s", exc)


async def log_classify_event(supabase, call_id: str, t: float, role: str) -> None:
    role_map = {"recipient": "receiver", "donor": "donor", "food_bank": "foodbank"}
    caller_type = role_map.get(role, role)
    rationale = f"Identified as {role.replace('_', ' ')} via database lookup."
    try:
        await asyncio.to_thread(
            lambda: supabase.table("call_events").insert({
                "call_id": call_id,
                "t": t,
                "kind": "classify",
                "caller_type": caller_type,
                "confidence": 1.0,
                "rationale": rationale,
                "writes": [{"block": "type", "field": "caller_type", "value": caller_type}],
            }).execute()
        )
    except Exception as exc:
        logger.exception("call_logger: log_classify_event failed: %s", exc)
