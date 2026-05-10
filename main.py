from dotenv import load_dotenv

# Load .env before routes import (webhook creates Supabase client at import time).
load_dotenv()

from fastapi import FastAPI

from routes.webhook import router as webhook_router

app = FastAPI(title="Mesa API")
app.include_router(webhook_router)
