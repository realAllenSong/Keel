from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from pf.api.routers import (
    app_update,
    connections,
    home,
    ledger,
    onboarding,
    portfolio,
    prefs,
    pulse,
    reports,
    sim,
    tools,
)
from pf.api.security import LocalOnlyMiddleware
from pf.db import make_engine, migrate
from pf.i18n import LanguageMiddleware
from pf.reports.common import sync_account_meta


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    migrate()
    with make_engine().begin() as conn:
        sync_account_meta(conn)
        from pf.ledger.portfolio import backfill
        from pf.settings import get_settings

        backfill(conn, get_settings().raw_dir)  # once: positions from earlier statements
    yield


app = FastAPI(title="Keel API", version="0.1.0", lifespan=lifespan)
app.add_middleware(LanguageMiddleware)
app.add_middleware(LocalOnlyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8710", "http://127.0.0.1:8710"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(app_update.router)
app.include_router(connections.router)
app.include_router(home.router)
app.include_router(ledger.router)
app.include_router(onboarding.router)
app.include_router(portfolio.router)
app.include_router(portfolio.bills_router)
app.include_router(portfolio.goals_router)
app.include_router(prefs.router)
app.include_router(pulse.router)
app.include_router(reports.router)
app.include_router(sim.router)
app.include_router(tools.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(RequestValidationError)
async def redact_statement_password_validation(request: Request, exc: RequestValidationError):
    path = request.url.path
    if path.startswith("/api/onboarding/statement-passwords") or (
        path.startswith("/api/onboarding/pending/") and path.endswith("/unlock")
    ):
        # SecretStr only masks successful parsing. Rejected input and JSON context
        # can still contain the original code; return structure/messages only.
        detail = [{k: e[k] for k in ("type", "loc", "msg") if k in e} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})
    return await request_validation_exception_handler(request, exc)
