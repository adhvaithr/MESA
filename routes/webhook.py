# routes/webhook.py
import os

from fastapi import APIRouter
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
supabase = create_client(
    os.getenv("SUPABASE_URL"),   # set in your .env
    os.getenv("SUPABASE_KEY"),   # set in your .env
)

# ============================================================
# Test routes — hit these at localhost:8000/docs
# ============================================================

@router.post("/test/identify-caller")
async def test_identify_caller(phone: str):
    return await identify_caller(supabase, phone)


@router.post("/test/register-new-user")
async def test_register_new_user(phone: str, zip_code: str, household_size: int, lang: str = "en"):
    return await register_new_user(supabase, phone, zip_code, household_size, lang)


@router.post("/test/register-donor")
async def test_register_donor(phone: str, name: str, business: str, zip: str, lang: str = "en"):
    return await register_donor(supabase, phone, name, business, zip, lang)


@router.post("/test/get-available-food")
async def test_get_available_food(zip: str, income_tier: str):
    return await get_available_food(supabase, zip, income_tier)


@router.post("/test/save-food-listing")
async def test_save_food_listing(food_type: str, quantity: str, pickup_time: str, zip_code: str, donor_phone: str):
    return await save_food_listing(supabase, food_type, quantity, pickup_time, zip_code, donor_phone)


@router.post("/test/notify-food-banks")
async def test_notify_food_banks(listing_id: str, zip: str):
    return await notify_food_banks(supabase, listing_id, zip)


@router.post("/test/claim-food-listing")
async def test_claim_food_listing(listing_id: str, phone: str):
    return await claim_food_listing(supabase, listing_id, phone)


@router.post("/test/register-food-bank")
async def test_register_food_bank(
    phone: str,
    name: str,
    address: str,
    zip_code: str,
    ein: str | None = None,
    lang: str = "en",
):
    return await register_food_bank(supabase, phone, name, ein, address, zip_code, lang)


@router.post("/test/verify-organization")
async def test_verify_organization(
    org_name: str,
    address: str,
    city: str,
    state: str,
    zipcode: str,
    phone: str,
    ein: str | None = None,
):
    return await verify_organization(
        supabase=supabase,
        org_name=org_name,
        address=address,
        city=city,
        state=state,
        zipcode=zipcode,
        phone=phone,
        ein=ein,
    )
@router.post("/test/get-nearby-food-banks")
async def test_get_nearby_food_banks(zip: str):
    return await get_nearby_food_banks(supabase, zip)