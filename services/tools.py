# services/tools.py
import asyncio
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Literal, TypedDict

import httpx
from supabase import Client
from vapi import Vapi

try:
    from firecrawl import Firecrawl
except ImportError:  # pragma: no cover - runtime dependency in deployment.
    Firecrawl = None  # type: ignore[assignment]

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
    user: dict[str, object]
    donor: dict[str, object]
    food_bank: dict[str, object]


class VerificationCheckResult(TypedDict):
    passed: bool
    reason: str
    latency_ms: int


CHECK_TIMEOUT_SECONDS = 1.2
OVERALL_VERIFY_TIMEOUT_SECONDS = 2.5
logger = logging.getLogger("uvicorn.error")


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


def normalize_ein(ein: str) -> str:
    """Returns EIN in NN-NNNNNNN format when possible."""
    digits = re.sub(r"\D", "", str(ein or ""))
    if len(digits) != 9:
        return str(ein or "").strip()
    return f"{digits[:2]}-{digits[2:]}"


def _ein_digits_if_valid(ein: str | None) -> str | None:
    """Nine-digit EIN only, or None if missing/invalid."""
    if ein is None:
        return None
    digits = re.sub(r"\D", "", str(ein).strip())
    return digits if len(digits) == 9 else None


def _ein_display_from_digits(digits: str) -> str:
    return f"{digits[:2]}-{digits[2:]}"


def _normalize_zip5(zipcode: str) -> str | None:
    """US ZIP: first five digits, or None if not usable."""
    z = re.sub(r"\D", "", str(zipcode or "").strip())
    return z[:5] if len(z) >= 5 else None


_US_STATE_NAMES: dict[str, str] = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}


def _normalize_us_state_abbrev(state: str) -> str | None:
    """Return two-letter USPS code, or None."""
    raw = str(state or "").strip()
    if not raw:
        return None
    if len(raw) == 2 and raw.isalpha():
        return raw.upper()
    return _US_STATE_NAMES.get(raw.lower())


def _normalize_name_for_match(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9 ]", " ", str(name or "").lower())
    collapsed = re.sub(r"\s+", " ", cleaned).strip()
    suffixes = {"inc", "llc", "corp", "co", "foundation", "the"}
    tokens = [t for t in collapsed.split(" ") if t and t not in suffixes]
    return " ".join(tokens)


def _name_match_score(left: str, right: str) -> float:
    l_tokens = set(_normalize_name_for_match(left).split())
    r_tokens = set(_normalize_name_for_match(right).split())
    if not l_tokens or not r_tokens:
        return 0.0
    overlap = l_tokens.intersection(r_tokens)
    return len(overlap) / max(len(l_tokens), len(r_tokens))


def _summary_from_checks(
    org_name: str,
    ein_match: bool,
    address_valid: bool,
    web_presence: bool,
    failures: list[str],
    ein_reason: str,
    address_reason: str,
    web_reason: str,
) -> str:
    reasons = (
        f"ein={ein_reason}; "
        f"address={address_reason}; "
        f"web={web_reason}"
    )
    if ein_match and address_valid and web_presence:
        return (
            f"{org_name} verification passed: EIN, address, and web presence checks all succeeded. "
            f"Evidence: {reasons}"
        )
    return f"{org_name} verification failed checks: {', '.join(failures)}. Evidence: {reasons}"


