"""Customers – the reference pattern for every list page in the app.

Copy this shape for files, tickets, payments, appointments, messages:
  * filters come in as query params and are added to one WHERE list
  * paginate() does count + LIMIT/OFFSET
  * full page on normal/boosted navigation, only the rows partial for HTMX filter calls
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from common.auth import require_role, require_user
from common.db import audit, execute, fetch_all, fetch_one, paginate, update
from common.money import customer_totals
from common.stages import to_date
from common.uploads import save_photo
from common.web import form_data, pager, render

router = APIRouter(prefix="/app/customers", tags=["customers"])


def clean_passport(value):
    return "".join(value.split()).upper() if value else value


@router.get("")
def customer_list(
    request: Request,
    q: str = "",
    state: str = "",
    has_active: str = "",          # '', 'yes', 'no'
    pg: dict = Depends(pager),
    user=Depends(require_user),
):
    where, params = ["true"], {}
    if q.strip():
        where.append("(c.name ILIKE %(q)s OR c.mobile ILIKE %(q)s OR c.passport_no ILIKE %(q)s)")
        params["q"] = f"%{q.strip()}%"
    if state:
        where.append("c.state = %(state)s")
        params["state"] = state
    if has_active == "yes":
        where.append("EXISTS (SELECT 1 FROM files f WHERE f.customer_id = c.id AND f.status = 'active')")
    elif has_active == "no":
        where.append("NOT EXISTS (SELECT 1 FROM files f WHERE f.customer_id = c.id AND f.status = 'active')")

    result = paginate(f"""
        SELECT c.id, c.name, c.mobile, c.passport_no, c.passport_expiry, c.district, c.state,
               (SELECT count(*) FROM files f WHERE f.customer_id = c.id)            AS files,
               (SELECT count(*) FROM ticket_bookings t WHERE t.customer_id = c.id)  AS tickets
        FROM customers c
        WHERE {' AND '.join(where)}
        ORDER BY c.created_at DESC, c.id DESC
    """, params, **pg)

    filters_on = sum(1 for v in (state, has_active) if v)
    return render(request, "customers/list.html", {
        "result": result, "q": q, "state": state, "has_active": has_active,
        "filters_on": filters_on, "active": "customers",
    }, partial="customers/_rows.html")


@router.get("/{customer_id}")
def customer_profile(customer_id: int, request: Request, user=Depends(require_user)):
    """Profile hub (FR-17.3). Files/tickets of one person are few, so a capped list is fine."""
    customer = fetch_one("""
        SELECT c.*, r.name AS referrer_name, r.mobile AS referrer_mobile
        FROM customers c LEFT JOIN referrers r ON r.id = c.referrer_id
        WHERE c.id = %s
    """, (customer_id,))
    if not customer:
        raise HTTPException(404)
    recent = [i for i in request.session.get("recent_customers", []) if i != customer_id]
    request.session["recent_customers"] = ([customer_id] + recent)[:5]        # FR-18.4

    files = fetch_all("""
        SELECT id, serial_no, visa_type, country, status, current_stage, created_at
        FROM files WHERE customer_id = %s ORDER BY created_at DESC LIMIT 50
    """, (customer_id,))
    tickets = fetch_all("""
        SELECT t.id, t.ticket_no, t.airline, t.pnr, t.travel_date, t.status, t.selling_price, t.file_id
        FROM ticket_bookings t WHERE t.customer_id = %(id)s
           OR EXISTS (SELECT 1 FROM ticket_passengers tp WHERE tp.booking_id = t.id AND tp.customer_id = %(id)s)
        ORDER BY t.created_at DESC LIMIT 50
    """, {"id": customer_id})
    money = customer_totals(customer_id)
    payments = fetch_all("""
        SELECT p.id, p.paid_on, p.amount, p.direction, p.kind, p.mode, p.receipt_no, f.serial_no, t.ticket_no
        FROM payments p LEFT JOIN files f ON f.id = p.file_id LEFT JOIN ticket_bookings t ON t.id = p.booking_id
        WHERE f.customer_id = %(id)s OR t.customer_id = %(id)s OR p.customer_id = %(id)s
        ORDER BY p.paid_on DESC, p.id DESC LIMIT 20
    """, {"id": customer_id})
    messages = fetch_all("""SELECT m.id, m.body, m.status, m.trigger, m.created_at, m.done_at, u.name AS done_by_name
                            FROM messages m LEFT JOIN users u ON u.id = m.done_by
                            WHERE m.customer_id = %s ORDER BY m.created_at DESC LIMIT 10""", (customer_id,))
    photos = fetch_all("""
        SELECT 'profile' AS kind, path FROM documents WHERE customer_id = %(id)s AND kind = 'profile_photo'
        UNION ALL
        SELECT 'visa', v.photo_path FROM files f JOIN visas v ON v.id = f.visa_id WHERE f.customer_id = %(id)s AND v.photo_path IS NOT NULL
        LIMIT 10
    """, {"id": customer_id})
    return render(request, "customers/profile.html", {
        "c": customer, "files": files, "tickets": tickets, "money": money, "payments": payments,
        "messages": messages, "photos": photos, "active": "customers",
    })


# ---------------------------------------------------------------- edit details (customers without a visa file too)

FIELDS = ("name", "father_name", "dob", "mobile", "family_mobile", "address", "district", "state", "pin", "passport_no",
          "passport_issue", "passport_expiry", "passport_place", "ecr", "profession", "referrer_id", "whatsapp_consent")


def _edit_ctx(c, errors=None):
    return {"c": c, "errors": errors or {}, "active": "customers",
            "referrers": fetch_all("SELECT id, name, place FROM referrers ORDER BY name LIMIT 300"),
            "states": ["Uttar Pradesh", "Bihar", "Jharkhand", "West Bengal", "Other"]}


@router.get("/{customer_id}/edit")
def edit_customer(customer_id: int, request: Request, user=Depends(require_role("owner", "staff"))):
    c = fetch_one("SELECT * FROM customers WHERE id = %s", (customer_id,))
    if not c:
        raise HTTPException(404)
    return render(request, "customers/edit.html", _edit_ctx(c))


@router.post("/{customer_id}")
def save_customer(customer_id: int, request: Request, form=Depends(form_data), user=Depends(require_role("owner", "staff"))):
    c = fetch_one("SELECT * FROM customers WHERE id = %s", (customer_id,))
    if not c:
        raise HTTPException(404)
    v = {k: (form.get(k) or "").strip() for k in FIELDS}
    v["passport_no"] = clean_passport(v["passport_no"]) or ""
    digits = re.sub(r"\D", "", v["mobile"])
    v["mobile"] = digits[2:] if len(digits) == 12 and digits.startswith("91") else digits
    errors = {}
    if not v["name"]:
        errors["name"] = "Enter the name."
    if not re.fullmatch(r"[6-9]\d{9}", v["mobile"]):
        errors["mobile"] = "Enter a 10-digit mobile number."
    if v["passport_no"] and not re.fullmatch(r"[A-Z0-9]{6,9}", v["passport_no"]):
        errors["passport_no"] = "Check the passport number."
    if v["passport_no"] and fetch_one("SELECT 1 FROM customers WHERE passport_no = %s AND id <> %s", (v["passport_no"], customer_id)):
        errors["passport_no"] = "This passport already belongs to another customer."
    if v["pin"] and not re.fullmatch(r"\d{6}", v["pin"]):
        errors["pin"] = "PIN is 6 digits."
    photo, err = save_photo(form.get("photo"), "profile") if not errors else (None, None)
    if err:
        errors["photo"] = err
    if errors:
        return render(request, "customers/edit.html", _edit_ctx({**c, **v, "whatsapp_consent": bool(v["whatsapp_consent"])}, errors))
    update("customers", customer_id, {
        "name": v["name"], "father_name": v["father_name"] or None, "dob": to_date(v["dob"]), "mobile": v["mobile"],
        "family_mobile": re.sub(r"\D", "", v["family_mobile"])[-10:] or None, "address": v["address"] or None,
        "district": v["district"] or None, "state": v["state"] or None, "pin": v["pin"] or None,
        "passport_no": v["passport_no"] or None, "passport_issue": to_date(v["passport_issue"]),
        "passport_expiry": to_date(v["passport_expiry"]), "passport_place": v["passport_place"] or None,
        "ecr": v["ecr"] if v["ecr"] in ("ECR", "ECNR") else None, "profession": v["profession"] or None,
        "referrer_id": int(v["referrer_id"]) if v["referrer_id"].isdigit() else None,
        "whatsapp_consent": bool(v["whatsapp_consent"]),
    })
    if photo:
        execute("INSERT INTO documents (customer_id, kind, path, uploaded_by) VALUES (%s, 'profile_photo', %s, %s)"
                " ON CONFLICT (customer_id, kind) DO UPDATE SET path = EXCLUDED.path, created_at = now()", (customer_id, photo, user["id"]))
    audit(user["id"], "update", "customer", customer_id, {"from": "customer edit"})
    return RedirectResponse(f"/app/customers/{customer_id}?saved=1", status_code=303)
