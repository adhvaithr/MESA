# services/tools.py
import asyncio
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Literal, TypedDict

import httpx
from supabase import Client

# ============================================================
# Table names — match schema.sql
# ============================================================
USERS_TABLE = "users"
DONORS_TABLE = "donors"
FOOD_BANKS_TABLE = "food_banks"
LISTINGS_TABLE = "listings"
CLAIMS_TABLE = "claims"
VERIFICATION_QUEUE_TABLE = "verification_queue"

# ============================================================
# Federal Poverty Level + median-income lookup
# ============================================================
FPL_BASE = {
    1: 15_650,
    2: 21_150,
    3: 26_650,
    4: 32_150,
    5: 37_650,
    6: 43_150,
    7: 48_650,
    8: 54_150,
}
FPL_PER_ADDITIONAL = 5_500

ZIP_MEDIAN_INCOME = {
    "95616": 84_000,   # Davis
    "95618": 110_000,  # West Davis
    "95817": 56_000,   # Sacramento (Oak Park)
    "95820": 52_000,   # Sacramento
    "95823": 61_000,   # South Sacramento
    "95824": 49_000,   # Fruitridge
    "95838": 47_000,   # North Sacramento (Del Paso)
}
DEFAULT_MEDIAN_INCOME = 75_000

# ============================================================
# Type definitions
# ============================================================
class TierResult(TypedDict):
    tier: str
    label: str
    fpl_ratio: float

CallerRole = Literal["recipient", "donor", "food_bank", "unknown"]
RegistrationStatus = Literal["unregistered", "pending", "registered", "rejected"]

class IdentifyCallerResult(TypedDict, total=False):
    role: CallerRole
    registration_status: RegistrationStatus
    user_id: str | None
    donor_id: str | None
    food_bank_id: str | None
    income_tier: str | None


# ============================================================
# Shared helpers
# ============================================================

def normalize_phone(phone: str) -> str:
    """Canonical E.164 phone for DB lookups and upserts."""
    p = str(phone).strip()
    digits = re.sub(r"\D", "", p)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    if p.startswith("+") and len(digits) >= 10:
        return f"+{digits}"
    if digits:
        return f"+{digits}"
    return p


def fpl_threshold(household_size: int) -> int:
    """2026 HHS Federal Poverty Level threshold for a given household size."""
    n = max(1, household_size)
    if n <= 8:
        return FPL_BASE[n]
    return FPL_BASE[8] + (n - 8) * FPL_PER_ADDITIONAL


def assign_income_tier(zip_code: str, household_size: int) -> TierResult:
    """Area median income vs. FPL proxy — no personal income collected."""
    median = ZIP_MEDIAN_INCOME.get(zip_code, DEFAULT_MEDIAN_INCOME)
    fpl = fpl_threshold(household_size)
    ratio = median / fpl
    if ratio < 1.3:
        return {"tier": "A", "label": "high-priority", "fpl_ratio": ratio}
    if ratio < 2.0:
        return {"tier": "B", "label": "moderate-priority", "fpl_ratio": ratio}
    return {"tier": "C", "label": "general", "fpl_ratio": ratio}


def _priority_band_to_income_tier(band: str) -> Literal["free", "discount"]:
    return "free" if band == "A" else "discount"


# ============================================================
# Tool 1: identify_caller
# ============================================================

async def identify_caller(supabase: Client, phone: str) -> IdentifyCallerResult:
    """
    Fires first on every inbound call.
    Checks users → donors → food_banks and returns role + registration status.
    """
    normalized = normalize_phone(phone)

    u = (
        supabase.table(USERS_TABLE)
        .select("id, onboarded, income_tier")
        .eq("phone", normalized)
        .maybe_single()
        .execute()
    )
    if u and u.data:
        row = u.data
        reg: RegistrationStatus = "registered" if row.get("onboarded") else "pending"
        return {
            "role": "recipient",
            "registration_status": reg,
            "user_id": str(row["id"]),
            "donor_id": None,
            "food_bank_id": None,
            "income_tier": row.get("income_tier"),
        }

    d = (
        supabase.table(DONORS_TABLE)
        .select("id")
        .eq("phone", normalized)
        .maybe_single()
        .execute()
    )
    if d and d.data:
        return {
            "role": "donor",
            "registration_status": "registered",
            "user_id": None,
            "donor_id": str(d.data["id"]),
            "food_bank_id": None,
            "income_tier": None,
        }

    f = (
        supabase.table(FOOD_BANKS_TABLE)
        .select("id, status")
        .eq("phone", normalized)
        .maybe_single()
        .execute()
    )
    if f and f.data:
        row = f.data
        st = str(row.get("status") or "").lower()
        reg = "registered" if st == "verified" else ("rejected" if st == "rejected" else "pending")
        return {
            "role": "food_bank",
            "registration_status": reg,
            "user_id": None,
            "donor_id": None,
            "food_bank_id": str(row["id"]),
            "income_tier": None,
        }

    return {
        "role": "unknown",
        "registration_status": "unregistered",
        "user_id": None,
        "donor_id": None,
        "food_bank_id": None,
        "income_tier": None,
    }