async def _run_check_with_timeout(
    check_name: str,
    fn,
    timeout_seconds: float,
) -> VerificationCheckResult:
    started = time.perf_counter()
    try:
        passed, reason = await asyncio.wait_for(fn(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        passed, reason = False, f"{check_name} timed out"
    except Exception as exc:
        passed, reason = False, f"{check_name} error: {exc}"
    latency_ms = int((time.perf_counter() - started) * 1000)
    return {"passed": bool(passed), "reason": str(reason), "latency_ms": latency_ms}


async def _check_ein_by_organization_id(
    client: httpx.AsyncClient,
    org_name: str,
    ein_digits: str,
    state_abbrev: str | None,
    zip5: str | None,
    *,
    skip_zip_verification: bool = False,
) -> tuple[bool, str]:
    """Verify a known EIN against ProPublica org profile + caller name (and optional state/ZIP)."""
    pp = await client.get(
        f"https://projects.propublica.org/nonprofits/api/v2/organizations/{ein_digits}.json"
    )
    if pp.status_code == 404:
        return False, "EIN not found in nonprofit registry"
    if pp.status_code >= 400:
        return False, f"EIN registry failed ({pp.status_code})"
    payload = pp.json()
    org = payload.get("organization") or {}
    external_name = org.get("name") or ""
    if not external_name:
        return False, "EIN registry returned no organization"
    if _name_match_score(org_name, external_name) < 0.5:
        return False, f"EIN found but name mismatch ({external_name})"

    if state_abbrev:
        reg_state = str(org.get("state") or "").strip().upper()
        if reg_state and reg_state != state_abbrev:
            return False, f"EIN nonprofit is in {reg_state}, expected {state_abbrev}"

    if not skip_zip_verification and zip5:
        reg_zip_raw = org.get("zip") or org.get("ZIP") or org.get("zipcode") or ""
        reg_zip = re.sub(r"\D", "", str(reg_zip_raw))[:5]
        if len(reg_zip) == 5 and reg_zip != zip5:
            return False, f"EIN nonprofit ZIP {reg_zip} does not match expected {zip5}"

    return True, f"EIN {_ein_display_from_digits(ein_digits)} verified as {external_name}"


async def _check_ein_by_propublica_name_search(
    client: httpx.AsyncClient,
    org_name: str,
    _city: str,
    state_abbrev: str | None,
    zip5: str | None,
) -> tuple[bool, str]:
    """Resolve nonprofit by name when no EIN is available (ProPublica search: name only)."""
    name_part = org_name.strip()
    if not name_part:
        return False, "No organization name for nonprofit lookup"

    q = f'"{name_part}"' if len(name_part.split()) > 1 else name_part
    search_params: dict[str, str | int] = {"q": q, "page": 0, "c_code[id]": 3}

    response = await client.get(
        "https://projects.propublica.org/nonprofits/api/v2/search.json",
        params=search_params,
    )
    if response.status_code >= 400:
        return False, f"Nonprofit name search failed ({response.status_code})"
    data = response.json()
    organizations = data.get("organizations") or []
    if not organizations:
        return False, "No nonprofit matches for organization name"

    best: tuple[float, dict] | None = None
    for row in organizations[:25]:
        ext_name = str(row.get("name") or "")
        if not ext_name:
            continue
        score = _name_match_score(org_name, ext_name)
        if best is None or score > best[0]:
            best = (score, row)

    if best is None or best[0] < 0.42:
        return False, "No confident nonprofit match by organization name"

    ein_digits = str(best[1].get("ein") or "").replace("-", "")
    digits_only = re.sub(r"\D", "", ein_digits)
    if len(digits_only) != 9:
        return False, "Search hit had no usable EIN"

    ok, detail = await _check_ein_by_organization_id(
        client,
        org_name,
        digits_only,
        state_abbrev,
        zip5,
        skip_zip_verification=True,
    )
    if ok:
        return True, f"Resolved by name: {detail}"
    return False, f"Name search candidate failed verification ({detail})"


async def _check_ein_external(
    org_name: str,
    city: str,
    state_abbrev: str | None,
    zip5: str | None,
    ein: str | None,
) -> tuple[bool, str]:
    """
    Prefer a concrete EIN (from DB or optional override). If none, resolve via
    ProPublica search by organization name only. After a name-search hit, the
    resolved EIN profile is still checked against state, but ZIP is not compared
    on that path (IRS/ProPublica ZIP can differ from caller ZIP).
    """
    digits = _ein_digits_if_valid(ein)
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        if digits:
            return await _check_ein_by_organization_id(client, org_name, digits, state_abbrev, zip5)
        return await _check_ein_by_propublica_name_search(client, org_name, city, state_abbrev, zip5)


async def _check_address_external(
    address: str,
    city: str,
    state_abbrev: str | None,
    zip5: str | None,
) -> tuple[bool, str]:
    api_key = os.getenv("GOOGLE_MAPS_API_KEY") or os.getenv("GOOGLE_GEOCODING_API_KEY")
    if not api_key:
        return False, "Missing GOOGLE_MAPS_API_KEY for geocode verification"

    parts = [address.strip(), city.strip()]
    if state_abbrev:
        parts.append(state_abbrev)
    if zip5:
        parts.append(zip5)
    query = ", ".join(p for p in parts if p)
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS) as client:
        response = await client.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": query, "key": api_key},
        )
        if response.status_code >= 400:
            return False, f"Geocode failed ({response.status_code})"
        payload = response.json()
        if payload.get("status") != "OK":
            return False, f"Geocode status {payload.get('status')}"
        results = payload.get("results") or []
        if not results:
            return False, "Geocode returned no matches"

        top = results[0]
        formatted = str(top.get("formatted_address") or "").lower()
        city_norm = city.strip().lower()
        components = top.get("address_components") or []
        component_text = " ".join(
            " ".join(comp.get("types", [])) + " " + comp.get("long_name", "")
            for comp in components
        ).lower()
        has_city_match = city_norm and (city_norm in formatted or city_norm in component_text)
        if not has_city_match:
            return False, "Geocode resolved but city did not match"

        if state_abbrev:
            state_short = None
            for comp in components:
                types = comp.get("types") or []
                if "administrative_area_level_1" in types:
                    state_short = str(comp.get("short_name") or "").strip().upper()
                    break
            if state_short and state_short != state_abbrev:
                return False, f"Geocode state is {state_short}, expected {state_abbrev}"

        if zip5:
            postal = None
            for comp in components:
                types = comp.get("types") or []
                if "postal_code" in types:
                    postal = re.sub(r"\D", "", str(comp.get("long_name") or comp.get("short_name") or ""))[:5]
                    break
            if postal and len(postal) == 5 and postal != zip5:
                return False, f"Geocode ZIP {postal} does not match expected {zip5}"

        return True, "Address validated by geocode"


