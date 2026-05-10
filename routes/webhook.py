# routes/webhook.py
import os

from fastapi import APIRouter
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
supabase = create_client(
    os.getenv("SUPABASE_URL"),   # set in your .env
    os.getenv("SUPABASE_KEY"),   # set in your .env
)


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
    listing_id: str
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
    return await claim_food_listing(supabase, payload.listing_id, payload.phone)


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