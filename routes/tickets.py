"""Ticket desk (SRS 5.7). Designs: docs/design/Tickets.dc.html, MobileTickets.dc.html.

One booking = one or more passengers + one or more legs (FR-7.2). Tickets booked for a visa file carry
file_id and show in the file, here and on the customer profile (FR-7.5, FR-17.5).
"""
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from common.auth import require_role, require_user
from common.db import audit, fetch_all, fetch_one, insert, paginate, transaction
from common.messages import IST, queue_message
from common.money import BOOKING_BALANCE_SQL, booking_totals, next_receipt_no
from common.stages import next_serial, refresh_file_stage, setting, to_date, to_time
from common.web import form_data, num, pager, render

router = APIRouter(prefix="/app/tickets", tags=["tickets"])
WRITE = require_role("owner", "staff")


def _clean_pp(v):
    return "".join((v or "").split()).upper()


def _clean_mob(v):
    d = re.sub(r"\D", "", v or "")
    return d[2:] if len(d) == 12 and d.startswith("91") else d


@router.get("")
def ticket_list(request: Request, q: str = "", travel: str = "", supplier: str = "", airline: str = "",
                status: str = "", source: str = "", pay: str = "", pg: dict = Depends(pager), user=Depends(require_user)):
    where, params = ["true"], {}
    if q.strip():
        where.append("""(c.name ILIKE %(q)s OR c.mobile ILIKE %(q)s OR c.passport_no ILIKE %(q)s OR upper(t.pnr) = %(qx)s
                         OR t.ticket_no::text = %(qn)s OR EXISTS (SELECT 1 FROM ticket_passengers tp JOIN customers pc
                         ON pc.id = tp.customer_id WHERE tp.booking_id = t.id AND (pc.name ILIKE %(q)s OR pc.passport_no ILIKE %(q)s)))""")
        params.update(q=f"%{q.strip()}%", qx=q.strip().upper(), qn=q.strip().upper().lstrip("T-"))
    if travel == "today":
        where.append("t.travel_date = current_date")
    elif travel == "7d":
        where.append("t.travel_date BETWEEN current_date AND current_date + 7")
    elif travel == "month":
        where.append("t.created_at >= date_trunc('month', current_date)")
    if supplier.isdigit():
        where.append("t.supplier_id = %(sup)s"); params["sup"] = int(supplier)
    if airline.strip():
        where.append("t.airline ILIKE %(al)s"); params["al"] = f"%{airline.strip()}%"
    if status:
        where.append("t.status = %(st)s"); params["st"] = status
    if source == "file":
        where.append("t.file_id IS NOT NULL")
    elif source == "walkin":
        where.append("t.file_id IS NULL")
    if pay == "due":
        where.append(f"{BOOKING_BALANCE_SQL} > 0")
    result = paginate(f"""
        SELECT t.*, c.name, c.district, p.name AS supplier, f.serial_no, f.visa_type,
               (SELECT (array_agg(l.from_code ORDER BY l.leg_no))[1] || ' → ' || string_agg(l.to_code, ' → ' ORDER BY l.leg_no) FROM ticket_legs l WHERE l.booking_id = t.id) AS route,
               (SELECT count(*) FROM ticket_passengers tp WHERE tp.booking_id = t.id) AS pax,
               {BOOKING_BALANCE_SQL} AS balance,
               (SELECT max(mode) FROM payments x WHERE x.booking_id = t.id AND x.direction = 'in') AS paid_mode
        FROM ticket_bookings t JOIN customers c ON c.id = t.customer_id
        LEFT JOIN parties p ON p.id = t.supplier_id LEFT JOIN files f ON f.id = t.file_id
        WHERE {' AND '.join(where)}
        ORDER BY t.ticket_no DESC
    """, params, **pg)
    stats = fetch_one(f"""
        SELECT count(*) FILTER (WHERE t.created_at >= date_trunc('month', current_date)) AS month_count,
               coalesce(sum(t.selling_price) FILTER (WHERE t.created_at >= date_trunc('month', current_date)), 0) AS sales,
               coalesce(sum(t.selling_price - t.cost_price) FILTER (WHERE t.created_at >= date_trunc('month', current_date)), 0) AS margin,
               count(*) FILTER (WHERE t.travel_date BETWEEN current_date AND current_date + 7 AND t.status = 'booked') AS week,
               coalesce(sum(greatest({BOOKING_BALANCE_SQL}, 0)) FILTER (WHERE t.file_id IS NULL AND t.status <> 'cancelled'), 0) AS due
        FROM ticket_bookings t
    """)
    suppliers = fetch_all("SELECT id, name FROM parties WHERE kind = 'supplier' ORDER BY name LIMIT 30")
    chips = {k: v for k, v in dict(travel=travel, supplier=supplier, airline=airline, status=status, source=source, pay=pay).items() if v}
    return render(request, "tickets/list.html", {
        "result": result, "q": q, "travel": travel, "supplier": supplier, "airline": airline, "status": status,
        "source": source, "pay": pay, "stats": stats, "suppliers": suppliers, "chips": chips,
        "filters_on": len(chips), "active": "tickets",
    }, partial="tickets/_rows.html")


