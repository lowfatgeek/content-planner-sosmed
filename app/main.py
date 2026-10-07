"""Entrypoint aplikasi (uvicorn app.main:app)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError as PydanticValidationError

from app.config import settings
from app.db import engine
from app.errors import AppError, Unauthorized
from app.routers import api_v1, ui, ui_admin, ui_content

app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    docs_url="/api/v1/docs" if settings.APP_ENV != "prod" else None,
    redoc_url=None,
    openapi_url="/api/v1/openapi.json" if settings.APP_ENV != "prod" else None,
)

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(api_v1.router)
app.include_router(ui.router)
app.include_router(ui_content.router)
app.include_router(ui_admin.router)


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/") or "application/json" in (
        request.headers.get("accept") or ""
    )


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    if _wants_json(request):
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    if isinstance(exc, Unauthorized) and not request.url.path.startswith("/api/"):
        return RedirectResponse("/login", status_code=303)
    tujuan = request.headers.get("referer") or "/"
    return RedirectResponse(f"{tujuan}?pesan={exc.message}&level=err", status_code=303)


@app.exception_handler(PydanticValidationError)
async def pydantic_handler(request: Request, exc: PydanticValidationError):
    """Validasi model yang kita konstruksi sendiri (mis. ContentCreate(**raw))."""
    detail = [
        {
            "lokasi": ".".join(str(x) for x in err.get("loc", [])),
            "pesan": err.get("msg", ""),
        }
        for err in exc.errors()
    ]
    if _wants_json(request):
        return JSONResponse(
            status_code=422,
            content={
                "error": "validasi",
                "message": "Payload tidak lolos validasi",
                "detail": detail,
            },
        )
    return HTMLResponse("<h1>422</h1><pre>" + str(detail) + "</pre>", status_code=422)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    detail = [
        {"lokasi": ".".join(str(x) for x in err.get("loc", [])), "pesan": err.get("msg", "")}
        for err in exc.errors()
    ]
    if _wants_json(request):
        return JSONResponse(
            status_code=422,
            content={"error": "validasi", "message": "Payload tidak lolos validasi", "detail": detail},
        )
    return HTMLResponse(
        "<h1>422 — payload tidak valid</h1><pre>"
        + "\n".join(f"{d['lokasi']}: {d['pesan']}" for d in detail)
        + "</pre><p><a href='/'>Kembali</a></p>",
        status_code=422,
    )


@app.exception_handler(PydanticValidationError)
async def pydantic_validation_handler(request: Request, exc: PydanticValidationError):
    """Schema pydantic dibangun manual dari body (`ContentCreate(**raw)` di api_v1),
    jadi `extra="forbid"` TIDAK melewati RequestValidationError. Tanpa handler ini
    payload agent yang menyelipkan field boss-only (mis. `status`, `approved_at`)
    jadi 500, bukan 422 yang dijanjikan kontrak API (PRD §7d)."""
    detail = [
        {"lokasi": ".".join(str(x) for x in err.get("loc", [])), "pesan": err.get("msg", "")}
        for err in exc.errors()
    ]
    if _wants_json(request):
        return JSONResponse(
            status_code=422,
            content={
                "error": "validasi",
                "message": "Payload tidak lolos validasi",
                "detail": detail,
            },
        )
    return HTMLResponse(
        "<h1>422 — payload tidak valid</h1><pre>"
        + "\n".join(f"{d['lokasi']}: {d['pesan']}" for d in detail)
        + "</pre><p><a href='/'>Kembali</a></p>",
        status_code=422,
    )


@app.get("/healthz", include_in_schema=False)
def healthz():
    """Liveness sederhana untuk systemd/nginx (tanpa token)."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "db": engine.dialect.name}
    except Exception as exc:  # pragma: no cover
        return JSONResponse(status_code=503, content={"status": "degraded", "error": str(exc)})
