"""Charges, payments, GST receipts and refunds (SRS 6, FR-14.1–14.8). Design: docs/design/Payments.dc.html.

Payments are never updated or deleted – a correction is a negative 'reversal' row (FR-14.8).
"""
import json
import os
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from common.auth import require_role, require_user
from common.db import audit, fetch_all, fetch_one, insert, paginate, transaction
from common.messages import queue_message
from common.money import booking_totals, file_totals, next_receipt_no, rupees_in_words
from common.stages import ORDER, SERVICES, refresh_file_stage, setting, to_date
from common.web import BUSINESS, num, pager, render

router = APIRouter(prefix="/app/payments", tags=["payments"])
public = APIRouter(tags=["receipt"])         # /r/{token} – the customer opens it from WhatsApp
WRITE = require_role("owner", "staff")
MODES = [("upi", "UPI"), ("bank", "Bank transfer"), ("cheque", "Cheque"), ("dd", "Demand draft"), ("cash", "Cash")]
_signer = URLSafeSerializer(os.environ.get("SECRET_KEY", "dev"), salt="receipt")


def public_receipt_url(payment_id):
    return f"{BUSINESS['site_url']}/r/{_signer.dumps(payment_id)}"


# ---------------------------------------------------------------- list

@router.get("")
def payment_list(request: Request, start: str = "", end: str = "", direction: str = "", mode: str = "", bank: str = "",
                 staff: str = "", party: str = "", file_id: str = "", booking_id: str = "",
                 pg: dict = Depends(pager), user=Depends(require_role("owner", "staff", "accountant"))):
    where, params = ["true"], {}
    if to_date(start):
        where.append("p.paid_on >= %(start)s"); params["start"] = to_date(start)
    if to_date(end):
        where.append("p.paid_on <= %(end)s"); params["end"] = to_date(end)
    if direction in ("in", "out"):
        where.append("p.direction = %(dir)s"); params["dir"] = direction
    if mode in dict(MODES):
        where.append("p.mode = %(mode)s"); params["mode"] = mode
    for key, col in (("bank", "bank_account_id"), ("staff", "created_by"), ("party", "party_id"),
                     ("file_id", "file_id"), ("booking_id", "booking_id")):
        val = locals()[key]
        if val.isdigit():
            where.append(f"p.{col} = %({key})s"); params[key] = int(val)
    w = " AND ".join(where)
    result = paginate(f"""
        SELECT p.*, a.name AS account, c.name AS customer, c.id AS cust_id, pt.name AS party, f.serial_no, t.ticket_no,
               u.name AS staff_name, EXISTS (SELECT 1 FROM payments r WHERE r.reverses_id = p.id) AS reversed
        FROM payments p LEFT JOIN bank_accounts a ON a.id = p.bank_account_id
        LEFT JOIN customers c ON c.id = p.customer_id LEFT JOIN parties pt ON pt.id = p.party_id
        LEFT JOIN files f ON f.id = p.file_id LEFT JOIN ticket_bookings t ON t.id = p.booking_id
        LEFT JOIN users u ON u.id = p.created_by
        WHERE {w} ORDER BY p.paid_on DESC, p.id DESC
    """, params, **pg)
    totals = fetch_one(f"""
        SELECT coalesce(sum(amount) FILTER (WHERE direction = 'in'), 0) AS money_in,
               coalesce(sum(amount) FILTER (WHERE direction = 'out' AND kind = 'expense'), 0) AS money_out,
               coalesce(sum(amount) FILTER (WHERE direction = 'out' AND kind = 'refund'), 0) AS refunds,
               count(*) AS n
        FROM payments p WHERE {w}
    """, params)
    subject, money = None, None
    if file_id.isdigit():
        subject = fetch_one("SELECT f.id, f.serial_no, c.name FROM files f JOIN customers c ON c.id = f.customer_id WHERE f.id = %s", (int(file_id),))
        money = file_totals(int(file_id))
    elif booking_id.isdigit():
        subject = fetch_one("SELECT t.id, t.ticket_no, c.name FROM ticket_bookings t JOIN customers c ON c.id = t.customer_id WHERE t.id = %s", (int(booking_id),))
        money = booking_totals(int(booking_id))
    filters = dict(start=start, end=end, direction=direction, mode=mode, bank=bank, staff=staff, party=party,
                   file_id=file_id, booking_id=booking_id)
    return render(request, "payments/list.html", {
        **filters, "result": result, "totals": totals, "subject": subject, "money": money, "modes": MODES,
        "banks": fetch_all("SELECT id, name FROM bank_accounts ORDER BY id LIMIT 10"),
        "staff_list": fetch_all("SELECT id, name FROM users ORDER BY name LIMIT 50"),
        "filters_on": sum(1 for k in ("start", "end", "direction", "mode", "bank", "staff", "party") if filters[k]),
        "active": "payments",
    }, partial="payments/_rows.html")