def _form_ctx(mode, values=None, errors=None, booking=None, pax=None, legs=None):
    return {
        "mode": mode, "v": values or {}, "errors": errors or {}, "b": booking or {},
        "pax": pax or [{}], "legs": legs or [{}],
        "suppliers": fetch_all("SELECT id, name FROM parties WHERE kind = 'supplier' AND is_active ORDER BY name LIMIT 30"),
        "banks": fetch_all("SELECT id, name FROM bank_accounts WHERE is_active ORDER BY id LIMIT 10"),
        "next_no": setting("next_ticket_no"), "active": "tickets",
    }


@router.get("/new")
def new_ticket(request: Request, customer_id: int | None = None, file_id: int | None = None,
               enquiry_id: int | None = None, user=Depends(WRITE)):
    pax, values = [{}], {"enquiry_id": enquiry_id or ""}
    if file_id:
        f = fetch_one("SELECT f.id, f.serial_no, f.customer_id, s.data FROM files f LEFT JOIN file_services s"
                      " ON s.file_id = f.id AND s.service = 'ticket' WHERE f.id = %s", (file_id,))
        if f:
            customer_id = f["customer_id"]
            values.update(file_id=f["id"], file_serial=f["serial_no"])
            d = f["data"] or {}
            values.update(airline=d.get("airline", ""), pnr=d.get("pnr", ""), cost_price=d.get("cost", ""),
                          selling_price=d.get("sell", ""))
            legs = [{"from_code": d.get("from", ""), "to_code": d.get("to", ""), "date": d.get("date", "")}]
        else:
            legs = [{}]
    else:
        legs = [{}]
    if customer_id:
        c = fetch_one("SELECT id, name, passport_no, passport_expiry, dob, mobile, address FROM customers WHERE id = %s", (customer_id,))
        if c:
            pax = [{**c, "customer_id": c["id"]}]
    elif enquiry_id:
        e = fetch_one("SELECT name, mobile FROM enquiries WHERE id = %s", (enquiry_id,))
        if e:
            pax = [{"name": e["name"], "mobile": e["mobile"]}]
    return render(request, "tickets/form.html", _form_ctx("new", values, pax=pax, legs=legs))


@router.get("/row")
def extra_row(request: Request, kind: str = "pax", user=Depends(WRITE)):
    """'+ Add passenger' / '+ Add connecting flight' – one empty row (FR-7.2)."""
    tpl = "tickets/_pax_row.html" if kind == "pax" else "tickets/_leg_row.html"
    return render(request, tpl, {"p": {}, "l": {}, "removable": True, "errors": {}})