# ============================================================
# Tool 2: register_new_user
# ============================================================

async def register_new_user(
    supabase: Client,
    phone: str,
    zip_code: str,
    household_size: int,
    lang: str = "en",
) -> dict:
    """
    Fires after assistant collects zip + household size from a new recipient.
    Upserts users row, assigns income_tier from FPL lookup, returns tier.
    """
    try:
        household_size = int(household_size)
    except (TypeError, ValueError):
        household_size = 1

    zip_code = str(zip_code).strip().split("-")[0][:5]
    phone = normalize_phone(phone)

    tier = assign_income_tier(zip_code, household_size)
    income_tier = _priority_band_to_income_tier(tier["tier"])

    result = (
        supabase.table(USERS_TABLE)
        .upsert({
            "phone": phone,
            "zip": zip_code,
            "household_size": household_size,
            "income_tier": income_tier,
            "lang": lang,
        }, on_conflict="phone")
        .execute()
    )

    if not result.data:
        raise RuntimeError(f"users upsert returned no row: {result}")

    return {
        "user_id": str(result.data[0]["id"]),
        "tier": tier["tier"],
        "income_tier": income_tier,
        "label": tier["label"],
        "fpl_ratio": round(tier["fpl_ratio"], 2),
        "registered": True,
    }


# ============================================================
# Tool 3: register_donor
# ============================================================

async def register_donor(
    supabase: Client,
    phone: str,
    name: str,
    business: str,
    zip: str,
    lang: str = "en",
) -> dict:
    """
    Fires after assistant collects donor details from a new donor.
    Inserts donor row. Called if identify_caller returns unknown and
    caller says they want to donate.
    """
    phone = normalize_phone(phone)
    zip = str(zip).strip().split("-")[0][:5]

    existing = (
        supabase.table(DONORS_TABLE)
        .select("id, name")
        .eq("phone", phone)
        .execute()
    )
    if existing.data:
        return {"result": f"Welcome back, {existing.data[0]['name']}. You're already registered as a donor."}

    result = (
        supabase.table(DONORS_TABLE)
        .insert({
            "phone": phone,
            "name": name,
            "address": business,
            "zip": zip,
            "lang": lang,
        })
        .execute()
    )

    if not result.data:
        raise RuntimeError(f"donors insert returned no row: {result}")

    if lang == "es":
        reply = f"Gracias, {name}. Tu restaurante o tienda ha sido registrado en MiComida. Cuando tengas comida sobrante, simplemente llama y dinos qué tienes."
    else:
        reply = f"Thank you, {name}. Your business has been registered with MiComida. Whenever you have surplus food, just call us and tell us what you have."

    return {"result": reply}


# ============================================================
# Tool 4: get_available_food
# ============================================================

async def get_available_food(supabase: Client, zip: str, income_tier: str) -> dict:
    """
    Fires once recipient is registered or identified.
    Queries listings by zip and status, returns list the assistant reads aloud.
    """
    allowed_statuses = ["food_bank_window", "open"] if income_tier == "free" else ["open"]
    now = datetime.now(timezone.utc).isoformat()

    result = (
        supabase.table(LISTINGS_TABLE)
        .select("id, food_type, quantity, pickup_addr, pickup_time, expiry_time")
        .eq("zip", zip)
        .in_("status", allowed_statuses)
        .or_(f"expiry_time.gt.{now},expiry_time.is.null")
        .order("expiry_time", desc=False)
        .limit(3)
        .execute()
    )

    listings = result.data
    if not listings:
        return {"result": "There's no food available near your area right now. Please check back soon."}

    lines = []
    for item in listings:
        pickup = item.get("pickup_time") or "time not specified"
        addr = item.get("pickup_addr") or "address not specified"
        lines.append(f"{item['food_type']}, {item['quantity']}, pickup at {addr} by {pickup}")

    summary = "Here's what's available near you: " + "; ".join(lines)
    summary += ". Would you like to claim any of these?"
    return {"result": summary}


