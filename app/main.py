import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.agents import bus
from app.db import SessionLocal, engine
from app.models import Base
from app.routes import admin, agents_api, auth, public, staff
from app.security import NeedLogin
from app.seed import seed_demo
from app.web import render

log = logging.getLogger("eventsphere")


def run_sweep():
    """Timer job: the WaitlistAgent acts on its own, no user involved."""
    with SessionLocal() as db:
        bus.agents["WaitlistAgent"].tick(db)
        db.commit()


async def _sweeper():
    while True:
        await asyncio.sleep(config.SWEEP_SECONDS)
        try:
            await asyncio.to_thread(run_sweep)
        except Exception:
            log.exception("waitlist sweep failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        bus.init_state(db)
        db.commit()
        if config.SEED_DEMO:
            seed_demo(db)
    task = asyncio.create_task(_sweeper()) if config.ENABLE_SCHEDULER else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="EventSphere", description="Agent-based venue booking and ticketing", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, https_only=config.COOKIE_SECURE,
                   same_site="lax", max_age=60 * 60 * 8)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
for r in (auth.router, public.router, staff.router, admin.router, agents_api.router):
    app.include_router(r)


@app.exception_handler(NeedLogin)
async def need_login(request: Request, exc: NeedLogin):
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api") or request.url.path == "/staff/scan":
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return render(request, "error.html", status_code=exc.status_code, code=exc.status_code, detail=exc.detail)


@app.get("/health")
def health():
    return {"status": "ok"}