async def _check_web_presence_external(
    org_name: str,
    city: str,
    state_abbrev: str | None,
    zip5: str | None,
) -> tuple[bool, str]:
    loc_parts = [org_name, city]
    if state_abbrev:
        loc_parts.append(state_abbrev)
    if zip5:
        loc_parts.append(zip5)
    query = " ".join(p for p in loc_parts if p).strip() + " food bank"
    firecrawl_api_key = os.getenv("FIRECRAWL_API_KEY")

    if not firecrawl_api_key:
        return False, "Missing FIRECRAWL_API_KEY for web presence verification"
    if Firecrawl is None:
        return False, "Missing firecrawl-py dependency. Install with: pip install firecrawl-py"

    firecrawl = Firecrawl(api_key=firecrawl_api_key)
    try:
        data = await asyncio.to_thread(lambda: firecrawl.search(query=query, limit=5))
    except TypeError:
        data = await asyncio.to_thread(lambda: firecrawl.search(query, limit=5))

    results = []
    if isinstance(data, dict):
        results = data.get("web") or data.get("data") or data.get("results") or []
    else:
        results = getattr(data, "web", None) or getattr(data, "data", None) or []

    if not results:
        return False, "No web results from Firecrawl"

    name_match_min = 0.25
    best_name_score = 0.0
    evidence: list[str] = []

    for raw in results[:5]:
        if isinstance(raw, dict):
            title = str(raw.get("title") or "")
            snippet = str(raw.get("description") or raw.get("snippet") or raw.get("markdown") or "")
            url = str(raw.get("url") or raw.get("source") or "")
        else:
            title = str(getattr(raw, "title", "") or "")
            snippet = str(
                getattr(raw, "description", "")
                or getattr(raw, "snippet", "")
                or getattr(raw, "markdown", "")
                or ""
            )
            url = str(getattr(raw, "url", "") or getattr(raw, "source", "") or "")

        combined = f"{title} {snippet} {url}".lower()
        name_score = _name_match_score(org_name, combined)
        best_name_score = max(best_name_score, name_score)

        if len(evidence) < 2 and name_score >= name_match_min:
            short_title = title[:90] if title else url[:90]
            evidence.append(short_title)

    evidence_text = ", ".join(evidence) if evidence else "no snippets"
    if best_name_score >= name_match_min:
        return (
            True,
            f"Web listings include the organization name (token_overlap={best_name_score:.2f}); evidence={evidence_text}",
        )
    return (
        False,
        f"No listing contains a usable match for the organization name (best_token_overlap={best_name_score:.2f}); "
        f"snippets={evidence_text}",
    )