# ============================================================
# Tool 5: save_food_listing
# ============================================================

async def save_food_listing(
    supabase: Client,
    food_type: str,
    quantity: str,
    pickup_time: str,
    zip_code: str,
    donor_phone: str,
) -> dict:
    """
    Fires the moment assistant has all 4 fields from a donor.
    Inserts listing to Supabase, returns listing_id.
    """
    donor_phone = normalize_phone(donor_phone)
    zip_code = str(zip_code).strip().split("-")[0][:5]

    parsed_pickup: str | None = None
    if pickup_time:
        try:
            parsed_pickup = datetime.fromisoformat(str(pickup_time)).isoformat()
        except ValueError:
            parsed_pickup = str(pickup_time).strip()

    result = (
        supabase.table(LISTINGS_TABLE)
        .insert({
            "donor_phone": donor_phone,
            "food_type": str(food_type).strip(),
            "quantity": str(quantity).strip(),
            "zip": zip_code,
            "pickup_time": parsed_pickup,
            "status": "available",
        })
        .execute()
    )

    if not result.data:
        raise RuntimeError(f"listings insert returned no row: {result}")

    listing = result.data[0]
    return {
        "listing_id": str(listing["id"]),
        "donor_phone": donor_phone,
        "food_type": food_type,
        "quantity": quantity,
        "zip": zip_code,
        "pickup_time": parsed_pickup,
        "status": listing["status"],
    }


# ============================================================
# Tool 6: notify_food_banks
# ============================================================

async def notify_food_banks(supabase: Client, listing_id: str, zip: str) -> dict:
    """
    Fires immediately after save_food_listing returns a listing_id.
    Queries verified food banks in zip, triggers outbound Vapi calls
    to each in their preferred language.
    """
    result = (
        supabase.table(FOOD_BANKS_TABLE)
        .select("phone, preferred_lang, name")
        .eq("zip", zip)
        .eq("status", "verified")
        .execute()
    )
    food_banks = result.data
    if not food_banks:
        return {"result": "No verified food banks found in this area."}

    listing = (
        supabase.table(LISTINGS_TABLE)
        .select("food_type, quantity, pickup_time, pickup_addr")
        .eq("id", listing_id)
        .single()
        .execute()
    )
    if not listing.data:
        return {"result": "Listing not found."}

    l = listing.data
    food_desc = f"{l['quantity']} {l['food_type']}"
    pickup = l.get("pickup_time") or "time not specified"
    addr = l.get("pickup_addr") or "address not specified"

    supabase.table(LISTINGS_TABLE).update({"status": "food_bank_window"}).eq("id", listing_id).execute()

    vapi_key = os.getenv("VAPI_API_KEY")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")
    phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID")
    called = []

    async with httpx.AsyncClient() as client:
        for bank in food_banks:
            lang = bank.get("preferred_lang", "en")
            if lang == "es":
                prompt = f"Estás llamando en nombre de MiComida. Un donante ha listado {food_desc} para recoger en {addr} a las {pickup}. ¿Puede su banco de alimentos reclamar esta donación? Si es así, confirme ahora."
            else:
                prompt = f"You are calling on behalf of MiComida. A donor has listed {food_desc} for pickup at {addr} at {pickup}. Can your food bank claim this donation? If yes, please confirm now."

            await client.post(
                "https://api.vapi.ai/call/phone",
                headers={"Authorization": f"Bearer {vapi_key}"},
                json={
                    "assistantId": assistant_id,
                    "assistantOverrides": {
                        "systemPrompt": prompt,
                        "variable": {"listing_id": listing_id},
                    },
                    "phoneNumberId": phone_number_id,
                    "customer": {"number": bank["phone"]},
                },
            )

            supabase.table("alert_log").insert({
                "listing_id": listing_id,
                "food_bank_phone": bank["phone"],
            }).execute()

            called.append(bank["name"])

    return {"result": f"Notified {len(called)} food bank(s): {', '.join(called)}. They are being called now."}


# ============================================================
# Tool 7: claim_food_listing
# ============================================================

