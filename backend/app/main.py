from __future__ import annotations
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import PROJECT_NAME
from app.routers import (
    apps,
    audit,
    cutover,
    dashboard,
    environments,
    fixtures,
    health,
    promote,
    reconcile,
    releases,
    rollback,
    settings,
    windows,
)
from app.seed import seed_if_empty

seed_if_empty()

app = FastAPI(title=PROJECT_NAME)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
for mod in (
    dashboard,
    apps,
    environments,
    releases,
    promote,
    cutover,
    windows,
    rollback,
    health,
    reconcile,
    settings,
    audit,
    fixtures,
):
    app.include_router(mod.router, prefix="/api")


@app.get("/api/健康")
def health_check():
    return {"产品": PROJECT_NAME, "状态": "正常"}

SURFACE_PANEL_FULL33 = "full33"