# ---------------------------------------------------------------- record a payment (FR-14.2, 14.4)

def _target(file_id, booking_id, ref=""):
    """Resolve what the payment is for: ?file_id / ?booking_id, or '4277' / 'T-3453' typed in the form."""
    ref = (ref or "").strip().upper().lstrip("#")
    if not file_id and not booking_id and ref:
        if ref.startswith("T"):
            row = fetch_one("SELECT id FROM ticket_bookings WHERE ticket_no::text = %s", (ref.lstrip("T-"),))
            booking_id = row and row["id"]
        elif ref.isdigit():
            row = fetch_one("SELECT id FROM files WHERE serial_no = %s", (int(ref),))
            file_id = row and row["id"]
    if file_id:
        f = fetch_one("SELECT f.id, f.serial_no, f.customer_id, f.service_charge, c.name, c.mobile FROM files f"
                      " JOIN customers c ON c.id = f.customer_id WHERE f.id = %s", (file_id,))
        return f and {"kind": "file", "id": f["id"], "label": f"#{f['serial_no']} {f['name']}", "customer_id": f["customer_id"],
                      "mobile": f["mobile"], "file_id": f["id"], "booking_id": None, "totals": file_totals(f["id"])}
    if booking_id:
        t = fetch_one("SELECT t.id, t.ticket_no, t.customer_id, t.file_id, t.supplier_id, c.name, c.mobile FROM ticket_bookings t"
                      " JOIN customers c ON c.id = t.customer_id WHERE t.id = %s", (booking_id,))
        return t and {"kind": "ticket", "id": t["id"], "label": f"T-{t['ticket_no']} {t['name']}", "customer_id": t["customer_id"],
                      "mobile": t["mobile"], "file_id": t["file_id"], "booking_id": t["id"], "supplier_id": t["supplier_id"],
                      "totals": booking_totals(t["id"])}
    return None


def _form_ctx(target, v=None, errors=None, warn=None):
    return {"target": target, "v": v or {}, "errors": errors or {}, "warn": warn, "modes": MODES,
            "banks": fetch_all("SELECT id, name, is_cash FROM bank_accounts WHERE is_active ORDER BY id LIMIT 10"),
            "parties": fetch_all("SELECT id, name, kind, default_rate FROM parties WHERE is_active ORDER BY kind, name LIMIT 100"),
            "today": date.today(), "active": "payments"}


@router.get("/new")
def new_payment(request: Request, file_id: int | None = None, booking_id: int | None = None, ref: str = "",
                direction: str = "in", user=Depends(WRITE)):
    target = _target(file_id, booking_id, ref)
    v = {"direction": direction, "ref": ref}
    if target and target.get("supplier_id") and direction == "out":
        v["party_id"] = target["supplier_id"]
    return render(request, "payments/form.html", _form_ctx(target, v, {"ref": "No file or ticket with that number."} if ref and not target else None))