async def _lookup_claimer(supabase: Client, phone: str) -> dict:
    """Returns claimer role + id — food_bank (verified only) or recipient."""
    fb = (
        supabase.table(FOOD_BANKS_TABLE)
        .select("id, name")
        .eq("phone", phone)
        .eq("status", "verified")
        .execute()
    )
    if fb.data:
        return {"role": "food_bank", "id": fb.data[0]["id"], "name": fb.data[0]["name"]}

    user = (
        supabase.table(USERS_TABLE)
        .select("id")
        .eq("phone", phone)
        .execute()
    )
    if user.data:
        return {"role": "recipient", "id": user.data[0]["id"], "name": "a community member"}

    return {"role": "unknown", "id": None, "name": "someone"}


async def _notify_donor_of_claim(donor: dict, listing: dict, claimer: dict) -> None:
    """Fire-and-forget outbound Vapi call to tell the donor their listing was claimed."""
    prompt = (
        f"You are calling on behalf of the food rescue service. "
        f"The donor at {donor['address']} listed {listing['quantity']} "
        f"{listing['food_type']} for pickup at {listing['pickup_time']}. "
        f"Tell them {claimer['name']} has claimed the listing and will pick it up. "
        f"Thank them, confirm the pickup time, and end the call. Be brief — under 30 seconds."
    )
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                "https://api.vapi.ai/call/phone",
                headers={"Authorization": f"Bearer {os.environ['VAPI_API_KEY']}"},
                json={
                    "phoneNumberId": os.environ["VAPI_PHONE_NUMBER_ID"],
                    "assistantId": os.environ["VAPI_ASSISTANT_ID"],
                    "customer": {"number": donor["phone"]},
                    "assistantOverrides": {"systemPrompt": prompt},
                },
            )
    except Exception as e:
        print(f"Donor notification failed for listing {listing.get('id')}: {e}")


async def claim_food_listing(supabase: Client, listing_id: str, phone: str) -> dict:
    """
    Fires when recipient or food bank confirms they want a listing.
    Inserts claim row, marks listing claimed, notifies donor via outbound call.
    """
    phone = normalize_phone(phone)
    listing_id = str(listing_id).strip()

    update = (
        supabase.table(LISTINGS_TABLE)
        .update({"status": "claimed"})
        .eq("id", listing_id)
        .in_("status", ["available", "food_bank_window", "open"])
        .execute()
    )

    if not update.data:
        check = supabase.table(LISTINGS_TABLE).select("status").eq("id", listing_id).execute()
        if not check.data:
            return {"success": False, "reason": "listing_not_found"}
        return {"success": False, "reason": "already_claimed"}

    listing = update.data[0]
    claimer = await _lookup_claimer(supabase, phone)

    claim = (
        supabase.table(CLAIMS_TABLE)
        .insert({
            "listing_id": listing_id,
            "claimer_phone": phone,
            "claimer_type": claimer["role"],
        })
        .execute()
    )

    donor = (
        supabase.table(DONORS_TABLE)
        .select("phone, name, address")
        .eq("phone", listing["donor_phone"])
        .single()
        .execute()
    ).data

    if donor:
        asyncio.create_task(_notify_donor_of_claim(donor=donor, listing=listing, claimer=claimer))

    return {
        "success": True,
        "claim_id": str(claim.data[0]["id"]) if claim.data else None,
        "food_type": listing.get("food_type"),
        "quantity": listing.get("quantity"),
        "pickup_time": listing.get("pickup_time"),
        "donor_phone": donor["phone"] if donor else None,
    }


# ============================================================
# Tool 8: register_food_bank
# ============================================================

async def register_food_bank(
    supabase: Client,
    phone: str,
    name: str,
    ein: str,
    address: str,
    zip_code: str,
    lang: str = "en",
) -> dict:
    """
    Fires after assistant collects contact info from a food bank caller,
    before verify_organization runs. Inserts food_bank row with status pending.
    """
    result = (
        supabase.table(FOOD_BANKS_TABLE)
        .upsert({
            "phone": normalize_phone(phone),
            "name": str(name).strip(),
            "ein": str(ein).strip(),
            "address": str(address).strip(),
            "zip": str(zip_code).strip().split("-")[0][:5],
            "preferred_lang": str(lang).strip() or "en",
            "status": "pending",
        }, on_conflict="phone")
        .execute()
    )

    if not result.data:
        raise RuntimeError(f"food_banks upsert returned no row: {result}")

    food_bank = result.data[0]
    return {
        "food_bank_id": str(food_bank["id"]),
        "name": name,
        "ein": ein,
        "address": address,
        "zip": zip_code,
        "lang": lang,
        "status": food_bank["status"],
    }