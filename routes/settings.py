"""Settings – owner only (SRS 3, 5.9, FR-9.2, FR-11.4). Design: docs/design/Settings.dc.html.

Business & licence, GST / cap / refund shares, serial numbers, bank accounts, country stages & default
charges (new GCC countries without code changes), users & roles. Message rules live on the Messages page.
"""
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from common.auth import ROLES, hash_password, require_role
from common.db import audit, execute, fetch_all, fetch_one, insert, transaction, update
from common.stages import ORDER, SERVICES, VISA_TYPES
from common.web import form_data, num, render

router = APIRouter(prefix="/app/settings", tags=["settings"])
OWNER = require_role("owner")
BUSINESS_KEYS = [("gstin", "GSTIN"), ("ra_licence_no", "RA licence number"), ("ra_valid_till", "Licence valid till"),
                 ("sac_code", "SAC code"), ("upi_id", "UPI ID (shown in balance reminders)"), ("portal_url", "Website address (for links)")]
MONEY_KEYS = [("service_charge_cap", "Service charge cap (₹)"), ("gst_rate", "GST on service charge (%)"),
              ("next_file_serial", "Next file number"), ("next_ticket_no", "Next ticket number"), ("receipt_prefix", "Receipt prefix")]
MAX_STAFF = 4


def _settings():
    return {r["key"]: r["value"] for r in fetch_all("SELECT key, value FROM settings LIMIT 200")}


@router.get("")
def settings_page(request: Request, country: str = "Saudi Arabia", saved: str = "", error: str = "", user=Depends(OWNER)):
    s = _settings()
    countries = [r["country"] for r in fetch_all("SELECT DISTINCT country FROM template_stages ORDER BY 1 LIMIT 20")]
    stages = fetch_all("SELECT * FROM template_stages WHERE country = %s ORDER BY visa_type, sort_order LIMIT 100", (country,))
    users = fetch_all("SELECT id, name, username, mobile, role, is_active, last_login_at, locked_until FROM users ORDER BY is_active DESC, role, name LIMIT 50")
    return render(request, "settings/page.html", {
        "s": s, "business_keys": BUSINESS_KEYS, "money_keys": MONEY_KEYS,
        "shares": json.loads(s.get("refund_shares") or "{}"), "countries": countries, "country": country,
        "stages": stages, "users": users, "roles": ROLES, "banks": fetch_all("SELECT * FROM bank_accounts ORDER BY id LIMIT 10"),
        "SERVICES": SERVICES, "ORDER": ORDER, "VISA_TYPES": VISA_TYPES, "saved": saved, "error": error,
        "staff_used": sum(1 for u in users if u["role"] == "staff" and u["is_active"]), "max_staff": MAX_STAFF,
        "active": "settings",
    })


def _save_keys(form, keys, user):
    with transaction() as conn:
        for key, _label in keys:
            if key in form:
                val = (form.get(key) or "").strip()
                if key in ("service_charge_cap", "gst_rate", "next_file_serial", "next_ticket_no"):
                    val = str(int(num(val))) if key != "gst_rate" else str(num(val))
                conn.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                             (key, val))
        audit(user["id"], "update", "settings", None, {k: form.get(k) for k, _ in keys if k in form}, conn=conn)


@router.post("/business")
def save_business(form=Depends(form_data), user=Depends(OWNER)):
    _save_keys(form, BUSINESS_KEYS, user)
    return RedirectResponse("/app/settings?saved=business#biz", status_code=303)


@router.post("/money")
def save_money(form=Depends(form_data), user=Depends(OWNER)):
    # serials may only move forward, so a number is never given twice (FR-1.1)
    s = _settings()
    for key in ("next_file_serial", "next_ticket_no"):
        if key in form and int(num(form.get(key))) < int(s.get(key, "0") or 0):
            used = fetch_one("SELECT max(serial_no) AS n FROM files") if key == "next_file_serial" else fetch_one("SELECT max(ticket_no) AS n FROM ticket_bookings")
            if used["n"] and int(num(form.get(key))) <= used["n"]:
                return RedirectResponse(f"/app/settings?error=Number+{int(num(form.get(key)))}+is+already+used#money", status_code=303)
    _save_keys(form, MONEY_KEYS, user)
    shares = {k: num(form.get(f"share.{k}")) for k in ["registration"] + ORDER if f"share.{k}" in form}
    if shares:
        execute("UPDATE settings SET value = %s WHERE key = 'refund_shares'", (json.dumps(shares),))
    return RedirectResponse("/app/settings?saved=money#money", status_code=303)


@router.post("/banks")
def save_banks(form=Depends(form_data), user=Depends(OWNER)):
    with transaction() as conn:
        for b in conn.execute("SELECT id FROM bank_accounts LIMIT 20").fetchall():
            i = b["id"]
            if f"name.{i}" in form:
                conn.execute("UPDATE bank_accounts SET name = %s, account_no = %s, ifsc = %s, is_active = %s WHERE id = %s",
                             ((form.get(f"name.{i}") or "").strip(), (form.get(f"acc.{i}") or "").strip() or None,
                              (form.get(f"ifsc.{i}") or "").strip().upper() or None, bool(form.get(f"active.{i}")), i))
        if (form.get("new_name") or "").strip():
            insert("bank_accounts", {"name": form.get("new_name").strip(), "account_no": (form.get("new_acc") or "").strip() or None,
                                     "ifsc": (form.get("new_ifsc") or "").strip().upper() or None}, conn=conn)
        audit(user["id"], "update", "bank_accounts", None, {}, conn=conn)
    return RedirectResponse("/app/settings?saved=banks#money", status_code=303)