async def verify_organization(
    supabase: Client,
    org_name: str,
    address: str,
    city: str,
    state: str,
    zipcode: str,
    phone: str,
    ein: str | None = None,
) -> dict:
    """
    Runs EIN + geocode + web checks in parallel and writes to verification_queue.
    Also updates food_banks.status immediately for voice-agent flows.

    ``org_name`` is required (as collected from the caller). It is used for EIN
    and web checks, not replaced by the name on file.

    ``state`` and ``zipcode`` disambiguate same-named cities (e.g. Fremont, CA vs NE).

    EIN is optional: use the value stored on ``food_banks`` for this phone, or
    an optional override. If no valid EIN is available, ProPublica name search
    uses ``org_name``, city, state, and ZIP to resolve a nonprofit.
    """
    normalized_phone = normalize_phone(phone)
    org_name_clean = str(org_name).strip()
    if not org_name_clean:
        return {
            "all_passed": False,
            "failed_checks": ["organization_name"],
            "summary": "Organization name is required for verification.",
        }

    address = str(address).strip()
    city = str(city).strip()

    state_abbrev = _normalize_us_state_abbrev(state)
    if not state_abbrev:
        return {
            "all_passed": False,
            "failed_checks": ["state"],
            "summary": "Valid U.S. state is required (two-letter code, e.g. CA, or full name).",
        }

    zip5 = _normalize_zip5(zipcode)
    if not zip5:
        return {
            "all_passed": False,
            "failed_checks": ["zipcode"],
            "summary": "Valid 5-digit U.S. ZIP code is required.",
        }

    food_bank_lookup = await asyncio.to_thread(
        lambda: (
            supabase.table(FOOD_BANKS_TABLE)
            .select("id, zip, ein")
            .eq("phone", normalized_phone)
            .maybe_single()
            .execute()
        )
    )
    row = getattr(food_bank_lookup, "data", None) if food_bank_lookup is not None else None
    if not row:
        return {
            "all_passed": False,
            "failed_checks": ["food_bank_registration"],
            "summary": "Food bank must be registered before verification.",
        }

    food_bank_id = str(row["id"])

    db_zip = _normalize_zip5(str(row.get("zip") or ""))
    if db_zip and db_zip != zip5:
        return {
            "all_passed": False,
            "failed_checks": ["zipcode_mismatch"],
            "summary": "ZIP code does not match the food bank registration on file.",
        }

    param_digits = _ein_digits_if_valid(ein)
    db_digits = _ein_digits_if_valid(row.get("ein"))
    ein_digits_for_check = param_digits or db_digits
    ein_for_check: str | None = _ein_display_from_digits(ein_digits_for_check) if ein_digits_for_check else None
    ein_resolution = (
        "parameter" if param_digits else ("database" if db_digits else "name_lookup")
    )

    try:
        checks = await asyncio.wait_for(
            asyncio.gather(
                _run_check_with_timeout(
                    "ein_match",
                    lambda n=org_name_clean, c=city, s=state_abbrev, z=zip5, e=ein_for_check: _check_ein_external(
                        n, c, s, z, e
                    ),
                    CHECK_TIMEOUT_SECONDS,
                ),
                _run_check_with_timeout(
                    "address_valid",
                    lambda: _check_address_external(
                        address=address,
                        city=city,
                        state_abbrev=state_abbrev,
                        zip5=zip5,
                    ),
                    CHECK_TIMEOUT_SECONDS,
                ),
                _run_check_with_timeout(
                    "web_presence",
                    lambda: _check_web_presence_external(
                        org_name=org_name_clean,
                        city=city,
                        state_abbrev=state_abbrev,
                        zip5=zip5,
                    ),
                    CHECK_TIMEOUT_SECONDS,
                ),
            ),
            timeout=OVERALL_VERIFY_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        checks = [
            {"passed": False, "reason": "ein_match timed out by global budget", "latency_ms": int(OVERALL_VERIFY_TIMEOUT_SECONDS * 1000)},
            {"passed": False, "reason": "address_valid timed out by global budget", "latency_ms": int(OVERALL_VERIFY_TIMEOUT_SECONDS * 1000)},
            {"passed": False, "reason": "web_presence timed out by global budget", "latency_ms": int(OVERALL_VERIFY_TIMEOUT_SECONDS * 1000)},
        ]

    ein_result, address_result, web_result = checks
    ein_match = ein_result["passed"]
    address_valid = address_result["passed"]
    web_presence = web_result["passed"]
    failed_checks: list[str] = []
    if not ein_match:
        failed_checks.append("ein_match")
    if not address_valid:
        failed_checks.append("address_valid")
    if not web_presence:
        failed_checks.append("web_presence")

    all_passed = not failed_checks
    summary = _summary_from_checks(
        org_name=org_name_clean,
        ein_match=ein_match,
        address_valid=address_valid,
        web_presence=web_presence,
        failures=failed_checks,
        ein_reason=ein_result["reason"],
        address_reason=address_result["reason"],
        web_reason=web_result["reason"],
    )
    review_token = secrets.token_urlsafe(24)

    verification_write = await asyncio.to_thread(
        lambda: (
            supabase.table(VERIFICATION_QUEUE_TABLE)
            .insert(
                {
                    "food_bank_id": food_bank_id,
                    "ein_match": ein_match,
                    "address_valid": address_valid,
                    "web_presence": web_presence,
                    "summary": summary,
                    "review_token": review_token,
                }
            )
            .execute()
        )
    )
    verification_id = None
    if verification_write is not None:
        vdata = getattr(verification_write, "data", None)
        if vdata and isinstance(vdata, list) and len(vdata) > 0:
            verification_id = str(vdata[0]["id"])

    fb_update_payload: dict[str, str | None] = {
        "status": "verified" if all_passed else "rejected",
        "verified_at": datetime.now(timezone.utc).isoformat() if all_passed else None,
    }
    await asyncio.to_thread(
        lambda: (
            supabase.table(FOOD_BANKS_TABLE)
            .update(fb_update_payload)
            .eq("id", food_bank_id)
            .execute()
        )
    )

    return {
        "food_bank_id": food_bank_id,
        "verification_id": verification_id,
        "all_passed": all_passed,
        "failed_checks": failed_checks,
        "summary": summary,
        "organization_name_used": org_name_clean,
        "state": state_abbrev,
        "zipcode": zip5,
        "ein_resolution": ein_resolution,
        "ein_used": ein_for_check,
        "check_details": {
            "ein_match": ein_result,
            "address_valid": address_result,
            "web_presence": web_result,
        },
    }


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
        .select("*")
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
            "user": row,
        }

    d = (
        supabase.table(DONORS_TABLE)
        .select("*")
        .eq("phone", normalized)
        .maybe_single()
        .execute()
    )
    if d and d.data:
        return {
            "role": "donor",
            "registration_status": "registered",
            "donor": d.data,
        }

    f = (
        supabase.table(FOOD_BANKS_TABLE)
        .select("*")
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
            "food_bank": row,
        }

    return {
        "role": "unknown",
        "registration_status": "unregistered",
        "user_id": None,
        "donor_id": None,
        "food_bank_id": None,
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
    zip = str(zip).strip().split("-")[0][:5]
    # Keep food_bank_window visible for callers that should see food-bank-routed inventory.
    allowed_statuses = ["available", "food_bank_window", "open"]
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
    vapi = Vapi(token=vapi_key) if vapi_key else None

    for bank in food_banks:
        lang = bank.get("preferred_lang", "en")
        if lang == "es":
            prompt = f"Estás llamando en nombre de MiComida. Un donante ha listado {food_desc} para recoger en {addr} a las {pickup}. ¿Puede su banco de alimentos reclamar esta donación? Si es así, confirme ahora."
        else:
            prompt = f"You are calling on behalf of MiComida. A donor has listed {food_desc} for pickup at {addr} at {pickup}. Can your food bank claim this donation? If yes, please confirm now."

        if not (vapi and assistant_id and phone_number_id):
            logger.warning(
                "notify_food_banks: skipped Vapi call due to missing config "
                "(has_key=%s has_assistant_id=%s has_phone_number_id=%s)",
                bool(vapi_key),
                bool(assistant_id),
                bool(phone_number_id),
            )
            continue

        await asyncio.to_thread(
            lambda: vapi.calls.create(
                assistant_id=assistant_id,
                assistant_overrides={
                    "first_message": prompt,
                    "variable_values": {"listing_id": listing_id},
                },
                phone_number_id=phone_number_id,
                customer={"number": bank["phone"]},
            )
        )

        supabase.table("alert_log").insert({
            "listing_id": listing_id,
            "food_bank_phone": bank["phone"],
        }).execute()

        called.append(bank["name"])

    return {"result": f"Notified {len(called)} food bank(s): {', '.join(called)}. They are being called now."}


# ============================================================
# ============================================================
# Tool 7: claim_food_listing  (NEW: search-by-description)
# ============================================================

def _distance_score(
    listing: dict,
    caller_zip: str | None,
    hint_zip: str | None,
    hint_text: str,
) -> int:
    """
    Lower = better match. Heuristic, no geocoding.

    0  hint is a zip, matches listing zip exactly
    1  caller's home zip matches listing zip
    2  hint text appears in listing pickup_addr (case-insensitive)
    3  hint zip prefix (3 digits) matches listing zip prefix
    4  caller zip prefix matches listing zip prefix
    5  anything else available
    """
    listing_zip = str(listing.get("zip") or "")[:5]
    listing_addr = str(listing.get("pickup_addr") or "").lower()
    hint_lower = hint_text.lower().strip()

    if hint_zip and listing_zip == hint_zip:
        return 0
    if caller_zip and listing_zip == caller_zip:
        return 1
    if hint_lower and len(hint_lower) >= 3 and hint_lower in listing_addr:
        return 2
    if hint_zip and len(listing_zip) >= 3 and listing_zip[:3] == hint_zip[:3]:
        return 3
    if caller_zip and len(listing_zip) >= 3 and listing_zip[:3] == caller_zip[:3]:
        return 4
    return 5


async def _lookup_caller_zip(supabase: Client, phone: str) -> str | None:
    """Best-effort zip lookup across users → donors → food_banks."""
    for table in (USERS_TABLE, DONORS_TABLE, FOOD_BANKS_TABLE):
        try:
            r = (
                supabase.table(table)
                .select("zip")
                .eq("phone", phone)
                .limit(1)
                .execute()
            )
            if r and r.data:
                z = _normalize_zip5(str(r.data[0].get("zip") or ""))
                if z:
                    return z
        except Exception:
            continue
    return None


async def _lookup_claimer(supabase: Client, phone: str) -> dict[str, str]:
    """Best-effort claimer role lookup for claim audit trail."""
    for table, role in (
        (USERS_TABLE, "recipient"),
        (FOOD_BANKS_TABLE, "food_bank"),
        (DONORS_TABLE, "donor"),
    ):
        try:
            r = (
                supabase.table(table)
                .select("id")
                .eq("phone", phone)
                .limit(1)
                .execute()
            )
            if r and r.data:
                return {"role": role}
        except Exception:
            continue
    return {"role": "unknown"}


async def _notify_donor_of_claim(donor: dict, listing: dict, claimer: dict[str, str]) -> None:
    """Fire-and-forget donor notification via Vapi outbound call."""
    vapi_key = os.getenv("VAPI_API_KEY")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")
    phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID")

    donor_phone = str(donor.get("phone") or "").strip()
    if not (vapi_key and assistant_id and phone_number_id and donor_phone):
        logger.warning(
            "claim notify: skipped donor call due to missing config or phone "
            "(has_key=%s has_assistant_id=%s has_phone_number_id=%s has_donor_phone=%s)",
            bool(vapi_key),
            bool(assistant_id),
            bool(phone_number_id),
            bool(donor_phone),
        )
        return

    food_type = listing.get("food_type") or "food"
    quantity = listing.get("quantity") or "an item"
    pickup_addr = listing.get("pickup_addr") or "the listed pickup location"
    pickup_time = listing.get("pickup_time") or "the listed pickup time"
    claimer_role = claimer.get("role", "someone")

    prompt = (
        "You are calling on behalf of MiComida with an update for a donor. "
        f"The listing for {quantity} of {food_type} has been claimed by a {claimer_role}. "
        f"Pickup details are {pickup_addr} at {pickup_time}. "
        "Thank them for donating and keep the call concise and warm."
    )

    logger.info(
        "claim notify: attempting donor call listing_id=%s donor_phone=%s claimer_role=%s",
        str(listing.get("id")),
        donor_phone,
        claimer_role,
    )
    vapi = Vapi(token=vapi_key)
    try:
        await asyncio.to_thread(
            lambda: vapi.calls.create(
                assistant_id=assistant_id,
                assistant_overrides={
                    "first_message": prompt,
                    "variable_values": {"listing_id": str(listing.get("id"))},
                },
                phone_number_id=phone_number_id,
                customer={"number": donor_phone},
            )
        )
    except Exception as exc:
        logger.exception(
            "claim notify: donor call failed listing_id=%s donor_phone=%s error=%s",
            str(listing.get("id")),
            donor_phone,
            exc,
        )
        return

    logger.info(
        "claim notify: donor call queued listing_id=%s donor_phone=%s",
        str(listing.get("id")),
        donor_phone,
    )


async def claim_food_listing_by_id(
    supabase: Client,
    listing_id: str,
    phone: str,
) -> dict:
    """Claims a listing directly by listing_id and notifies donor."""
    phone = normalize_phone(phone)
    listing_id = str(listing_id).strip()
    if not listing_id:
        return {"success": False, "reason": "missing_listing_id"}

    listing_resp = (
        supabase.table(LISTINGS_TABLE)
        .select("id, food_type, quantity, pickup_addr, pickup_time, donor_phone, status")
        .eq("id", listing_id)
        .maybe_single()
        .execute()
    )
    listing_row = listing_resp.data if listing_resp else None
    if not listing_row:
        return {"success": False, "reason": "listing_not_found"}

    update = (
        supabase.table(LISTINGS_TABLE)
        .update({"status": "claimed"})
        .eq("id", listing_id)
        .in_("status", ["available", "food_bank_window", "open"])
        .execute()
    )
    if not update.data:
        return {
            "success": False,
            "reason": "already_claimed_or_unavailable",
            "message": "This listing is no longer available to claim.",
        }

    listing = update.data[0]
    claimer = await _lookup_claimer(supabase, phone)
    claim = (
        supabase.table(CLAIMS_TABLE)
        .insert(
            {
                "listing_id": listing["id"],
                "claimer_phone": phone,
                "claimer_type": claimer["role"],
            }
        )
        .execute()
    )

    donor = (
        supabase.table(DONORS_TABLE)
        .select("phone, name, address")
        .eq("phone", listing.get("donor_phone"))
        .maybe_single()
        .execute()
    )
    donor_row = donor.data if donor else None
    if donor_row:
        asyncio.create_task(
            _notify_donor_of_claim(donor=donor_row, listing=listing, claimer=claimer)
        )

    return {
        "success": True,
        "claim_id": str(claim.data[0]["id"]) if claim.data else None,
        "listing_id": str(listing["id"]),
        "food_type": listing.get("food_type"),
        "quantity": listing.get("quantity"),
        "pickup_addr": listing.get("pickup_addr"),
        "pickup_time": listing.get("pickup_time"),
        "donor_phone": donor_row["phone"] if donor_row else None,
    }


async def claim_food_listing(
    supabase: Client,
    food_type: str,
    pickup_hint: str,
    phone: str,
) -> dict:
    """
    Fires when a recipient or food bank confirms they want food matching a
    spoken description. The assistant passes through what the caller said
    rather than tracking listing IDs across turns.

    Args:
        food_type:   what the caller wants — "burritos", "pizza", "produce"
        pickup_hint: where — neighborhood, address fragment, or zip ("El Centro", "95820")
        phone:       caller's phone

    Flow: search matching listings → rank by distance → atomically claim
    the closest. On race loss, fall through to next candidate.
    """
    phone = normalize_phone(phone)
    food_query = str(food_type).strip()
    hint = str(pickup_hint or "").strip()

    if not food_query:
        return {"success": False, "reason": "missing_food_type"}

    # ─── Resolve location context for ranking ──────────────────
    caller_zip = await _lookup_caller_zip(supabase, phone)
    hint_zip = _normalize_zip5(hint) if hint else None

    # ─── Query candidate listings ──────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    candidates_resp = (
        supabase.table(LISTINGS_TABLE)
        .select(
            "id, food_type, quantity, pickup_addr, pickup_time, "
            "expiry_time, zip, donor_phone, status"
        )
        .ilike("food_type", f"%{food_query}%")
        .in_("status", ["available", "food_bank_window", "open"])
        .or_(f"expiry_time.gt.{now},expiry_time.is.null")
        .limit(20)
        .execute()
    )
    candidates = candidates_resp.data or []
    if not candidates:
        return {
            "success": False,
            "reason": "no_matching_listings",
            "message": f"No '{food_query}' is available right now.",
        }

    # ─── Rank: distance score, then expiry-soonest as tiebreak ──
    def sort_key(c: dict) -> tuple:
        return (
            _distance_score(c, caller_zip, hint_zip, hint),
            c.get("expiry_time") or "9999",  # null expiry sorts last
        )
    ranked = sorted(candidates, key=sort_key)

    # ─── Atomic claim with race-loss fallthrough ───────────────
    listing = None
    for candidate in ranked:
        update = (
            supabase.table(LISTINGS_TABLE)
            .update({"status": "claimed"})
            .eq("id", candidate["id"])
            .in_("status", ["available", "food_bank_window", "open"])
            .execute()
        )
        if update.data:
            listing = update.data[0]
            break

    if listing is None:
        return {
            "success": False,
            "reason": "all_candidates_claimed",
            "message": "Those listings were just claimed by someone else. Try again in a moment.",
        }

    # ─── Insert claim row + notify donor ─────────────────────
    claimer = await _lookup_claimer(supabase, phone)

    claim = (
        supabase.table(CLAIMS_TABLE)
        .insert({
            "listing_id": listing["id"],
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
        asyncio.create_task(
            _notify_donor_of_claim(donor=donor, listing=listing, claimer=claimer)
        )

    return {
        "success": True,
        "claim_id": str(claim.data[0]["id"]) if claim.data else None,
        "listing_id": str(listing["id"]),
        "food_type": listing.get("food_type"),
        "quantity": listing.get("quantity"),
        "pickup_addr": listing.get("pickup_addr"),
        "pickup_time": listing.get("pickup_time"),
        "donor_phone": donor["phone"] if donor else None,
    }

# Tool 8: register_food_bank
# ============================================================

async def register_food_bank(
    supabase: Client,
    phone: str,
    name: str,
    ein: str | None,
    address: str,
    zip_code: str,
    lang: str = "en",
) -> dict:
    """
    Fires after assistant collects contact info from a food bank caller,
    before verify_organization runs. Inserts food_bank row with status pending.
    """
    phone_n = normalize_phone(phone)
    param_digits = _ein_digits_if_valid(ein)
    ein_value: str | None = _ein_display_from_digits(param_digits) if param_digits else None

    existing = await asyncio.to_thread(
        lambda: (
            supabase.table(FOOD_BANKS_TABLE)
            .select("ein")
            .eq("phone", phone_n)
            .maybe_single()
            .execute()
        )
    )
    existing_row = getattr(existing, "data", None) if existing is not None else None
    if ein_value is None and existing_row and existing_row.get("ein"):
        prev = _ein_digits_if_valid(str(existing_row["ein"]))
        if prev:
            ein_value = _ein_display_from_digits(prev)

    result = await asyncio.to_thread(
        lambda: (
            supabase.table(FOOD_BANKS_TABLE)
            .upsert(
                {
                    "phone": phone_n,
                    "name": str(name).strip(),
                    "ein": ein_value,
                    "address": str(address).strip(),
                    "zip": str(zip_code).strip().split("-")[0][:5],
                    "preferred_lang": str(lang).strip() or "en",
                    "status": "pending",
                },
                on_conflict="phone",
            )
            .execute()
        )
    )

    row_data = getattr(result, "data", None) if result is not None else None
    if not row_data:
        raise RuntimeError(f"food_banks upsert returned no row: {result}")

    food_bank = row_data[0]
    return {
        "food_bank_id": str(food_bank["id"]),
        "name": name,
        "ein": ein_value,
        "address": address,
        "zip": zip_code,
        "lang": lang,
        "status": food_bank["status"],
    }
    
    # ============================================================
# Tool 9: get_nearby_food_banks
# ============================================================

async def get_nearby_food_banks(supabase: Client, zip: str) -> dict:
    zip5 = str(zip).strip().split("-")[0][:5]
    result = (
        supabase.table(FOOD_BANKS_TABLE)
        .select("name, address, zip, phone, preferred_lang, status")
        .eq("zip", zip5)
        .eq("status", "verified")
        .execute()
    )

    food_banks = result.data or []

    if not food_banks:
        return {
            "result": "I couldn't find any verified food banks near your zip code right now.",
            "zip": zip5,
            "nearby_food_banks": [],
            "claimed_food_options": [],
        }

    bank_phones = [str(fb.get("phone") or "").strip() for fb in food_banks if fb.get("phone")]
    claimed_food_options: list[dict[str, object]] = []
    if bank_phones:
        claims_resp = (
            supabase.table(CLAIMS_TABLE)
            .select("id, listing_id, claimer_phone")
            .eq("claimer_type", "food_bank")
            .in_("claimer_phone", bank_phones)
            .limit(30)
            .execute()
        )
        claim_rows = claims_resp.data or []
        listing_ids = [
            str(row.get("listing_id"))
            for row in claim_rows
            if row.get("listing_id")
        ]
        listing_ids = list(dict.fromkeys(listing_ids))

        listings_by_id: dict[str, dict] = {}
        if listing_ids:
            now = datetime.now(timezone.utc).isoformat()
            listings_resp = (
                supabase.table(LISTINGS_TABLE)
                .select("id, food_type, quantity, pickup_addr, pickup_time, expiry_time, zip, status")
                .in_("id", listing_ids)
                .eq("zip", zip5)
                .eq("status", "claimed")
                .or_(f"expiry_time.gt.{now},expiry_time.is.null")
                .execute()
            )
            listings_by_id = {
                str(row["id"]): row for row in (listings_resp.data or []) if row.get("id")
            }

        banks_by_phone = {
            str(fb.get("phone") or ""): fb for fb in food_banks if fb.get("phone")
        }
        for claim_row in claim_rows:
            listing_id = str(claim_row.get("listing_id") or "")
            listing = listings_by_id.get(listing_id)
            if not listing:
                continue
            bank_phone = str(claim_row.get("claimer_phone") or "")
            bank = banks_by_phone.get(bank_phone)
            if not bank:
                continue
            claimed_food_options.append(
                {
                    "claim_id": str(claim_row.get("id") or ""),
                    "listing_id": listing_id,
                    "food_bank_phone": bank_phone,
                    "food_bank_name": bank.get("name"),
                    "food_type": listing.get("food_type"),
                    "quantity": listing.get("quantity"),
                    "pickup_addr": listing.get("pickup_addr"),
                    "pickup_time": listing.get("pickup_time"),
                }
            )

    nearby_banks_payload = [
        {
            "name": fb.get("name"),
            "address": fb.get("address"),
            "zip": fb.get("zip"),
            "phone": fb.get("phone"),
            "preferred_lang": fb.get("preferred_lang"),
        }
        for fb in food_banks[:5]
    ]

    lines = []
    for fb in food_banks[:3]:
        lines.append(f"{fb['name']} at {fb['address']}")

    summary = "Here are the nearest food banks to you: " + "; ".join(lines)
    if claimed_food_options:
        option_lines = []
        for option in claimed_food_options[:3]:
            pickup_addr = option.get("pickup_addr") or "address not specified"
            pickup_time = option.get("pickup_time") or "time not specified"
            option_lines.append(
                f"{option.get('food_type')}, {option.get('quantity')} from {option.get('food_bank_name')} "
                f"at {pickup_addr} around {pickup_time}"
            )
        summary += (
            " I can currently connect you to these claimed food options: "
            + "; ".join(option_lines)
            + ". Would you like me to notify one of these food banks that you are interested?"
        )
    else:
        summary += ". I don't currently see claimed food options in this zip. Would you like me to keep checking?"

    return {
        "result": summary,
        "zip": zip5,
        "nearby_food_banks": nearby_banks_payload,
        "claimed_food_options": claimed_food_options[:5],
    }


async def _notify_food_bank_of_recipient_interest(
    food_bank: dict,
    listing: dict,
    recipient_phone: str,
) -> None:
    vapi_key = os.getenv("VAPI_API_KEY")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")
    phone_number_id = os.getenv("VAPI_PHONE_NUMBER_ID")
    food_bank_phone = str(food_bank.get("phone") or "").strip()
    if not (vapi_key and assistant_id and phone_number_id and food_bank_phone):
        logger.warning(
            "recipient notify: skipped food bank call due to missing config/phone "
            "(has_key=%s has_assistant_id=%s has_phone_number_id=%s has_food_bank_phone=%s)",
            bool(vapi_key),
            bool(assistant_id),
            bool(phone_number_id),
            bool(food_bank_phone),
        )
        return

    lang = str(food_bank.get("preferred_lang") or "en").lower()
    food_type = listing.get("food_type") or "food"
    quantity = listing.get("quantity") or "an item"
    pickup_addr = listing.get("pickup_addr") or "address not specified"
    pickup_time = listing.get("pickup_time") or "time not specified"
    if lang == "es":
        first_message = (
            "Llamas en nombre de MiComida. "
            f"Un beneficiario nuevo está interesado en {quantity} de {food_type}. "
            f"La recogida está en {pickup_addr} a las {pickup_time}. "
            f"El número del beneficiario es {recipient_phone}. "
            "Por favor confirma seguimiento con el beneficiario."
        )
    else:
        first_message = (
            "You are calling on behalf of MiComida. "
            f"A recipient is interested in {quantity} of {food_type}. "
            f"Pickup is at {pickup_addr} at {pickup_time}. "
            f"The recipient phone number is {recipient_phone}. "
            "Please confirm follow-up with the recipient."
        )

    logger.info(
        "recipient notify: attempting food bank call listing_id=%s food_bank_phone=%s recipient_phone=%s",
        str(listing.get("id")),
        food_bank_phone,
        recipient_phone,
    )
    vapi = Vapi(token=vapi_key)
    try:
        await asyncio.to_thread(
            lambda: vapi.calls.create(
                assistant_id=assistant_id,
                assistant_overrides={
                    "first_message": first_message,
                    "variable_values": {
                        "listing_id": str(listing.get("id")),
                        "recipient_phone": recipient_phone,
                    },
                },
                phone_number_id=phone_number_id,
                customer={"number": food_bank_phone},
            )
        )
    except Exception as exc:
        logger.exception(
            "recipient notify: food bank call failed listing_id=%s food_bank_phone=%s error=%s",
            str(listing.get("id")),
            food_bank_phone,
            exc,
        )
        return

    logger.info(
        "recipient notify: food bank call queued listing_id=%s food_bank_phone=%s",
        str(listing.get("id")),
        food_bank_phone,
    )


async def request_food_from_food_bank(
    supabase: Client,
    recipient_phone: str,
    listing_id: str,
    food_bank_phone: str,
) -> dict:
    recipient_phone_n = normalize_phone(recipient_phone)
    listing_id = str(listing_id or "").strip()
    food_bank_phone_n = normalize_phone(food_bank_phone)

    if not listing_id:
        return {"success": False, "reason": "missing_listing_id"}
    if not food_bank_phone_n:
        return {"success": False, "reason": "missing_food_bank_phone"}

    logger.info(
        "recipient request: start recipient_phone=%s listing_id=%s food_bank_phone=%s",
        recipient_phone_n,
        listing_id,
        food_bank_phone_n,
    )

    recipient_lookup = (
        supabase.table(USERS_TABLE)
        .select("id, phone, zip")
        .eq("phone", recipient_phone_n)
        .maybe_single()
        .execute()
    )
    if not recipient_lookup or not recipient_lookup.data:
        return {
            "success": False,
            "reason": "recipient_not_registered",
            "message": "Recipient must be registered before requesting food from a food bank.",
        }

    food_bank_lookup = (
        supabase.table(FOOD_BANKS_TABLE)
        .select("id, name, phone, preferred_lang, status")
        .eq("phone", food_bank_phone_n)
        .maybe_single()
        .execute()
    )
    food_bank_row = food_bank_lookup.data if food_bank_lookup else None
    if not food_bank_row:
        return {"success": False, "reason": "food_bank_not_found"}
    if str(food_bank_row.get("status") or "").lower() != "verified":
        return {"success": False, "reason": "food_bank_not_verified"}

    listing_lookup = (
        supabase.table(LISTINGS_TABLE)
        .select("id, food_type, quantity, pickup_addr, pickup_time, zip, status, expiry_time")
        .eq("id", listing_id)
        .maybe_single()
        .execute()
    )
    listing_row = listing_lookup.data if listing_lookup else None
    if not listing_row:
        return {"success": False, "reason": "listing_not_found"}
    if str(listing_row.get("status") or "").lower() != "claimed":
        return {"success": False, "reason": "listing_not_claimed_by_food_bank"}

    claim_lookup = (
        supabase.table(CLAIMS_TABLE)
        .select("id, claimer_phone, claimer_type")
        .eq("listing_id", listing_id)
        .eq("claimer_phone", food_bank_phone_n)
        .eq("claimer_type", "food_bank")
        .limit(1)
        .execute()
    )
    if not claim_lookup or not claim_lookup.data:
        return {"success": False, "reason": "food_bank_claim_not_found"}

    request_insert = (
        supabase.table(CLAIMS_TABLE)
        .insert(
            {
                "listing_id": listing_id,
                "claimer_phone": recipient_phone_n,
                "claimer_type": "recipient",
            }
        )
        .execute()
    )
    request_row = request_insert.data[0] if request_insert and request_insert.data else None

    asyncio.create_task(
        _notify_food_bank_of_recipient_interest(
            food_bank=food_bank_row,
            listing=listing_row,
            recipient_phone=recipient_phone_n,
        )
    )

    logger.info(
        "recipient request: queued notify listing_id=%s food_bank_phone=%s recipient_phone=%s",
        listing_id,
        food_bank_phone_n,
        recipient_phone_n,
    )
    return {
        "success": True,
        "request_id": str(request_row["id"]) if request_row else None,
        "listing_id": listing_id,
        "food_bank_phone": food_bank_phone_n,
        "food_bank_name": food_bank_row.get("name"),
        "food_type": listing_row.get("food_type"),
        "quantity": listing_row.get("quantity"),
        "pickup_addr": listing_row.get("pickup_addr"),
        "pickup_time": listing_row.get("pickup_time"),
        "message": "Your request has been sent to the food bank. They will follow up shortly.",
    }