# routes/webhook.py — relevant excerpt
import json
from services.tools import register_new_user
import supabase

async def dispatch_tool(name: str, args: dict, message: dict):
    if name == "register_new_user":
        # Vapi exposes detected language on the call object once the
        # assistant has heard the caller speak. Default to "en" if absent.
        lang = (message.get("call", {}).get("detectedLanguage") or "en")[:2]
        return await register_new_user(
            supabase=supabase,
            phone=args["phone"],
            zip_code=args["zip"],
            household_size=args["household_size"],
            lang=lang,
        )
    # ... other 8 tools