from dotenv import load_dotenv

# Load .env before routes import (webhook creates Supabase client at import time).
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from routes.webhook import router as webhook_router
from routes.calls import router as calls_router

app = FastAPI(title="Mesa API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(webhook_router)
app.include_router(calls_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "error": "validation_error",
            "message": "Request validation failed. Check required fields and types.",
            "path": str(request.url.path),
            "details": exc.errors(),
        },
    )