@router.post("")
def create_payment(
    request: Request, file_id: str = Form(""), booking_id: str = Form(""), ref: str = Form(""),
    direction: str = Form("in"), amount: str = Form(""), paid_on: str = Form(""), against: str = Form("service_charge"),
    mode: str = Form("upi"), ref_no: str = Form("", max_length=60), bank_account_id: str = Form(""), party_id: str = Form(""),
    note: str = Form("", max_length=300), send_receipt: str = Form(""), cash_ok: str = Form(""), user=Depends(WRITE),
):
    target = _target(int(file_id) if file_id.isdigit() else None, int(booking_id) if booking_id.isdigit() else None, ref)
    v = dict(direction=direction, amount=amount, paid_on=paid_on, against=against, mode=mode, ref_no=ref_no,
             bank_account_id=bank_account_id, party_id=party_id, note=note, send_receipt=send_receipt, ref=ref)
    errors = {}
    amt = num(amount)
    if not target: errors["ref"] = "Choose the file or ticket this payment is for."
    if amt <= 0: errors["amount"] = "Enter the amount."
    if direction not in ("in", "out"): errors["direction"] = "Choose the type."
    if mode not in dict(MODES): errors["mode"] = "Choose the mode."
    if direction == "in" and not bank_account_id.isdigit(): errors["bank_account_id"] = "Choose where it was received."
    if direction == "out" and not party_id.isdigit(): errors["party_id"] = "Choose the centre / agency / supplier paid."
    if errors:
        return render(request, "payments/form.html", _form_ctx(target, v, errors))
    # FR-14.2 / Rule 25: service charge only by digital mode or DD – warn, never block
    if direction == "in" and against == "service_charge" and mode == "cash" and not cash_ok:
        return render(request, "payments/form.html", _form_ctx(target, v, warn=(
            "Service charge should be taken by UPI, bank transfer, cheque or DD (Emigration Rules). "
            "Tick “Record anyway” if you still want to save this cash payment.")))

    with transaction() as conn:
        pay = insert("payments", {
            "direction": direction, "kind": "payment" if direction == "in" else "expense",
            "file_id": target["file_id"], "booking_id": target["booking_id"], "customer_id": target["customer_id"],
            "party_id": int(party_id) if direction == "out" else None,
            "bank_account_id": int(bank_account_id) if bank_account_id.isdigit() else None,
            "amount": amt, "mode": mode, "ref_no": ref_no.strip() or None, "paid_on": to_date(paid_on) or date.today(),
            "receipt_no": next_receipt_no(conn) if direction == "in" else None,
            "note": note.strip() or {"service_charge": "Service charge", "costs": "Costs", "retest": "Retest charge"}.get(against),
            "for_service_charge": direction == "in" and against == "service_charge", "created_by": user["id"],
        }, conn=conn)
        entity = "file" if target["kind"] == "file" else "ticket"
        audit(user["id"], "payment", entity, target["id"], {"payment_id": pay["id"], "amount": amt, "direction": direction}, conn=conn)
        msg = None
        if direction == "in" and send_receipt:
            totals = file_totals(target["id"], conn) if target["kind"] == "file" else booking_totals(target["id"], conn)
            msg = queue_message(conn, "payment_received", target["customer_id"], file_id=target["file_id"],
                                booking_id=target["booking_id"], amount=amt, date=pay["paid_on"].strftime("%d %b %Y"),
                                balance=totals["balance"], receipt_link=public_receipt_url(pay["id"]))
    if direction == "in":
        return RedirectResponse(f"/app/payments/{pay['id']}/receipt" + (f"?msg={msg['id']}" if msg else ""), status_code=303)
    back = f"/app/files/{target['file_id']}" if target["kind"] == "file" else f"/app/tickets/{target['booking_id']}"
    return RedirectResponse(back, status_code=303)


# ---------------------------------------------------------------- receipt (FR-14.6)