def _parse(form):
    g = lambda k: (form.get(k) or "").strip()  # noqa: E731
    v = {k: g(k) for k in ("supplier_id", "airline", "pnr", "cost_price", "selling_price", "received", "bank_account_id",
                           "mode_pay", "pay_ref", "file_id", "enquiry_id", "send_details", "travel_reminder", "status")}
    v["pnr"] = v["pnr"].upper().replace(" ", "")
    cols = ["customer_id", "name", "passport", "expiry", "dob", "mobile", "address"]
    lists = {c: form.getlist(f"pax_{c}") for c in cols}
    pax = []
    for i in range(len(lists["name"])):
        p = {c: (lists[c][i] if i < len(lists[c]) else "").strip() for c in cols}
        p["passport"], p["mobile"] = _clean_pp(p["passport"]), _clean_mob(p["mobile"])
        if p["name"] or p["customer_id"]:
            pax.append(p)
    lcols = ["from", "to", "date", "time", "flight"]
    ll = {c: form.getlist(f"leg_{c}") for c in lcols}
    legs = []
    for i in range(len(ll["from"])):
        l = {c: (ll[c][i] if i < len(ll[c]) else "").strip() for c in lcols}
        if l["from"] or l["to"]:
            legs.append({"from_code": l["from"].upper(), "to_code": l["to"].upper(), "date": l["date"],
                         "time": l["time"], "flight_no": l["flight"].upper()})
    e = {}
    if not pax:
        e["pax"] = "Add at least one passenger."
    for i, p in enumerate(pax):
        if not p["customer_id"]:
            if not p["name"]:
                e["pax"] = f"Passenger {i + 1}: enter the name."
            elif not re.fullmatch(r"[6-9]\d{9}", p["mobile"]) and i == 0:
                e["pax"] = f"Passenger {i + 1}: enter a 10-digit mobile."
            elif p["passport"] and not re.fullmatch(r"[A-Z0-9]{6,9}", p["passport"]):
                e["pax"] = f"Passenger {i + 1}: check the passport number."
    if not legs:
        e["legs"] = "Add the flight (from, to, travel date)."
    elif not to_date(legs[0]["date"]):
        e["legs"] = "Enter the travel date."
    if num(v["selling_price"]) <= 0:
        e["selling_price"] = "Enter the selling price."
    if num(v["received"]) > 0 and not v["bank_account_id"].isdigit():
        e["bank_account_id"] = "Choose where the money was received."
    return v, pax, legs, e


def _pax_customer(conn, p, user):
    """Link to an existing customer by id, passport or mobile – else create one (FR-7.1, FR-17.2)."""
    if p["customer_id"].isdigit():
        return int(p["customer_id"])
    row = None
    if p["passport"]:
        row = conn.execute("SELECT id FROM customers WHERE passport_no = %s", (p["passport"],)).fetchone()
    if not row and p["mobile"] and not p["passport"]:
        row = conn.execute("SELECT id FROM customers WHERE mobile = %s AND lower(name) = lower(%s) LIMIT 1",
                           (p["mobile"], p["name"])).fetchone()
    if row:
        return row["id"]
    c = insert("customers", {
        "name": p["name"], "mobile": p["mobile"] or "", "passport_no": p["passport"] or None,
        "passport_expiry": to_date(p["expiry"]), "dob": to_date(p["dob"]), "address": p["address"] or None,
        "created_by": user["id"],
    }, conn=conn)
    audit(user["id"], "create", "customer", c["id"], {"from": "ticket desk"}, conn=conn)
    return c["id"]


def _save_legs(conn, booking_id, legs):
    conn.execute("DELETE FROM ticket_legs WHERE booking_id = %s", (booking_id,))
    for i, l in enumerate(legs, 1):
        d, t = to_date(l["date"]), to_time(l["time"])
        depart = datetime.combine(d, t, tzinfo=IST) if d and t else (datetime.combine(d, datetime.min.time(), tzinfo=IST) if d else None)
        insert("ticket_legs", {"booking_id": booking_id, "leg_no": i, "from_code": l["from_code"] or "?",
                               "to_code": l["to_code"] or "?", "depart_at": depart, "flight_no": l["flight_no"] or None}, conn=conn)


def _route(legs):
    return " → ".join([legs[0]["from_code"]] + [l["to_code"] for l in legs]) if legs else ""


def _link_file(conn, file_id, booking, legs):
    """A ticket for a visa file: the file's planned ticket cost is replaced by this booking's charge
    and the Ticket stage is marked done (FR-7.5)."""
    conn.execute("""DELETE FROM charges WHERE file_id = %s AND kind = 'cost'
                    AND file_service_id IN (SELECT id FROM file_services WHERE file_id = %s AND service = 'ticket')""",
                 (file_id, file_id))
    conn.execute("""UPDATE file_services SET status = 'done', done_on = current_date, updated_at = now(),
                    data = data || jsonb_build_object('pnr', %s::text, 'date', %s::text, 'airline', %s::text)
                    WHERE file_id = %s AND service = 'ticket'""",
                 (booking["pnr"] or "", str(booking["travel_date"] or ""), booking["airline"] or "", file_id))
    total = conn.execute("SELECT coalesce(sum(amount * (1 + gst_rate / 100)), 0) AS t FROM charges WHERE file_id = %s"
                         " AND kind IN ('service_charge', 'cost', 'ticket')", (file_id,)).fetchone()["t"]
    conn.execute("UPDATE files SET package_total = %s WHERE id = %s", (total, file_id))
    refresh_file_stage(conn, file_id)


