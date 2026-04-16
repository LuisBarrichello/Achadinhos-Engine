import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.links import router as links_router
from api.routes.system import router as system_router
from api.routes.webhooks import router as webhooks_router
from core.config import FRONTEND_ORIGIN
from core.database import lifespan

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Achadinhos do Momento API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

app.include_router(links_router)
app.include_router(webhooks_router)
app.include_router(system_router)