def _receipt_ctx(payment_id):
    p = fetch_one("""
        SELECT p.*, a.name AS account, c.name, c.mobile, c.address, c.district, c.state, f.serial_no, t.ticket_no
        FROM payments p LEFT JOIN bank_accounts a ON a.id = p.bank_account_id LEFT JOIN customers c ON c.id = p.customer_id
        LEFT JOIN files f ON f.id = p.file_id LEFT JOIN ticket_bookings t ON t.id = p.booking_id
        WHERE p.id = %s AND p.direction = 'in'
    """, (payment_id,))
    if not p:
        raise HTTPException(404)
    gst = float(setting("gst_rate", "18") or 0)
    amount = float(p["amount"])
    if p["for_service_charge"] and gst:
        taxable = round(amount / (1 + gst / 100), 2)
        cgst = round((amount - taxable) / 2, 2)
        lines = [("Recruitment service charge" + (" – part payment" if p["file_id"] else ""), True, taxable),
                 (f"CGST {gst / 2:g}%", False, cgst), (f"SGST {gst / 2:g}%", False, round(amount - taxable - cgst, 2))]
    else:
        lines = [((p["note"] or "Payment") + (" (pass-through costs, paid on your behalf)" if p["file_id"] and not p["booking_id"] else ""), False, amount)]
    totals = file_totals(p["file_id"]) if p["file_id"] and not p["booking_id"] else booking_totals(p["booking_id"]) if p["booking_id"] else None
    return {"p": p, "lines": lines, "words": rupees_in_words(amount), "totals": totals,
            "gstin": setting("gstin") or BUSINESS["gstin"], "ra": setting("ra_licence_no") or BUSINESS["ra_licence"],
            "sac": setting("sac_code"), "banks": fetch_all("SELECT * FROM bank_accounts WHERE NOT is_cash AND is_active LIMIT 5"),
            "public_url": public_receipt_url(p["id"])}


@router.get("/{payment_id}/receipt")
def receipt(payment_id: int, request: Request, msg: int | None = None, user=Depends(require_user)):
    ctx = _receipt_ctx(payment_id)
    ctx["toast"] = fetch_one("SELECT * FROM messages WHERE id = %s", (msg,)) if msg else None
    return render(request, "payments/receipt.html", {**ctx, "staff_view": True, "active": "payments"})


@public.get("/r/{token}")
def public_receipt(token: str, request: Request):
    try:
        payment_id = _signer.loads(token)
    except BadSignature:
        raise HTTPException(404)
    return render(request, "payments/receipt.html", {**_receipt_ctx(int(payment_id)), "staff_view": False})


# ---------------------------------------------------------------- reversal (FR-14.8)

@router.post("/{payment_id}/reverse")
def reverse_payment(payment_id: int, reason: str = Form(..., min_length=3, max_length=200), user=Depends(WRITE)):
    with transaction() as conn:
        p = conn.execute("SELECT * FROM payments WHERE id = %s FOR UPDATE", (payment_id,)).fetchone()
        if not p or p["kind"] == "reversal":
            raise HTTPException(404)
        if conn.execute("SELECT 1 FROM payments WHERE reverses_id = %s", (payment_id,)).fetchone():
            raise HTTPException(409, "Already reversed")
        r = insert("payments", {
            "direction": p["direction"], "kind": "reversal" if p["direction"] == "in" else p["kind"],
            "file_id": p["file_id"], "booking_id": p["booking_id"], "customer_id": p["customer_id"], "party_id": p["party_id"],
            "bank_account_id": p["bank_account_id"], "amount": -p["amount"], "mode": p["mode"], "reverses_id": p["id"],
            "note": f"Reversal of {p['receipt_no'] or '#' + str(p['id'])}: {reason.strip()}", "created_by": user["id"],
        }, conn=conn)
        audit(user["id"], "payment_reversal", "file" if p["file_id"] else "ticket", p["file_id"] or p["booking_id"],
              {"payment_id": p["id"], "reversal_id": r["id"], "reason": reason.strip()}, conn=conn)
    return RedirectResponse(f"/app/payments?{'file_id=' + str(p['file_id']) if p['file_id'] else 'booking_id=' + str(p['booking_id'])}",
                            status_code=303)