def _queue_ticket_messages(conn, b, legs, v, first=True):
    msg = None
    route = _route(legs)
    if v.get("send_details") or first:
        dep = legs[0]["time"] if legs else ""
        msg = queue_message(conn, "ticket_booked", b["customer_id"], file_id=b["file_id"], booking_id=b["id"],
                            route=route, date=b["travel_date"].strftime("%d %b %Y") if b["travel_date"] else "",
                            pnr=b["pnr"] or "(will be shared soon)", report_time=f"3 hours before {dep}" if dep else "3 hours before the flight")
    if v.get("travel_reminder") and b["travel_date"]:      # morning of travel (FR-7.4, travel_day is exempt from quiet hours)
        due = datetime.combine(b["travel_date"], datetime.min.time(), tzinfo=IST).replace(hour=6)
        queue_message(conn, "travel_day", b["customer_id"], file_id=b["file_id"], booking_id=b["id"], due_at=due,
                      dedupe_key=f"travel:{b['id']}", route=route, pnr=b["pnr"] or "")
    return msg


@router.post("")
def create_ticket(request: Request, form=Depends(form_data), user=Depends(WRITE)):
    v, pax, legs, errors = _parse(form)
    if errors:
        return render(request, "tickets/form.html", _form_ctx("new", v, errors, pax=pax or [{}], legs=legs or [{}]))
    file_id = int(v["file_id"]) if v["file_id"].isdigit() else None
    with transaction() as conn:
        ids = [_pax_customer(conn, p, user) for p in pax]
        no = next_serial(conn, "next_ticket_no")
        supplier = int(v["supplier_id"]) if v["supplier_id"].isdigit() else None
        b = insert("ticket_bookings", {
            "ticket_no": no, "customer_id": ids[0], "file_id": file_id, "supplier_id": supplier,
            "airline": v["airline"] or None, "pnr": v["pnr"] or None, "travel_date": to_date(legs[0]["date"]),
            "cost_price": num(v["cost_price"]), "selling_price": num(v["selling_price"]), "created_by": user["id"],
        }, conn=conn)
        for cid in dict.fromkeys(ids):
            insert("ticket_passengers", {"booking_id": b["id"], "customer_id": cid}, conn=conn)
        _save_legs(conn, b["id"], legs)
        insert("charges", {"booking_id": b["id"], "file_id": file_id, "kind": "ticket", "label": f"Ticket T-{no}",
                           "amount": num(v["selling_price"]), "created_by": user["id"]}, conn=conn)
        if file_id:
            _link_file(conn, file_id, b, legs)
        audit(user["id"], "create", "ticket", b["id"], {"ticket_no": no, "pnr": b["pnr"]}, conn=conn)
        if file_id:
            audit(user["id"], "ticket_booked", "file", file_id, {"ticket_no": no}, conn=conn)
        msg = _queue_ticket_messages(conn, b, legs, v)
        rec = num(v["received"])
        if rec > 0:
            pay = insert("payments", {
                "direction": "in", "kind": "payment", "booking_id": b["id"], "file_id": file_id, "customer_id": ids[0],
                "bank_account_id": int(v["bank_account_id"]), "amount": rec,
                "mode": v["mode_pay"] if v["mode_pay"] in ("cash", "upi", "bank", "cheque", "dd") else "cash",
                "ref_no": v["pay_ref"] or None, "receipt_no": next_receipt_no(conn), "created_by": user["id"],
            }, conn=conn)
            audit(user["id"], "payment", "ticket", b["id"], {"payment_id": pay["id"], "amount": rec}, conn=conn)
        if v["enquiry_id"].isdigit():
            conn.execute("UPDATE enquiries SET status = 'converted', customer_id = %s, updated_at = now() WHERE id = %s",
                         (ids[0], int(v["enquiry_id"])))
    return RedirectResponse(f"/app/tickets/{b['id']}" + (f"?msg={msg['id']}" if msg else ""), status_code=303)


def _load(booking_id):
    b = fetch_one("""
        SELECT t.*, c.name, c.mobile, p.name AS supplier, f.serial_no FROM ticket_bookings t
        JOIN customers c ON c.id = t.customer_id LEFT JOIN parties p ON p.id = t.supplier_id
        LEFT JOIN files f ON f.id = t.file_id WHERE t.id = %s
    """, (booking_id,))
    if not b:
        raise HTTPException(404)
    pax = fetch_all("""SELECT c.id, c.name, c.passport_no, c.passport_expiry, c.dob, c.mobile FROM ticket_passengers tp
                       JOIN customers c ON c.id = tp.customer_id WHERE tp.booking_id = %s ORDER BY tp.id LIMIT 20""", (booking_id,))
    legs = fetch_all("SELECT * FROM ticket_legs WHERE booking_id = %s ORDER BY leg_no LIMIT 10", (booking_id,))
    return b, pax, legs


