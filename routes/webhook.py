# routes/webhook.py
import json
import logging
import os

from fastapi import APIRouter, Request
from pydantic import BaseModel
from supabase import create_client
from services.tools import (
    identify_caller,
    register_new_user,
    register_donor,
    get_available_food,
    save_food_listing,
    notify_food_banks,
    claim_food_listing,
    register_food_bank,
    verify_organization,
    get_nearby_food_banks,
)

router = APIRouter()
logger = logging.getLogger("uvicorn.error")
supabase = create_client(
    os.getenv("SUPABASE_URL"),   # set in your .env
    os.getenv("SUPABASE_KEY"),   # set in your .env
)


def _missing_args(tool_name: str, arguments: dict, required: list[str]) -> dict:
    missing = [key for key in required if key not in arguments]
    return {
        "ok": False,
        "error": "missing_arguments",
        "tool": tool_name,
        "required": required,
        "missing": missing,
    }


async def _dispatch_tool_call(tool_name: str, arguments: dict) -> dict:
    if tool_name == "test_identify_caller":
        if "phone" not in arguments:
            return _missing_args(tool_name, arguments, ["phone"])
        return await identify_caller(supabase, arguments["phone"])

    if tool_name == "test_register_new_user":
        required = ["phone", "zip_code", "household_size"]
        if any(key not in arguments for key in required):
            return _missing_args(tool_name, arguments, required)
        return await register_new_user(
            supabase,
            arguments["phone"],
            arguments["zip_code"],
            arguments["household_size"],
            arguments.get("lang", "en"),
        )

    if tool_name == "test_register_donor":
        required = ["phone", "name", "business", "zip"]
        if any(key not in arguments for key in required):
            return _missing_args(tool_name, arguments, required)
        return await register_donor(
            supabase,
            arguments["phone"],
            arguments["name"],
            arguments["business"],
            arguments["zip"],
            arguments.get("lang", "en"),
        )

    if tool_name == "test_get_available_food":
        required = ["zip", "income_tier"]
        if any(key not in arguments for key in required):
            return _missing_args(tool_name, arguments, required)
        return await get_available_food(supabase, arguments["zip"], arguments["income_tier"])

    if tool_name == "test_save_food_listing":
        required = ["food_type", "quantity", "pickup_time", "zip_code", "donor_phone"]
        if any(key not in arguments for key in required):
            return _missing_args(tool_name, arguments, required)
        return await save_food_listing(
            supabase,
            arguments["food_type"],
            arguments["quantity"],
            arguments["pickup_time"],
            arguments["zip_code"],
            arguments["donor_phone"],
        )

    if tool_name == "test_notify_food_banks":
        required = ["listing_id", "zip"]
        if any(key not in arguments for key in required):
            return _missing_args(tool_name, arguments, required)
        return await notify_food_banks(supabase, arguments["listing_id"], arguments["zip"])

    if tool_name == "test_claim_food_listing":
        required = ["food_type", "pickup_hint", "phone"]
        if any(key not in arguments for key in required):
            return _missing_args(tool_name, arguments, required)
        return await claim_food_listing(
            supabase,
            arguments["food_type"],
            arguments["pickup_hint"],
            arguments["phone"],
        )

    if tool_name == "test_register_food_bank":
        required = ["phone", "name", "address", "zip_code"]
        if any(key not in arguments for key in required):
            return _missing_args(tool_name, arguments, required)
        return await register_food_bank(
            supabase,
            arguments["phone"],
            arguments["name"],
            arguments.get("ein"),
            arguments["address"],
            arguments["zip_code"],
            arguments.get("lang", "en"),
        )

    if tool_name == "test_verify_organization":
        required = ["org_name", "address", "city", "state", "zipcode", "phone"]
        if any(key not in arguments for key in required):
            return _missing_args(tool_name, arguments, required)
        return await verify_organization(
            supabase=supabase,
            org_name=arguments["org_name"],
            address=arguments["address"],
            city=arguments["city"],
            state=arguments["state"],
            zipcode=arguments["zipcode"],
            phone=arguments["phone"],
            ein=arguments.get("ein"),
        )

    if tool_name == "test_get_nearby_food_banks":
        if "zip" not in arguments:
            return _missing_args(tool_name, arguments, ["zip"])
        return await get_nearby_food_banks(supabase, arguments["zip"])

    return {"ok": False, "error": f"Unknown tool: {tool_name}"}