# ---------------------------------------------------------------- refund on cancellation (FR-14.7)

def _refund_calc(file_id):
    f = fetch_one("SELECT f.*, c.name, c.mobile FROM files f JOIN customers c ON c.id = f.customer_id WHERE f.id = %s", (file_id,))
    if not f:
        raise HTTPException(404)
    t = file_totals(file_id)
    shares = json.loads(setting("refund_shares", "{}") or "{}")
    done = [r["service"] for r in fetch_all("SELECT service FROM file_services WHERE file_id = %s AND status = 'done' LIMIT 20", (file_id,))]
    reached = max(done, key=ORDER.index) if done else "registration"
    pct = float(shares.get(reached, shares.get("registration", 0)))
    gst = float(setting("gst_rate", "18") or 0)
    earned = round(float(f["service_charge"] or 0) * pct / 100 * (1 + gst / 100), 2)
    received = float(t["received"] - t["refunded"])
    costs = float(t["expenses"])
    return {"f": f, "t": t, "reached": "Registration" if reached == "registration" else SERVICES[reached]["label"] + " done",
            "pct": pct, "earned": earned, "received": received, "costs": costs,
            "proposed": max(round(received - costs - earned, 2), 0),
            "done_refunds": fetch_all("SELECT r.*, u.name AS by_name FROM refunds r LEFT JOIN users u ON u.id = r.approved_by"
                                      " WHERE r.file_id = %s ORDER BY r.created_at DESC LIMIT 10", (file_id,))}


@router.get("/refund")
def refund_page(request: Request, file_id: int, user=Depends(require_role("owner", "staff"))):
    ctx = _refund_calc(file_id)
    return render(request, "payments/refund.html", {**ctx, "banks": fetch_all("SELECT id, name FROM bank_accounts WHERE is_active LIMIT 10"),
                                                    "modes": MODES, "active": "payments"})


@router.post("/refund")
def approve_refund(file_id: int = Form(...), amount: str = Form(...), reason: str = Form(..., min_length=3, max_length=300),
                   mode: str = Form("bank"), bank_account_id: str = Form(""), release_visa: str = Form(""),
                   user=Depends(require_role("owner"))):
    """Only the owner approves (SRS 3). Staff can only see the calculation."""
    calc = _refund_calc(file_id)
    amt = num(amount)
    with transaction() as conn:
        pay = None
        if amt > 0:
            pay = insert("payments", {
                "direction": "out", "kind": "refund", "file_id": file_id, "customer_id": calc["f"]["customer_id"],
                "bank_account_id": int(bank_account_id) if bank_account_id.isdigit() else None, "amount": amt,
                "mode": mode if mode in dict(MODES) else "bank", "note": f"Refund on cancellation: {reason.strip()}",
                "created_by": user["id"],
            }, conn=conn)
        insert("refunds", {"file_id": file_id, "received": calc["received"], "costs_paid": calc["costs"], "earned": calc["earned"],
                           "amount": amt, "stage_reached": calc["reached"], "reason": reason.strip(),
                           "payment_id": pay and pay["id"], "approved_by": user["id"]}, conn=conn)
        conn.execute("UPDATE files SET status = 'cancelled', cancel_reason = coalesce(cancel_reason, %s), updated_at = now()"
                     " WHERE id = %s", (reason.strip(), file_id))
        if release_visa and calc["f"]["visa_id"]:
            conn.execute("UPDATE visa_assignments SET released_on = now(), reason = 'Candidate cancelled – refund done'"
                         " WHERE file_id = %s AND released_on IS NULL", (file_id,))
        refresh_file_stage(conn, file_id)
        audit(user["id"], "refund", "file", file_id, {"amount": amt, "proposed": calc["proposed"], "reason": reason.strip()}, conn=conn)
    return RedirectResponse(f"/app/files/{file_id}", status_code=303)