@router.get("/{booking_id}")
def ticket_detail(booking_id: int, request: Request, msg: int | None = None, user=Depends(require_user)):
    b, pax, legs = _load(booking_id)
    payments = fetch_all("""SELECT p.*, a.name AS account FROM payments p LEFT JOIN bank_accounts a ON a.id = p.bank_account_id
                            WHERE p.booking_id = %s ORDER BY p.created_at DESC LIMIT 30""", (booking_id,))
    messages = fetch_all("SELECT m.*, u.name AS done_by_name FROM messages m LEFT JOIN users u ON u.id = m.done_by"
                         " WHERE m.booking_id = %s ORDER BY m.created_at DESC LIMIT 10", (booking_id,))
    toast = fetch_one("SELECT * FROM messages WHERE id = %s AND booking_id = %s", (msg, booking_id)) if msg else None
    return render(request, "tickets/detail.html", {"b": b, "pax": pax, "legs": legs, "t": booking_totals(booking_id),
                                                    "payments": payments, "messages": messages, "toast": toast,
                                                    "active": "tickets"})


@router.get("/{booking_id}/edit")
def edit_ticket(booking_id: int, request: Request, user=Depends(WRITE)):
    b, pax, legs = _load(booking_id)
    v = {k: b[k] for k in ("supplier_id", "airline", "pnr", "status", "file_id")}
    v.update(cost_price=int(b["cost_price"]), selling_price=int(b["selling_price"]))
    legs_v = [{"from_code": l["from_code"], "to_code": l["to_code"], "flight_no": l["flight_no"],
               "date": l["depart_at"].astimezone(IST).date() if l["depart_at"] else b["travel_date"],
               "time": l["depart_at"].astimezone(IST).strftime("%H:%M") if l["depart_at"] and l["depart_at"].astimezone(IST).hour else ""}
              for l in legs]
    return render(request, "tickets/form.html", _form_ctx("edit", v, booking=b, pax=pax, legs=legs_v or [{}]))


@router.post("/{booking_id}")
def save_ticket(booking_id: int, request: Request, form=Depends(form_data), user=Depends(WRITE)):
    b, pax, _legs = _load(booking_id)
    g = lambda k: (form.get(k) or "").strip()  # noqa: E731
    _v, _p, legs, errors = _parse(form)
    errors.pop("pax", None)        # passengers are not changed on edit
    if errors:
        v = {k: g(k) for k in ("supplier_id", "airline", "pnr", "status", "cost_price", "selling_price")}
        return render(request, "tickets/form.html", _form_ctx("edit", v, errors, booking=b, pax=pax, legs=legs or [{}]))
    status = g("status") if g("status") in ("booked", "travelled", "cancelled") else b["status"]
    with transaction() as conn:
        nb = conn.execute("""UPDATE ticket_bookings SET supplier_id = %s, airline = %s, pnr = %s, travel_date = %s,
                             cost_price = %s, selling_price = %s, status = %s, updated_at = now() WHERE id = %s RETURNING *""",
                          (int(g("supplier_id")) if g("supplier_id").isdigit() else None, g("airline") or None,
                           g("pnr").upper().replace(" ", "") or None, to_date(legs[0]["date"]), num(g("cost_price")),
                           num(g("selling_price")), status, booking_id)).fetchone()
        _save_legs(conn, booking_id, legs)
        conn.execute("UPDATE charges SET amount = %s, updated_at = now() WHERE booking_id = %s AND kind = 'ticket'",
                     (0 if status == "cancelled" else num(g("selling_price")), booking_id))
        if nb["file_id"]:
            _link_file(conn, nb["file_id"], nb, legs)
        changed = (b["pnr"], b["travel_date"]) != (nb["pnr"], nb["travel_date"])
        msg = _queue_ticket_messages(conn, nb, legs, {"send_details": changed, "travel_reminder": changed}, first=False) if changed else None
        audit(user["id"], "update", "ticket", booking_id, {"status": status, "pnr": nb["pnr"]}, conn=conn)
    return RedirectResponse(f"/app/tickets/{booking_id}" + (f"?msg={msg['id']}" if msg else ""), status_code=303)
