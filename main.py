from dotenv import load_dotenv

# Load .env before routes import (webhook creates Supabase client at import time).
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from routes.webhook import router as webhook_router

app = FastAPI(title="Mesa API")
app.include_router(webhook_router)


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
