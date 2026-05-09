# services/tools.py
import re
from typing import Literal, TypedDict

from supabase import Client

# Supabase table names — match schema.sql
USERS_TABLE = "users"
DONORS_TABLE = "donors"
FOOD_BANKS_TABLE = "food_banks"


# ============================================================
# Federal Poverty Level + median-income lookup
# ============================================================

# 2026 HHS Poverty Guidelines (48 contiguous states + DC).
# Verify against aspe.hhs.gov/poverty-guidelines before demo if precision matters.
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

# Median household income by ZIP — seed with demo-relevant zips.
# Source: ACS 5-year estimates. Add more before the demo if your judges'
# test calls might come from elsewhere.
ZIP_MEDIAN_INCOME = {
    "95616": 84_000,   # Davis
    "95618": 110_000,  # West Davis
    "95817": 56_000,   # Sacramento (Oak Park)
    "95820": 52_000,   # Sacramento
    "95823": 61_000,   # South Sacramento
    "95824": 49_000,   # Fruitridge
    "95838": 47_000,   # North Sacramento (Del Paso)
}
DEFAULT_MEDIAN_INCOME = 75_000  # fallback when zip is unknown


# ============================================================
# Type definitions
# ============================================================

class TierResult(TypedDict):
    tier: str        # internal priority band "A" | "B" | "C" (assistant-facing)
    label: str       # human-readable, for the assistant to speak
    fpl_ratio: float # diagnostic — median income / FPL threshold


CallerRole = Literal["recipient", "donor", "food_bank", "unknown"]
RegistrationStatus = Literal["unregistered", "pending", "registered", "rejected"]


class IdentifyCallerResult(TypedDict, total=False):
    role: CallerRole
    registration_status: RegistrationStatus
    user_id: str | None
    donor_id: str | None
    food_bank_id: str | None
    income_tier: str | None  # users.income_tier: "free" | "discount"


# ============================================================
# Helpers
# ============================================================

def normalize_phone(phone: str) -> str:
    """
    Canonical phone for DB lookups and upserts (US-first; extend as needed).
    Always use this for identify + register so the same handset matches one row.
    """
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
    """Federal poverty threshold for a given household size."""
    n = max(1, household_size)
    if n <= 8:
        return FPL_BASE[n]
    return FPL_BASE[8] + (n - 8) * FPL_PER_ADDITIONAL


def assign_income_tier(zip_code: str, household_size: int) -> TierResult:
    """
    Estimates need tier from area median income vs. household FPL.
    No personal income is collected — this is an area-based proxy.
    """
    median = ZIP_MEDIAN_INCOME.get(zip_code, DEFAULT_MEDIAN_INCOME)
    fpl = fpl_threshold(household_size)
    ratio = median / fpl

    if ratio < 1.3:
        return {"tier": "A", "label": "high-priority", "fpl_ratio": ratio}
    if ratio < 2.0:
        return {"tier": "B", "label": "moderate-priority", "fpl_ratio": ratio}
    return {"tier": "C", "label": "general", "fpl_ratio": ratio}


def _priority_band_to_income_tier(band: str) -> Literal["free", "discount"]:
    """Maps internal A/B/C band to DB users.income_tier (schema: free | discount)."""
    return "free" if band == "A" else "discount"


# ============================================================
# Tool: identify_caller
# ============================================================

async def identify_caller(supabase: Client, phone: str) -> IdentifyCallerResult:
    """
    Tool: identify_caller(phone)

    Fires first on every inbound call. Checks users, donors, and food_banks
    tables in that order and returns role + registration status so the
    assistant knows how to proceed.

    Role resolution order:
      1. users      → role: "recipient"
      2. donors     → role: "donor"
      3. food_banks → role: "food_bank"
      4. no match   → role: "unknown", status: "unregistered"

    Registration status mapping:
      - recipient:  onboarded=True → "registered", onboarded=False → "pending"
      - donor:      always "registered" (no status column on donors table)
      - food_bank:  verified → "registered", rejected → "rejected", else → "pending"
      - unknown:    "unregistered"
    """
    normalized = normalize_phone(phone)

    # ── 1. Check users (recipients) ──────────────────────────
    u = (
        supabase.table(USERS_TABLE)
        .select("id, onboarded, income_tier")
        .eq("phone", normalized)
        .maybe_single()
        .execute()
    )
    if u.data:
        row = u.data
        reg: RegistrationStatus = (
            "registered" if row.get("onboarded") else "pending"
        )
        return {
            "role": "recipient",
            "registration_status": reg,
            "user_id": str(row["id"]),
            "donor_id": None,
            "food_bank_id": None,
            "income_tier": row.get("income_tier"),
        }

    # ── 2. Check donors ──────────────────────────────────────
    d = (
        supabase.table(DONORS_TABLE)
        .select("id")
        .eq("phone", normalized)
        .maybe_single()
        .execute()
    )
    if d.data:
        return {
            "role": "donor",
            "registration_status": "registered",
            "user_id": None,
            "donor_id": str(d.data["id"]),
            "food_bank_id": None,
            "income_tier": None,
        }

    # ── 3. Check food_banks ──────────────────────────────────
    f = (
        supabase.table(FOOD_BANKS_TABLE)
        .select("id, status")
        .eq("phone", normalized)
        .maybe_single()
        .execute()
    )
    if f.data:
        row = f.data
        st = str(row.get("status") or "").lower()
        if st == "verified":
            reg = "registered"
        elif st == "rejected":
            reg = "rejected"
        else:
            reg = "pending"
        return {
            "role": "food_bank",
            "registration_status": reg,
            "user_id": None,
            "donor_id": None,
            "food_bank_id": str(row["id"]),
            "income_tier": None,
        }

    # ── 4. No match ──────────────────────────────────────────
    return {
        "role": "unknown",
        "registration_status": "unregistered",
        "user_id": None,
        "donor_id": None,
        "food_bank_id": None,
        "income_tier": None,
    }


# ============================================================
# Tool: register_new_user
# ============================================================

async def register_new_user(
    supabase: Client,
    phone: str,
    zip_code: str,
    household_size: int,
    lang: str = "en",
) -> dict:
    """
    Tool: register_new_user(phone, zip, household_size)

    Fires after the assistant collects zip + household size from a new recipient.
    Upserts a row in `users` with income_tier ∈ {free, discount} per schema.sql.
    Response exposes priority band (A/B/C) for the assistant script.

    Income tier assignment:
      - Uses area median income (by ZIP) vs. household FPL as a proxy.
      - No personal income is collected.
      - Band A (ratio < 1.3) → income_tier: "free"
      - Band B/C (ratio ≥ 1.3) → income_tier: "discount"
    """
    try:
        household_size = int(household_size)
    except (TypeError, ValueError):
        household_size = 1

    zip_code = str(zip_code).strip().split("-")[0][:5]
    phone = normalize_phone(phone)

    tier = assign_income_tier(zip_code, household_size)
    income_tier = _priority_band_to_income_tier(tier["tier"])

    row = {
        "phone": phone,
        "zip": zip_code,
        "household_size": household_size,
        "income_tier": income_tier,
        "lang": lang,
        # onboarded defaults False in DB; set True via a later flow/tool
    }

    result = (
        supabase.table(USERS_TABLE)
        .upsert(row, on_conflict="phone")
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
