"""Noor Travels Services – app entry point.

Run (dev):   uvicorn main:app --reload --port 8030
Run (prod):  python main.py   (reads HOST/PORT from .env; behind nginx)
"""
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.middleware.gzip import GZipMiddleware  # noqa: E402
from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

from common.auth import Forbidden, LoginRequired  # noqa: E402
from common.db import pool  # noqa: E402
from common.web import is_htmx_partial, render  # noqa: E402
from routes import all_routers  # noqa: E402

IS_PROD = os.getenv("APP_ENV", "dev") == "prod"


@asynccontextmanager
async def lifespan(app):
    pool.open(wait=True)
    yield
    pool.close()


app = FastAPI(
    title="Noor Travels Services",
    lifespan=lifespan,
    docs_url=None if IS_PROD else "/docs",
    redoc_url=None,
    openapi_url=None if IS_PROD else "/openapi.json",
)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SECRET_KEY"],
    session_cookie="nts_session",
    max_age=60 * 60 * 12,  # 12 hours
    same_site="lax",
    https_only=IS_PROD,
)
app.add_middleware(GZipMiddleware, minimum_size=800, compresslevel=6)   # HTML/JSON/CSS ~70% smaller on slow 4G


class CachedStatic(StaticFiles):
    """/static/x?v=<hash> (see asset() in common/web.py) never changes → browsers keep it for a year."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if b"v=" in scope.get("query_string", b"") and response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", CachedStatic(directory="static"), name="static")


@app.middleware("http")
async def html_headers(request: Request, call_next):
    """HTML is always fresh (data changes), and full pages / HTMX fragments are cached separately."""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers.setdefault("Cache-Control", "no-cache")
        response.headers["Vary"] = "HX-Request, Accept-Encoding"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
    return response

for router in all_routers:
    app.include_router(router)


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc):
    if is_htmx_partial(request):
        # HTMX fragment request: tell htmx to do a full redirect
        return HTMLResponse("", headers={"HX-Redirect": "/login"})
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


@app.exception_handler(Forbidden)
async def forbidden(request: Request, exc):
    return render(request, "errors/403.html", status_code=403)


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return render(request, "errors/404.html", status_code=404)


@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8030")),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        workers=int(os.getenv("WORKERS", "2")),
    )
