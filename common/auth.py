"""Login, password hashing and role checks.

Use in a route:
    user = Depends(require_user)                 # any logged-in staff
    user = Depends(require_role("owner"))        # owner only
"""
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Request

from common.db import execute, fetch_one

ROLES = ("owner", "staff", "accountant")
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 30


class LoginRequired(Exception):
    """Raised when there is no valid session; main.py turns it into a redirect."""


class Forbidden(Exception):
    """Raised when the user's role may not open the page."""


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_login(username, password):
    """Return (user, error). Locks the account after 5 wrong passwords (SRS 7)."""
    user = fetch_one(
        "SELECT * FROM users WHERE lower(username) = lower(%s) AND is_active",
        (username.strip(),),
    )
    if not user:
        return None, "Wrong username or password."

    now = datetime.now(timezone.utc)
    if user["locked_until"] and user["locked_until"] > now:
        mins = int((user["locked_until"] - now).total_seconds() // 60) + 1
        return None, f"Too many wrong tries. Try again in {mins} min."

    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        failed = user["failed_logins"] + 1
        locked = now + timedelta(minutes=LOCK_MINUTES) if failed >= MAX_FAILED_LOGINS else None
        execute(
            "UPDATE users SET failed_logins = %s, locked_until = %s WHERE id = %s",
            (0 if locked else failed, locked, user["id"]),
        )
        return None, "Wrong username or password."

    execute(
        "UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = now() WHERE id = %s",
        (user["id"],),
    )
    return user, None


def login(request: Request, user):
    request.session.clear()
    request.session["uid"] = user["id"]


def logout(request: Request):
    request.session.clear()


def require_user(request: Request):
    uid = request.session.get("uid")
    user = uid and fetch_one(
        "SELECT id, name, username, role FROM users WHERE id = %s AND is_active", (uid,)
    )
    if not user:
        raise LoginRequired()
    request.state.user = user
    return user


def require_role(*roles):
    def checker(request: Request):
        user = require_user(request)
        if user["role"] not in roles:
            raise Forbidden()
        return user

    return checker