@router.post("/vapi/webhook")
async def vapi_webhook(request: Request):
    try:
        body = await request.json()
    except Exception as exc:
        logger.exception("VAPI webhook: failed to parse JSON body: %s", exc)
        return {"results": []}

    if not isinstance(body, dict):
        logger.warning("VAPI webhook: ignored non-dict body: %s", type(body).__name__)
        return {"results": []}

    logger.info("VAPI webhook: incoming payload=%s", json.dumps(body, default=str))

    message = body.get("message", {})
    if not isinstance(message, dict) or message.get("type") != "tool-calls":
        logger.warning(
            "VAPI webhook: ignored message type. message=%s",
            json.dumps(message, default=str),
        )
        return {"results": []}

    tool_call_list = message.get("toolCallList", [])
    if not isinstance(tool_call_list, list):
        logger.warning(
            "VAPI webhook: toolCallList is not a list. value=%s",
            json.dumps(tool_call_list, default=str),
        )
        return {"results": []}

    logger.info("VAPI webhook: received %d tool call(s)", len(tool_call_list))

    results = []
    for tool_call in tool_call_list:
        if not isinstance(tool_call, dict):
            logger.warning("VAPI webhook: skipped non-dict tool call: %s", type(tool_call).__name__)
            continue

        call_id = str(tool_call.get("id", ""))
        func = tool_call.get("function", {})
        tool_name = str(func.get("name", ""))
        arguments = func.get("arguments", {})

        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except Exception:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        logger.info(
            "VAPI webhook: dispatching call_id=%s tool_name=%s arguments=%s",
            call_id,
            tool_name,
            json.dumps(arguments, default=str),
        )

        try:
            result = await _dispatch_tool_call(tool_name, arguments)
        except Exception as exc:
            logger.exception(
                "VAPI webhook: tool execution failed call_id=%s tool_name=%s error=%s",
                call_id,
                tool_name,
                exc,
            )
            result = {
                "ok": False,
                "error": "tool_execution_failed",
                "tool": tool_name,
                "message": str(exc),
            }
        else:
            logger.info(
                "VAPI webhook: tool completed call_id=%s tool_name=%s result=%s",
                call_id,
                tool_name,
                json.dumps(result, default=str),
            )

        results.append(
            {
                "toolCallId": call_id,
                "result": json.dumps(result, default=str),
            }
        )

    logger.info("VAPI webhook: returning results=%s", json.dumps(results, default=str))
    return {"results": results}


class IdentifyCallerRequest(BaseModel):
    phone: str


class RegisterNewUserRequest(BaseModel):
    phone: str
    zip_code: str
    household_size: int
    lang: str = "en"


class RegisterDonorRequest(BaseModel):
    phone: str
    name: str
    business: str
    zip: str
    lang: str = "en"


class GetAvailableFoodRequest(BaseModel):
    zip: str
    income_tier: str


class SaveFoodListingRequest(BaseModel):
    food_type: str
    quantity: str
    pickup_time: str
    zip_code: str
    donor_phone: str


class NotifyFoodBanksRequest(BaseModel):
    listing_id: str
    zip: str


class ClaimFoodListingRequest(BaseModel):
    food_type: str
    pickup_hint: str
    phone: str


class RegisterFoodBankRequest(BaseModel):
    phone: str
    name: str
    address: str
    zip_code: str
    ein: str | None = None
    lang: str = "en"


class VerifyOrganizationRequest(BaseModel):
    org_name: str
    address: str
    city: str
    state: str
    zipcode: str
    phone: str
    ein: str | None = None


class GetNearbyFoodBanksRequest(BaseModel):
    zip: str


# ============================================================
# Test routes — hit these at localhost:8000/docs
# ============================================================

@router.post("/test/identify-caller")
async def test_identify_caller(payload: IdentifyCallerRequest):
    return await identify_caller(supabase, payload.phone)


@router.post("/test/register-new-user")
async def test_register_new_user(payload: RegisterNewUserRequest):
    return await register_new_user(
        supabase, payload.phone, payload.zip_code, payload.household_size, payload.lang
    )


@router.post("/test/register-donor")
async def test_register_donor(payload: RegisterDonorRequest):
    return await register_donor(
        supabase, payload.phone, payload.name, payload.business, payload.zip, payload.lang
    )


@router.post("/test/get-available-food")
async def test_get_available_food(payload: GetAvailableFoodRequest):
    return await get_available_food(supabase, payload.zip, payload.income_tier)


@router.post("/test/save-food-listing")
async def test_save_food_listing(payload: SaveFoodListingRequest):
    return await save_food_listing(
        supabase,
        payload.food_type,
        payload.quantity,
        payload.pickup_time,
        payload.zip_code,
        payload.donor_phone,
    )


@router.post("/test/notify-food-banks")
async def test_notify_food_banks(payload: NotifyFoodBanksRequest):
    return await notify_food_banks(supabase, payload.listing_id, payload.zip)


@router.post("/test/claim-food-listing")
async def test_claim_food_listing(payload: ClaimFoodListingRequest):
    return await claim_food_listing(supabase, payload.food_type, payload.pickup_hint, payload.phone)


@router.post("/test/register-food-bank")
async def test_register_food_bank(payload: RegisterFoodBankRequest):
    return await register_food_bank(
        supabase, payload.phone, payload.name, payload.ein, payload.address, payload.zip_code, payload.lang
    )


@router.post("/test/verify-organization")
async def test_verify_organization(payload: VerifyOrganizationRequest):
    return await verify_organization(
        supabase=supabase,
        org_name=payload.org_name,
        address=payload.address,
        city=payload.city,
        state=payload.state,
        zipcode=payload.zipcode,
        phone=payload.phone,
        ein=payload.ein,
    )
@router.post("/test/get-nearby-food-banks")
async def test_get_nearby_food_banks(payload: GetNearbyFoodBanksRequest):
    return await get_nearby_food_banks(supabase, payload.zip)