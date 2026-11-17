"""Staff login / logout. Customer portal login (mobile + DOB) is added in Phase 2 (FR-12)."""
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from common.auth import check_login, login, logout
from common.db import audit
from common.web import render

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_page(request: Request, next: str = "/app"):
    if request.session.get("uid"):
        return RedirectResponse("/app", status_code=303)
    return render(request, "auth/login.html", {"next": next})


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/app"),
):
    user, error = check_login(username, password)
    if error:
        return render(request, "auth/login.html",
                      {"error": error, "username": username, "next": next})
    login(request, user)
    audit(user["id"], "login", "user", user["id"])
    # only allow redirects inside the app
    target = next if next.startswith("/app") else "/app"
    return RedirectResponse(target, status_code=303)


@router.post("/logout")
def logout_submit(request: Request):
    logout(request)
    return RedirectResponse("/login", status_code=303)