@router.post("/stages")
def save_stages(form=Depends(form_data), user=Depends(OWNER)):
    """FR-9.2: order, default on/off and default charge per country + visa type."""
    country = form.get("country") or "Saudi Arabia"
    with transaction() as conn:
        for r in conn.execute("SELECT id FROM template_stages WHERE country = %s LIMIT 200", (country,)).fetchall():
            i = r["id"]
            if f"order.{i}" in form:
                conn.execute("UPDATE template_stages SET sort_order = %s, default_on = %s, default_charge = %s WHERE id = %s",
                             (int(num(form.get(f"order.{i}"), 1)), bool(form.get(f"on.{i}")), num(form.get(f"charge.{i}"), None), i))
        add_vt, add_svc = form.get("add_visa_type"), form.get("add_service")
        if add_vt in dict((k, 1) for k, _l, _h in VISA_TYPES) and add_svc in SERVICES:
            conn.execute("""INSERT INTO template_stages (country, visa_type, service, sort_order, default_on, default_charge)
                            VALUES (%s, %s, %s, %s, true, %s) ON CONFLICT (country, visa_type, service) DO NOTHING""",
                         (country, add_vt, add_svc, ORDER.index(add_svc) + 1, num(form.get("add_charge"), None)))
        audit(user["id"], "update", "template_stages", None, {"country": country}, conn=conn)
    return RedirectResponse(f"/app/settings?country={country}&saved=stages#tpl", status_code=303)


@router.post("/stages/country")
def add_country(name: str = Form(..., min_length=2, max_length=60), user=Depends(OWNER)):
    """A new GCC country starts as a copy of Saudi Arabia's stages, then the owner adjusts it."""
    name = name.strip().title()
    with transaction() as conn:
        conn.execute("""INSERT INTO template_stages (country, visa_type, service, sort_order, default_on, default_charge)
                        SELECT %s, visa_type, service, sort_order, default_on, default_charge FROM template_stages
                        WHERE country = 'Saudi Arabia' ON CONFLICT DO NOTHING""", (name,))
        audit(user["id"], "create", "template_stages", None, {"country": name}, conn=conn)
    return RedirectResponse(f"/app/settings?country={name}&saved=stages#tpl", status_code=303)


@router.post("/users")
def save_user(uid: str = Form(""), name: str = Form(..., min_length=2, max_length=80), username: str = Form(..., min_length=3, max_length=40),
              mobile: str = Form("", max_length=15), role: str = Form(...), password: str = Form(""), is_active: str = Form(""),
              user=Depends(OWNER)):
    """Add / edit staff and accountant. Users are deactivated, never deleted (audit history)."""
    if role not in ROLES:
        raise HTTPException(400)
    username = username.strip().lower()
    clash = fetch_one("SELECT id FROM users WHERE lower(username) = %s AND id::text <> %s", (username, uid or "0"))
    if clash:
        return RedirectResponse("/app/settings?error=Username+already+taken#users", status_code=303)
    if password and len(password) < 8:
        return RedirectResponse("/app/settings?error=Password+must+be+at+least+8+characters#users", status_code=303)
    data = {"name": name.strip(), "username": username, "mobile": mobile.strip() or None, "role": role}
    if uid.isdigit():
        if int(uid) == user["id"]:
            data["role"] = "owner"                       # the owner cannot lock themselves out
            data["is_active"] = True
        else:
            data["is_active"] = bool(is_active)
        if password:
            data.update(password_hash=hash_password(password), failed_logins=0, locked_until=None)
        row = update("users", int(uid), data)
        audit(user["id"], "update", "user", row["id"], {"role": role, "active": data["is_active"], "password_reset": bool(password)})
    else:
        if not password:
            return RedirectResponse("/app/settings?error=Set+a+password+for+the+new+user#users", status_code=303)
        staff = fetch_one("SELECT count(*) AS n FROM users WHERE role = 'staff' AND is_active")["n"]
        if role == "staff" and staff >= MAX_STAFF:
            return RedirectResponse(f"/app/settings?error=All+{MAX_STAFF}+staff+seats+are+used#users", status_code=303)
        row = insert("users", {**data, "password_hash": hash_password(password)})
        audit(user["id"], "create", "user", row["id"], {"role": role})
    return RedirectResponse("/app/settings?saved=users#users", status_code=303)


@router.post("/users/{uid}/unlock")
def unlock_user(uid: int, user=Depends(OWNER)):
    execute("UPDATE users SET failed_logins = 0, locked_until = NULL WHERE id = %s", (uid,))
    audit(user["id"], "unlock", "user", uid, {})
    return RedirectResponse("/app/settings?saved=users#users", status_code=303)
