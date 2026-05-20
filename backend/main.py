import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routes.links import router as links_router
from api.routes.system import router as system_router
from api.routes.webhooks import router as webhooks_router
from core.config import FRONTEND_ORIGIN
from core.database import lifespan

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Achadinhos do Momento API", lifespan=lifespan)

# Hardening: CORS Estrito
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN, "http://localhost:5500"], # NUNCA USE "*"
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["x-admin-secret", "Content-Type", "Authorization"],
)

# Hardening: Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    # Proteção de tamanho de payload no backend (2MB max)
    if int(request.headers.get("content-length", 0)) > 2 * 1024 * 1024:
        return JSONResponse(status_code=413, content={"detail": "Payload Too Large"})

    response = await call_next(request)
    # Proteção contra Sniffing de MIME Type
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Proteção contra Clickjacking e iFrames
    response.headers["X-Frame-Options"] = "DENY"
    # Proteção XSS via Browser
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # HTTP Strict Transport Security (Força HTTPS)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

app.include_router(links_router)
app.include_router(webhooks_router)
app.include_router(system_router)