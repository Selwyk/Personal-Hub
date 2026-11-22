from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from app.database.session import engine, get_db
from app.security.service import get_session, csrf, signed_in
from app.core.config import settings
from app.core.product import NAME, VERSION
from app.security.routes import auth, security
from app.modules.journal.routes import router as journal
from app.modules.ingestion.routes import router as ingestion
from app.modules.tasks.routes import router as tasks
from app.core.dashboard import router as dashboard, register
from app.core.routes import router as catalogs
from app.modules.journal.routes import JournalWidgetProvider
from app.security.bootstrap import bootstrap_user


@asynccontextmanager
async def lifespan(app):
    bootstrap_user()
    yield


def browser_guard(request: Request, db=Depends(get_db, scope="function")):
    bearer = request.headers.get("authorization")
    collector_endpoint = request.method == "POST" and request.url.path == "/api/v1/ingest/batches"
    if bearer and not collector_endpoint:
        raise HTTPException(401, "Credenciales no válidas para este recurso.")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not collector_endpoint:
        csrf(request, get_session(request, db))

app = FastAPI(title=NAME, version=VERSION, lifespan=lifespan, openapi_url="/api/v1/openapi.json",
              docs_url="/api/v1/docs", dependencies=[Depends(browser_guard)])
app.include_router(auth, prefix="/api/v1")
app.include_router(security, prefix="/api/v1")
app.include_router(journal, prefix="/api/v1")
app.include_router(catalogs, prefix="/api/v1")
app.include_router(ingestion, prefix="/api/v1")
app.include_router(tasks, prefix="/api/v1")
register(JournalWidgetProvider())
app.include_router(dashboard, prefix="/api/v1")


@app.middleware("http")
async def safe_responses(request, call_next):
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 2 * 1024 * 1024:
                return JSONResponse({"detail": "Petición demasiado grande."}, status_code=413)
        request._body = bytes(body)
    try:
        response = await call_next(request)
    except Exception:
        # No exception repr/SQL parameters: they can contain private data or credentials.
        response = JSONResponse({"detail": "No se ha podido completar la operación."}, status_code=500)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Pydantic errors include input values by default: never echo passwords or private payloads.
    return JSONResponse({"detail": "Datos no válidos.", "fields": [
        {"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]}, status_code=422)


@app.get("/api/v1/health")
def health():
    with engine().connect() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/api/v1/modules", dependencies=[Depends(signed_in)])
def modules():
    return {"tasks": settings().tasks_enabled}
