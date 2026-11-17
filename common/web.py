"""Template rendering, HTMX helpers, pagination params and small display filters."""
import os
from datetime import date, datetime
from urllib.parse import quote, urlencode

from fastapi import Query, Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

from common.db import MAX_PER_PAGE

templates = Jinja2Templates(directory="templates")

BUSINESS = {
    "name": "Noor Travels Services",
    "short": "NTS",
    "address": "Gorakhpur Road, Near Hitashi Hospital, Kasia, Distt. Kushinagar, U.P. 274402",
    "phones": ["99844 14350", "88819 51234"],
    "whatsapp": os.getenv("OFFICE_WHATSAPP", "919984414350"),
    # Open questions in SRS 10 – left empty until the office confirms them (FR-15.4, no invented data)
    "email": os.getenv("OFFICE_EMAIL", ""),
    "hours": os.getenv("OFFICE_HOURS", ""),
    "gstin": os.getenv("OFFICE_GSTIN", ""),
    "ra_licence": os.getenv("OFFICE_RA_LICENCE", ""),
    "site_url": os.getenv("SITE_URL", "").rstrip("/"),
    "maps": "https://www.google.com/maps/search/?api=1&query=Noor+Travels+Services+Gorakhpur+Road+Kasia+Kushinagar",
}


_ASSET_VERSIONS = {}


def asset(path):
    """'/static/css/app.css?v=1a2b3c4d' – the version is the file's mtime, so a new deploy busts the cache
    and the browser can keep the file for a year (CachedStatic in main.py)."""
    full = os.path.join("static", path)
    try:
        mtime = os.path.getmtime(full)
    except OSError:
        return f"/static/{path}"
    if _ASSET_VERSIONS.get(path, (None,))[0] != mtime:
        _ASSET_VERSIONS[path] = (mtime, format(int(mtime * 1000) % 0xFFFFFFFF, "x"))
    return f"/static/{path}?v={_ASSET_VERSIONS[path][1]}"


def is_htmx_partial(request: Request):
    """True for hx-get/hx-post calls that target part of the page.
    Boosted navigation (hx-boost links) wants the full page, so it is excluded."""
    return request.headers.get("HX-Request") == "true" and request.headers.get("HX-Boosted") != "true"


def render(request: Request, template, context=None, partial=None, status_code=200):
    """Render a full page, or only `partial` when HTMX asked for a fragment.

        return render(request, "customers/list.html", ctx, partial="customers/_rows.html")
    """
    ctx = {"request": request, "user": getattr(request.state, "user", None), **(context or {})}
    name = partial if (partial and is_htmx_partial(request)) else template
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def pager(page: int = Query(1), per_page: int = Query(25)):
    """FastAPI dependency: `pg: dict = Depends(pager)` -> {"page": 1, "per_page": 25}.
    Out-of-range values are clamped (1..100 per page) instead of failing with a 422."""
    return {"page": max(page, 1), "per_page": min(max(per_page, 1), MAX_PER_PAGE)}


# ---------- Jinja filters / globals ----------

def inr(value):
    """12500 -> '₹12,500' (Indian grouping)."""
    if value is None:
        return "—"
    n = int(round(float(value)))
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return f"{sign}₹{s}"


def dmy(value, with_time=False):
    """date/datetime -> '16 Nov 2026' (or '16 Nov 2026, 3:40 pm'). Shown in India time."""
    if not value:
        return "—"
    if isinstance(value, str):                # '2026-11-21' from a stage's saved details
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return value
    if isinstance(value, datetime) and value.tzinfo is not None:
        value = value.astimezone(_IST)
    if isinstance(value, datetime) and with_time and (value.hour, value.minute) == (0, 0):
        with_time = False                     # flight with no time entered – show the date only
    if isinstance(value, datetime) and with_time:
        return value.strftime("%d %b %Y, %I:%M ") + value.strftime("%p").lower()
    if isinstance(value, (date, datetime)):
        return value.strftime("%d %b %Y")
    return str(value)


def wa_link(mobile, text=""):
    """Free WhatsApp click-to-chat link (SRS FR-11.1). Adds 91 for 10-digit numbers."""
    digits = "".join(ch for ch in str(mobile or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return f"https://wa.me/{digits}?text={quote(text)}"


def mask_passport(value):
    """Customer portal shows only the last 4 characters (FR-12.3)."""
    return "•••• " + value[-4:] if value else "—"


def page_url(request: Request, page):
    """Same URL with a different ?page= – used by the pagination partial."""
    params = dict(request.query_params)
    params["page"] = page
    return f"{request.url.path}?{urlencode(params)}"


def hm(value):
    """time -> '9:30 am'."""
    if not value:
        return ""
    return value.strftime("%I:%M ").lstrip("0") + value.strftime("%p").lower()


def hm_str(value):
    """'14:30' -> '2:30 pm' (times saved in stage details)."""
    try:
        return hm(datetime.strptime(value, "%H:%M").time())
    except (TypeError, ValueError):
        return value or ""


def ago(value):
    """datetime -> 'Today' / 'Yesterday' / '3 days ago' (list 'Updated' column)."""
    if not value:
        return "—"
    days = (date.today() - (value.date() if isinstance(value, datetime) else value)).days
    return "Today" if days <= 0 else "Yesterday" if days == 1 else f"{days} days ago"


def icon(name, cls="icon"):
    """<svg> that points into the cached sprite static/img/icons.svg."""
    return Markup(f'<svg class="{escape(cls)}" aria-hidden="true"><use href="{asset("img/icons.svg")}#i-{escape(name)}"/></svg>')


def nav_counts():
    """Badges in the sidebar / tab bar: WhatsApp to send and new enquiries. One tiny indexed query per full page."""
    from common.db import fetch_one
    return fetch_one("""SELECT (SELECT count(*) FROM messages WHERE status = 'to_send' AND due_at <= now()) AS to_send,
                               (SELECT count(*) FROM enquiries WHERE status = 'new') AS enquiries""")


from common.stages import stage_label  # noqa: E402

templates.env.filters.update(inr=inr, dmy=dmy, mask_passport=mask_passport, hm=hm, hm_str=hm_str, ago=ago, stage=stage_label)
templates.env.globals.update(biz=BUSINESS, wa_link=wa_link, page_url=page_url, today=date.today, asset=asset, icon=icon,
                             nav_counts=nav_counts)


async def form_data(request: Request):
    """Dependency for sync routes that need dynamic field names (e.g. 'medical.date') or uploads:
    `form = Depends(form_data)` – the async part (reading the body) runs before the route."""
    return await request.form()


def num(value, default=0):
    """'30,000' / '' / None -> Decimal-safe float string for numeric columns."""
    s = "".join(ch for ch in str(value or "") if ch.isdigit() or ch in ".-")
    try:
        return round(float(s), 2) if s else default
    except ValueError:
        return default
