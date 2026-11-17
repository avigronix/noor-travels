"""Visa files: list, registration / edit form, detail with stages, courier (SRS 5.1–5.6, 5.16).

Designs: Candidates + MobileFiles (list), NewFile + EditFile (form), FileDetail + MobileFile (detail).
"""
import csv
import io
import json
import re
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from psycopg.types.json import Jsonb

from common.auth import require_role, require_user
from common.db import audit, fetch_all, fetch_one, insert, paginate, stream, transaction, update
from common.messages import queue_message
from common.money import FILE_BALANCE_SQL, file_totals, next_receipt_no
from common.stages import (ALL_PROCESS, CHECKBOX_SERVICES, DOCUMENTS, ORDER, SERVICES, VISA_TYPES, clean_section,
                           default_costs, default_services, missing_fields, next_serial, next_step,
                           refresh_file_stage, setting, sync_attempt, to_date)
from common.uploads import save_photo
from common.web import form_data, num, pager, render

router = APIRouter(prefix="/app/files", tags=["files"])
WRITE = require_role("owner", "staff")          # accountant is read-only
STATES = ["Uttar Pradesh", "Bihar", "Jharkhand", "West Bengal", "Other"]
COUNTRIES = ["Saudi Arabia", "UAE", "Kuwait", "Qatar", "Oman", "Bahrain"]
MOBILE_RE = re.compile(r"[6-9]\d{9}")


def clean_passport(value):
    return "".join((value or "").split()).upper()


def clean_mobile(value):
    d = re.sub(r"\D", "", value or "")
    return d[2:] if len(d) == 12 and d.startswith("91") else d


# ---------------------------------------------------------------- list (FR-6.3, 6.4, 10.3 links)

def _list_where(q, visa_type, status, stage, passport, visa_done, assigned, pay, country, alert):
    where, params = ["true"], {}
    if q.strip():
        where.append("(c.name ILIKE %(q)s OR c.mobile ILIKE %(q)s OR c.passport_no ILIKE %(q)s OR f.serial_no::text = %(qx)s)")
        params.update(q=f"%{q.strip()}%", qx=q.strip().lstrip("#"))
    if visa_type:
        where.append("f.visa_type = %(vt)s"); params["vt"] = visa_type
    if status:
        where.append("f.status = %(st)s"); params["st"] = status
    if stage:
        where.append("f.current_stage = %(stage)s"); params["stage"] = stage
    if country:
        where.append("f.country = %(country)s"); params["country"] = country
    if assigned.isdigit():
        where.append("f.assigned_to = %(as)s"); params["as"] = int(assigned)
    if passport == "out":         # FR-6.4 passports outside the office
        where.append("f.status = 'active' AND f.passport_location <> 'Office'")
    if visa_done == "month":
        where.append("f.visa_done_on >= date_trunc('month', current_date)")
    if pay == "due":
        where.append(f"{FILE_BALANCE_SQL} > 0")
    elif pay == "paid":
        where.append(f"{FILE_BALANCE_SQL} <= 0")
    if alert == "courier":        # FR-6.3 courier not received in 3 days
        where.append("EXISTS (SELECT 1 FROM courier_movements m WHERE m.file_id = f.id AND m.received_on IS NULL"
                     " AND m.sent_on < current_date - 3)")
    elif alert == "ppt":          # FR-6.3 passport not back 7 days after visa done
        where.append("f.visa_done_on < current_date - 7 AND f.passport_back_on IS NULL AND f.status = 'active'")
    elif alert == "stuck":        # FR-10.6 no update for 7+ days
        where.append("f.status = 'active' AND f.updated_at < now() - interval '7 days'")
    elif alert == "failed":
        where.append("EXISTS (SELECT 1 FROM file_services s WHERE s.file_id = f.id AND s.status = 'failed')")
    return where, params


FILTERS = ("visa_type", "status", "stage", "passport", "visa_done", "assigned", "pay", "country", "alert")


@router.get("")
def file_list(
    request: Request, q: str = "", visa_type: str = "", status: str = "", stage: str = "", passport: str = "",
    visa_done: str = "", assigned: str = "", pay: str = "", country: str = "", alert: str = "",
    pg: dict = Depends(pager), user=Depends(require_user),
):
    args = dict(q=q, visa_type=visa_type, status=status, stage=stage, passport=passport, visa_done=visa_done,
                assigned=assigned, pay=pay, country=country, alert=alert)
    where, params = _list_where(**args)
    result = paginate(f"""
        SELECT f.id, f.serial_no, f.visa_type, f.country, f.status, f.current_stage, f.updated_at,
               f.passport_location, c.id AS customer_id, c.name, c.passport_no, c.ecr, c.profession,
               nx.on_date AS next_date, nx.at_time AS next_time, {FILE_BALANCE_SQL} AS balance
        FROM files f
        JOIN customers c ON c.id = f.customer_id
        LEFT JOIN LATERAL (
            SELECT a.on_date, a.at_time FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id
            WHERE s.file_id = f.id AND a.status = 'scheduled' AND a.on_date >= current_date
            ORDER BY a.on_date, a.at_time NULLS LAST LIMIT 1) nx ON true
        WHERE {' AND '.join(where)}
        ORDER BY f.serial_no DESC
    """, params, **pg)

    # stage tabs with counts – same filters except the stage itself
    w2, p2 = _list_where(**{**args, "stage": ""})
    tabs = fetch_all(f"""
        SELECT f.current_stage AS stage, count(*) AS n FROM files f JOIN customers c ON c.id = f.customer_id
        WHERE {' AND '.join(w2)} GROUP BY 1 ORDER BY 2 DESC LIMIT 20
    """, p2)
    staff = fetch_all("SELECT id, name FROM users WHERE is_active ORDER BY name LIMIT 50")
    chips = {k: v for k, v in args.items() if k != "q" and v}
    return render(request, "files/list.html", {
        **args, "result": result, "tabs": tabs, "staff": staff, "chips": chips,
        "filters_on": len([k for k in chips if k != "stage"]), "countries": COUNTRIES, "active": "files",
        "export_qs": request.url.query,
    }, partial="files/_rows.html")


@router.get("/export.csv")
def file_export(
    request: Request, q: str = "", visa_type: str = "", status: str = "", stage: str = "", passport: str = "",
    visa_done: str = "", assigned: str = "", pay: str = "", country: str = "", alert: str = "",
    user=Depends(require_user),
):
    where, params = _list_where(q, visa_type, status, stage, passport, visa_done, assigned, pay, country, alert)
    rows = stream(f"""
        SELECT f.serial_no, f.created_at::date AS opened, c.name, c.mobile, c.passport_no, f.visa_type, f.country,
               f.status, f.current_stage, f.passport_location, f.visa_done_on, {FILE_BALANCE_SQL} AS balance
        FROM files f JOIN customers c ON c.id = f.customer_id
        WHERE {' AND '.join(where)} ORDER BY f.serial_no DESC
    """, params)
    return _csv(rows, ["serial_no", "opened", "name", "mobile", "passport_no", "visa_type", "country", "status",
                       "current_stage", "passport_location", "visa_done_on", "balance"], "files.csv")


def _csv(rows, cols, filename):
    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for i, r in enumerate(rows):
            w.writerow([r[c] for c in cols])
            if i % 500 == 0:
                yield buf.getvalue(); buf.seek(0); buf.truncate()
        yield buf.getvalue()
    return StreamingResponse(gen(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# ---------------------------------------------------------------- lookup (FR-17.2, FR-1.4)

@router.get("/lookup")
def lookup(request: Request, passport_no: str = "", mobile: str = "", passport_expiry: str = "",
           expected_travel: str = "", customer_id: str = "", pax_lookup: str = "", user=Depends(require_user)):
    """HTMX fragment under the passport field: existing customer found / new customer / expiry warning.
    Also used by the ticket desk passenger rows (?for=ticket)."""
    if pax_lookup:                     # ticket desk: one box, passport or mobile
        digits = clean_mobile(pax_lookup)
        passport_no, mobile = ("", digits) if len(digits) == 10 else (pax_lookup, "")
    pp, mob = clean_passport(passport_no), clean_mobile(mobile)
    expiry, travel = passport_expiry, expected_travel
    match = None
    if len(pp) >= 6 or len(mob) == 10:
        match = fetch_one("""
            SELECT c.id, c.name, c.mobile, c.passport_no,
                   (SELECT count(*) FROM files f WHERE f.customer_id = c.id) AS files,
                   (SELECT count(*) FROM ticket_bookings t WHERE t.customer_id = c.id) AS tickets,
                   (SELECT f.id FROM files f WHERE f.customer_id = c.id AND f.status = 'active' LIMIT 1) AS active_id,
                   (SELECT f.serial_no FROM files f WHERE f.customer_id = c.id AND f.status = 'active' LIMIT 1) AS active_serial
            FROM customers c
            WHERE (%(pp)s <> '' AND c.passport_no = %(pp)s) OR (%(pp)s = '' AND %(mob)s <> '' AND c.mobile = %(mob)s)
            ORDER BY (c.passport_no = %(pp)s) DESC LIMIT 1
        """, {"pp": pp, "mob": mob})
    same = match and customer_id.isdigit() and match["id"] == int(customer_id)
    return render(request, "files/_lookup.html", {
        "match": match, "same": same, "typed": bool(pp or mob),
        "expiry_warn": _expiry_warning(to_date(expiry), to_date(travel)),
        "for_ticket": request.query_params.get("for") == "ticket",
    })


def _expiry_warning(expiry, travel):
    if not expiry:
        return None
    base = travel or date.today()
    if expiry < base + timedelta(days=182):
        return f"Passport expires on {expiry:%d %b %Y} – less than 6 months before {'travel' if travel else 'today + 6 months'}."
    return None


# ---------------------------------------------------------------- form (new + edit, FR-2.x)

def _form_context(request, mode, f=None, c=None, services=None, data=None, locked=(), errors=None,
                  values=None, warnings=None):
    visa_type = (values or {}).get("visa_type") or (f or {}).get("visa_type") or "work"
    defaults = {vt: default_services(vt) for vt, _l, _h in VISA_TYPES}
    costs = default_costs("work")
    return {
        "mode": mode, "f": f or {}, "c": c or {}, "v": values or {}, "errors": errors or {},
        "warnings": warnings or [], "visa_type": visa_type,
        "on": set(services if services is not None else defaults.get(visa_type, [])),
        "data": data or {k: ({"cost": str(int(costs[k]))} if k in costs else {}) for k in ORDER},
        "locked": set(locked), "SERVICES": SERVICES, "ORDER": ORDER, "CHECKBOX_SERVICES": CHECKBOX_SERVICES,
        "ALL_PROCESS": ALL_PROCESS, "VISA_TYPES": VISA_TYPES, "defaults_json": json.dumps(defaults),
        "referrers": fetch_all("SELECT id, name, mobile, place FROM referrers ORDER BY name LIMIT 300"),
        "banks": fetch_all("SELECT id, name FROM bank_accounts WHERE is_active ORDER BY id LIMIT 10"),
        "staff": fetch_all("SELECT id, name FROM users WHERE is_active AND role <> 'accountant' ORDER BY name LIMIT 50"),
        "states": STATES, "countries": COUNTRIES,
        "next_serial": setting("next_file_serial"), "gst_rate": float(setting("gst_rate", "18") or 0),
        "cap": float(setting("service_charge_cap", "30000") or 0),
        "active": "files",
    }


@router.get("/new")
def new_file(request: Request, customer_id: int | None = None, enquiry_id: int | None = None, user=Depends(WRITE)):
    c, values = {}, {}
    if customer_id:
        c = fetch_one("SELECT * FROM customers WHERE id = %s", (customer_id,)) or {}
    elif enquiry_id:      # FR-16.2 convert enquiry → registration
        e = fetch_one("SELECT name, mobile, service FROM enquiries WHERE id = %s", (enquiry_id,))
        if e:
            c = {"name": e["name"], "mobile": e["mobile"]}
            s = (e["service"] or "").lower()
            values["visa_type"] = "umrah" if "umrah" in s else "family" if "family" in s else "tourist" if "tourist" in s else "work"
    values["enquiry_id"] = enquiry_id or ""
    return render(request, "files/form.html", _form_context(request, "new", c=c, values=values,
                                                            services=default_services(values.get("visa_type", "work"))))


def _parse(form, mode):
    """Read the whole registration form. Returns (customer, file, services{key: data}, money, errors, values)."""
    g = lambda k: (form.get(k) or "").strip()  # noqa: E731
    v = {k: g(k) for k in ("name", "father_name", "dob", "mobile", "family_mobile", "address", "district", "state",
                           "pin", "passport_no", "passport_issue", "passport_expiry", "passport_place", "ecr",
                           "profession", "referrer_id", "ref_name", "ref_mobile", "ref_place", "visa_type", "country",
                           "expected_travel", "assigned_to", "service_charge", "advance", "bank_account_id", "mode_pay",
                           "pay_ref", "customer_id", "enquiry_id", "policy", "consent")}
    v["passport_no"], v["mobile"] = clean_passport(v["passport_no"]), clean_mobile(v["mobile"])
    e = {}
    if not v["name"]: e["name"] = "Enter the full name as on the passport."
    if not v["father_name"]: e["father_name"] = "Enter the father's name."
    if not to_date(v["dob"]): e["dob"] = "Enter the date of birth."
    if not MOBILE_RE.fullmatch(v["mobile"]): e["mobile"] = "Enter a 10-digit mobile number."
    if v["family_mobile"] and not MOBILE_RE.fullmatch(clean_mobile(v["family_mobile"])): e["family_mobile"] = "Check this mobile number."
    if not re.fullmatch(r"[A-Z0-9]{6,9}", v["passport_no"]): e["passport_no"] = "Enter a valid passport number (e.g. T5812231)."
    if not to_date(v["passport_expiry"]): e["passport_expiry"] = "Enter the passport expiry date."
    if v["pin"] and not re.fullmatch(r"\d{6}", v["pin"]): e["pin"] = "PIN is 6 digits."
    if v["visa_type"] not in dict((t, 1) for t, _l, _h in VISA_TYPES): e["visa_type"] = "Choose a visa type."
    if v["referrer_id"] == "new" and not v["ref_name"]: e["ref_name"] = "Enter the reference person's name."
    if mode == "new" and not v["policy"]: e["policy"] = "Read out the refund policy and tick to confirm."
    adv = num(v["advance"])
    if adv < 0: e["advance"] = "Advance cannot be negative."
    if adv > 0 and not v["bank_account_id"].isdigit(): e["bank_account_id"] = "Choose where the advance was received."

    # services: ticked checkboxes + All Process extras + the section tied to the visa type (FR-2.2)
    ticked = [s for s in form.getlist("svc") if s in SERVICES]
    if form.get("all_process"):
        ticked = list(dict.fromkeys(ticked + ALL_PROCESS))
    if v["ecr"] != "ECR" and "emigration" in ticked:
        ticked.remove("emigration")                    # emigration is ECR only
    if v["visa_type"] in ("umrah", "family"):
        ticked.append(v["visa_type"])
    ticked += [s for s in form.getlist("locked_svc") if s in SERVICES]
    services = {s: clean_section(s, form) for s in ORDER if s in set(ticked)}
    return v, services, e


@router.post("")
def create_file(request: Request, form=Depends(form_data), user=Depends(WRITE)):
    v, services, errors = _parse(form, "new")
    if not errors:
        pp_owner = fetch_one("SELECT id, name FROM customers WHERE passport_no = %s", (v["passport_no"],))
        cid = int(v["customer_id"]) if v["customer_id"].isdigit() else None
        if pp_owner and pp_owner["id"] != cid:
            errors["passport_no"] = f"This passport already belongs to {pp_owner['name']} – use “Use this customer” above."
        target = cid or (pp_owner and pp_owner["id"])
        if target and fetch_one("SELECT 1 FROM files WHERE customer_id = %s AND status = 'active'", (target,)):
            errors["passport_no"] = "This customer already has an active file – open it instead (FR-1.4)."
    photo, photo_err = save_photo(form.get("photo"), "profile") if not errors else (None, None)
    if photo_err:
        errors["photo"] = photo_err
    if errors:
        return render(request, "files/form.html", _form_context(
            request, "new", c=v, services=list(services), data=services, errors=errors, values=v))

    with transaction() as conn:
        cust = _save_customer(conn, v, int(v["customer_id"]) if v["customer_id"].isdigit() else None, user)
        serial = next_serial(conn, "next_file_serial")
        f = insert("files", {
            "serial_no": serial, "customer_id": cust["id"], "visa_type": v["visa_type"],
            "country": v["country"] or "Saudi Arabia", "service_charge": num(v["service_charge"]),
            "expected_travel": to_date(v["expected_travel"]),
            "assigned_to": int(v["assigned_to"]) if v["assigned_to"].isdigit() else user["id"],
            "created_by": user["id"],
        }, conn=conn)
        _sync_services(conn, f, cust, services, set(), user)
        _sync_charges(conn, f, services, user)
        if photo:
            conn.execute("INSERT INTO documents (customer_id, kind, path, uploaded_by) VALUES (%s, 'profile_photo', %s, %s)"
                         " ON CONFLICT (customer_id, kind) DO UPDATE SET path = EXCLUDED.path, created_at = now()",
                         (cust["id"], photo, user["id"]))
        refresh_file_stage(conn, f["id"])
        audit(user["id"], "create", "file", f["id"], {"serial": serial, "services": list(services)}, conn=conn)
        labels = ", ".join(SERVICES[s]["label"] for s in services) or v["visa_type"].title()
        msg = queue_message(conn, "file_created", cust["id"], file_id=f["id"], file_no=serial, services=labels)
        adv = num(v["advance"])
        if adv > 0:
            pay = _record_advance(conn, f, cust, adv, v, user)
            totals = file_totals(f["id"], conn=conn)
            msg = queue_message(conn, "payment_received", cust["id"], file_id=f["id"], amount=adv,
                                date=date.today().strftime("%d %b %Y"), balance=totals["balance"],
                                receipt_link=_receipt_link(pay)) or msg
        if v["enquiry_id"].isdigit():
            conn.execute("UPDATE enquiries SET status = 'converted', customer_id = %s, updated_at = now() WHERE id = %s",
                         (cust["id"], int(v["enquiry_id"])))
    return RedirectResponse(f"/app/files/{f['id']}" + (f"?msg={msg['id']}" if msg else ""), status_code=303)


def _receipt_link(pay):
    from routes.payments import public_receipt_url
    return public_receipt_url(pay["id"])


def _record_advance(conn, f, cust, amount, v, user):
    pay = insert("payments", {
        "direction": "in", "kind": "payment", "file_id": f["id"], "customer_id": cust["id"],
        "bank_account_id": int(v["bank_account_id"]), "amount": amount,
        "mode": v["mode_pay"] if v["mode_pay"] in ("cash", "upi", "bank", "cheque", "dd") else "bank",
        "ref_no": v["pay_ref"] or None, "receipt_no": next_receipt_no(conn), "note": "Advance at registration",
        "for_service_charge": True, "created_by": user["id"],
    }, conn=conn)
    audit(user["id"], "payment", "file", f["id"], {"payment_id": pay["id"], "amount": amount}, conn=conn)
    return pay


def _save_customer(conn, v, customer_id, user):
    ref_id = int(v["referrer_id"]) if v["referrer_id"].isdigit() else None
    if v["referrer_id"] == "new":
        ref_id = insert("referrers", {"name": v["ref_name"], "mobile": clean_mobile(v["ref_mobile"]) or None,
                                      "place": v["ref_place"] or None}, conn=conn)["id"]
    data = {
        "name": v["name"], "father_name": v["father_name"] or None, "dob": to_date(v["dob"]), "mobile": v["mobile"],
        "family_mobile": clean_mobile(v["family_mobile"]) or None, "address": v["address"] or None,
        "district": v["district"] or None, "state": v["state"] or None, "pin": v["pin"] or None,
        "passport_no": v["passport_no"], "passport_issue": to_date(v["passport_issue"]),
        "passport_expiry": to_date(v["passport_expiry"]), "passport_place": v["passport_place"] or None,
        "ecr": v["ecr"] if v["ecr"] in ("ECR", "ECNR") else None, "profession": v["profession"] or None,
        "referrer_id": ref_id, "whatsapp_consent": bool(v["consent"]),
    }
    if customer_id:
        cust = update("customers", customer_id, data, conn=conn)
        audit(user["id"], "update", "customer", customer_id, {"from": "registration form"}, conn=conn)
    else:
        cust = insert("customers", {**data, "created_by": user["id"]}, conn=conn)
        audit(user["id"], "create", "customer", cust["id"], {}, conn=conn)
    return cust


def _sync_services(conn, f, cust, services, locked, user):
    """Insert / update / remove file_services to match the ticked services (FR-2.5, 2.6, 2.9)."""
    existing = {r["service"]: r for r in conn.execute(
        "SELECT * FROM file_services WHERE file_id = %s LIMIT 20", (f["id"],)).fetchall()}
    for svc, data in services.items():
        fs = existing.get(svc)
        if fs and svc in locked:
            continue                                  # done stage – data only changes through the detail page
        cost = num(data.get("cost"), None)
        if fs:
            fs = conn.execute("UPDATE file_services SET data = %s, cost = %s, updated_at = now(),"
                              " status = CASE WHEN status = 'cancelled' THEN 'pending' ELSE status END,"
                              " details_pending = details_pending AND %s WHERE id = %s RETURNING *",
                              (Jsonb(data), cost, bool(missing_fields(svc, data)), fs["id"])).fetchone()
        else:
            fs = insert("file_services", {"file_id": f["id"], "service": svc, "sort_order": ORDER.index(svc) + 1,
                                          "data": Jsonb(data), "cost": cost}, conn=conn)
        kind = sync_attempt(conn, fs, data, user["id"])
        if kind:
            _queue_appointment(conn, kind, f, cust, svc, data)
    for svc, fs in existing.items():
        if svc in services or svc in locked:
            continue
        has_attempts = conn.execute("SELECT 1 FROM stage_attempts WHERE file_service_id = %s LIMIT 1", (fs["id"],)).fetchone()
        if has_attempts:      # attempts are history – never deleted (FR-4.7)
            conn.execute("UPDATE file_services SET status = 'cancelled', cancel_reason = 'Removed in edit profile',"
                         " updated_at = now() WHERE id = %s", (fs["id"],))
            conn.execute("UPDATE stage_attempts SET status = 'cancelled' WHERE file_service_id = %s AND status = 'scheduled'", (fs["id"],))
        else:
            conn.execute("DELETE FROM charges WHERE file_service_id = %s", (fs["id"],))
            conn.execute("DELETE FROM file_services WHERE id = %s", (fs["id"],))
        audit(user["id"], "remove_service", "file", f["id"], {"service": svc}, conn=conn)


def _queue_appointment(conn, kind, f, cust, svc, data):
    return queue_message(
        conn, "appointment_booked" if kind == "booked" else "appointment_changed", cust["id"], file_id=f["id"],
        stage=SERVICES[svc]["label"], date=(to_date(data.get("date")) or date.today()).strftime("%d %b %Y"),
        time=data.get("time") or "", place=", ".join(x for x in (data.get("centre"), data.get("city")) if x),
        documents=DOCUMENTS.get(svc, "original passport"),
    )


def _sync_charges(conn, f, services, user):
    """Service charge (+GST) and one cost line per ticked service with a cost (FR-14.1)."""
    gst = float(conn.execute("SELECT value FROM settings WHERE key = 'gst_rate'").fetchone()["value"] or 0)
    sc = float(f["service_charge"] or 0)
    row = conn.execute("SELECT id FROM charges WHERE file_id = %s AND kind = 'service_charge' LIMIT 1", (f["id"],)).fetchone()
    if row and sc:
        conn.execute("UPDATE charges SET amount = %s, gst_rate = %s, updated_at = now() WHERE id = %s", (sc, gst, row["id"]))
    elif row:
        conn.execute("DELETE FROM charges WHERE id = %s", (row["id"],))
    elif sc:
        insert("charges", {"file_id": f["id"], "label": "Service charge", "amount": sc, "gst_rate": gst,
                           "kind": "service_charge", "created_by": user["id"]}, conn=conn)
    for fs in conn.execute("SELECT id, service, cost, status FROM file_services WHERE file_id = %s LIMIT 20", (f["id"],)).fetchall():
        # a ticket booked on the ticket desk carries its own charge – the planned ticket cost then goes
        has_booking = fs["service"] == "ticket" and conn.execute(
            "SELECT 1 FROM ticket_bookings WHERE file_id = %s AND status <> 'cancelled' LIMIT 1", (f["id"],)).fetchone()
        cost = 0 if (fs["status"] == "cancelled" or has_booking) else float(fs["cost"] or 0)
        if fs["service"] == "ticket" and services.get("ticket", {}).get("sell"):
            cost = 0 if has_booking or fs["status"] == "cancelled" else num(services["ticket"]["sell"])
        row = conn.execute("SELECT id FROM charges WHERE file_service_id = %s AND kind = 'cost' LIMIT 1", (fs["id"],)).fetchone()
        if row and cost:
            conn.execute("UPDATE charges SET amount = %s, updated_at = now() WHERE id = %s", (cost, row["id"]))
        elif row:
            conn.execute("DELETE FROM charges WHERE id = %s", (row["id"],))
        elif cost:
            insert("charges", {"file_id": f["id"], "file_service_id": fs["id"], "label": SERVICES[fs["service"]]["label"],
                               "amount": cost, "kind": "cost", "created_by": user["id"]}, conn=conn)
    total = conn.execute("SELECT coalesce(sum(amount * (1 + gst_rate / 100)), 0) AS t FROM charges"
                         " WHERE file_id = %s AND kind IN ('service_charge', 'cost')", (f["id"],)).fetchone()["t"]
    conn.execute("UPDATE files SET package_total = %s WHERE id = %s", (total, f["id"]))


@router.get("/{file_id}/edit")
def edit_file(file_id: int, request: Request, user=Depends(WRITE)):
    f, c, services, data, locked = _load_for_edit(file_id)
    return render(request, "files/form.html", _form_context(
        request, "edit", f=f, c=c, services=services, data=data, locked=locked,
        values={"visa_type": f["visa_type"], "referrer_id": str(c["referrer_id"] or "")}))


def _load_for_edit(file_id):
    f = fetch_one("SELECT * FROM files WHERE id = %s", (file_id,))
    if not f:
        raise HTTPException(404)
    c = fetch_one("SELECT * FROM customers WHERE id = %s", (f["customer_id"],))
    rows = fetch_all("SELECT * FROM file_services WHERE file_id = %s ORDER BY sort_order LIMIT 20", (file_id,))
    services = [r["service"] for r in rows if r["status"] != "cancelled"]
    data = {r["service"]: {k: str(val) for k, val in (r["data"] or {}).items()} for r in rows}
    locked = [r["service"] for r in rows if r["status"] == "done"]       # FR-2.6 / 2.9
    return f, c, services, data, locked


@router.post("/{file_id}")
def save_file(file_id: int, request: Request, form=Depends(form_data), user=Depends(WRITE)):
    f, c, _services, _data, locked = _load_for_edit(file_id)
    v, services, errors = _parse(form, "edit")
    for s in locked:                       # a done stage cannot be unticked (FR-2.6)
        services.setdefault(s, _data.get(s, {}))
    other = fetch_one("SELECT id, name FROM customers WHERE passport_no = %s AND id <> %s", (v["passport_no"], c["id"]))
    if other:
        errors["passport_no"] = f"This passport already belongs to {other['name']}."
    photo, photo_err = save_photo(form.get("photo"), "profile") if not errors else (None, None)
    if photo_err:
        errors["photo"] = photo_err
    if errors:
        return render(request, "files/form.html", _form_context(
            request, "edit", f=f, c={**c, **v}, services=list(services), data={**_data, **services},
            locked=locked, errors=errors, values=v))

    with transaction() as conn:
        v["consent"] = v["consent"] or ""
        cust = _save_customer(conn, v, c["id"], user)
        f = update("files", file_id, {
            "visa_type": v["visa_type"], "country": v["country"] or f["country"],
            "service_charge": num(v["service_charge"]), "expected_travel": to_date(v["expected_travel"]),
            "assigned_to": int(v["assigned_to"]) if v["assigned_to"].isdigit() else f["assigned_to"],
        }, conn=conn)
        _sync_services(conn, f, cust, services, set(locked), user)
        _sync_charges(conn, f, services, user)
        if photo:
            conn.execute("INSERT INTO documents (customer_id, kind, path, uploaded_by) VALUES (%s, 'profile_photo', %s, %s)"
                         " ON CONFLICT (customer_id, kind) DO UPDATE SET path = EXCLUDED.path, created_at = now()",
                         (cust["id"], photo, user["id"]))
        refresh_file_stage(conn, file_id)
        audit(user["id"], "update", "file", file_id, {"services": list(services)}, conn=conn)
    return RedirectResponse(f"/app/files/{file_id}?saved=1", status_code=303)


# ---------------------------------------------------------------- detail (FR-4.x, 6.x)

def _detail_context(file_id, user, msg_id=None, toast=None):
    f = fetch_one("""
        SELECT f.*, u.name AS staff_name, v.visa_no, v.visa_date_hijri, v.visa_date, v.profession AS visa_profession,
               v.photo_path AS visa_photo, s.name AS sponsor_name, s.sponsor_no
        FROM files f LEFT JOIN users u ON u.id = f.assigned_to
        LEFT JOIN visas v ON v.id = f.visa_id LEFT JOIN sponsors s ON s.id = v.sponsor_id
        WHERE f.id = %s
    """, (file_id,))
    if not f:
        raise HTTPException(404)
    c = fetch_one("""
        SELECT c.*, r.name AS ref_name, r.mobile AS ref_mobile, r.place AS ref_place,
               (SELECT path FROM documents d WHERE d.customer_id = c.id AND d.kind = 'profile_photo') AS photo,
               (SELECT count(*) FROM files x WHERE x.customer_id = c.id) AS n_files,
               (SELECT count(*) FROM ticket_bookings t WHERE t.customer_id = c.id) AS n_tickets
        FROM customers c LEFT JOIN referrers r ON r.id = c.referrer_id WHERE c.id = %s
    """, (f["customer_id"],))
    services = fetch_all("SELECT * FROM file_services WHERE file_id = %s ORDER BY sort_order LIMIT 20", (file_id,))
    attempts = fetch_all("""
        SELECT a.*, p.name AS party FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id
        LEFT JOIN parties p ON p.id = a.party_id
        WHERE s.file_id = %s ORDER BY a.attempt_no LIMIT 200
    """, (file_id,))
    by_service = {}
    for a in attempts:
        by_service.setdefault(a["file_service_id"], []).append(a)
    done_keys = {s["service"] for s in services if s["status"] == "done"}
    for s in services:
        s["attempts"] = by_service.get(s["id"], [])
        s["missing"] = missing_fields(s["service"], s["data"])
        s["waiting_on"] = [SERVICES[n]["label"] for n in SERVICES[s["service"]].get("needs", [])
                           if n in {x["service"] for x in services} and n not in done_keys]
        s["next"] = next((a for a in reversed(s["attempts"]) if a["status"] == "scheduled"), None)
    courier = fetch_all("""
        SELECT m.*, p.name AS courier FROM courier_movements m LEFT JOIN parties p ON p.id = m.courier_id
        WHERE m.file_id = %s ORDER BY m.sent_on DESC, m.id DESC LIMIT 30
    """, (file_id,))
    messages = fetch_all("""
        SELECT m.*, u.name AS done_by_name FROM messages m LEFT JOIN users u ON u.id = m.done_by
        WHERE m.file_id = %s ORDER BY m.created_at DESC LIMIT 8
    """, (file_id,))
    log = fetch_all("""
        SELECT a.*, u.name AS user_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
        WHERE a.entity = 'file' AND a.entity_id = %s ORDER BY a.created_at DESC LIMIT 12
    """, (file_id,))
    tickets = fetch_all("SELECT id, ticket_no, pnr, airline, travel_date, status FROM ticket_bookings"
                        " WHERE file_id = %s ORDER BY created_at DESC LIMIT 10", (file_id,))
    totals = file_totals(file_id)
    totals["retest"] = fetch_one("SELECT coalesce(sum(amount), 0) AS n FROM charges WHERE file_id = %s AND kind = 'retest'",
                                 (file_id,))["n"]
    toast_msg = fetch_one("SELECT m.*, c.mobile FROM messages m JOIN customers c ON c.id = m.customer_id"
                          " WHERE m.id = %s AND m.file_id = %s", (msg_id, file_id)) if msg_id else None
    return {
        "f": f, "c": c, "services": services, "courier": courier, "messages": messages, "log": log,
        "tickets": tickets, "t": totals, "toast_msg": toast_msg, "toast": toast, "SERVICES": SERVICES,
        "couriers": fetch_all("SELECT id, name FROM parties WHERE kind = 'courier' AND is_active ORDER BY name LIMIT 30"),
        "done_count": sum(1 for s in services if s["status"] == "done") + 1 + (1 if f["visa_id"] else 0)
                      + (1 if f["status"] == "departed" else 0),
        "total_count": len([s for s in services if s["status"] != "cancelled"]) + 3,
        "active": "files",
    }


@router.get("/{file_id}")
def file_detail(file_id: int, request: Request, msg: int | None = None, saved: str = "", user=Depends(require_user)):
    ctx = _detail_context(file_id, user, msg_id=msg, toast="Saved" if saved else None)
    return render(request, "files/detail.html", ctx)


def _stages_response(request, file_id, user, msg=None, toast=None):
    """After an in-page change: return the stages panel (+ money, passport and messages via OOB) and close the modal."""
    ctx = _detail_context(file_id, user, msg_id=msg["id"] if msg else None, toast=toast)
    return render(request, "files/detail.html", {**ctx, "oob": True}, partial="files/_after_change.html")


def _load_service(file_id, sid):
    fs = fetch_one("SELECT s.*, f.customer_id, f.serial_no, f.status AS file_status FROM file_services s"
                   " JOIN files f ON f.id = s.file_id WHERE s.id = %s AND s.file_id = %s", (sid, file_id))
    if not fs:
        raise HTTPException(404)
    return fs


@router.get("/{file_id}/services/{sid}/modal")
def stage_modal(file_id: int, sid: int, request: Request, mode: str = "edit", user=Depends(WRITE)):
    fs = _load_service(file_id, sid)
    return render(request, "files/_stage_modal.html", _modal_ctx(file_id, fs, mode))


def _modal_ctx(file_id, fs, mode, errors=None):
    services = {r["service"]: r["status"] for r in fetch_all(
        "SELECT service, status FROM file_services WHERE file_id = %s LIMIT 20", (file_id,))}
    waiting = [SERVICES[n]["label"] for n in SERVICES[fs["service"]].get("needs", [])
               if n in services and services[n] != "done"]
    return {"fs": fs, "file_id": file_id, "mode": mode, "spec": SERVICES[fs["service"]], "waiting": waiting,
            "missing": missing_fields(fs["service"], fs["data"]), "errors": errors or {}}


@router.post("/{file_id}/services/{sid}/done")
def mark_done(file_id: int, sid: int, request: Request, form=Depends(form_data), user=Depends(WRITE)):
    """One-tap Mark done in any order (FR-4.3). Missing details or an earlier stage not done → the pop-up
    opens instead (FR-4.4, 4.5 – warn, never block). 'Mark done, fill later' sets details_pending."""
    fs = _load_service(file_id, sid)
    from_modal = form.get("from_modal") == "1"
    data = {**fs["data"], **({k: val for k, val in clean_section(fs["service"], form).items()} if from_modal else {})}
    ctx = _modal_ctx(file_id, {**fs, "data": data}, "mark")
    if not from_modal and (ctx["missing"] or ctx["waiting"]):
        resp = render(request, "files/_stage_modal.html", ctx)
        resp.headers["HX-Retarget"] = "#modal"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp

    spec = SERVICES[fs["service"]]
    later = form.get("later") == "1"
    failed = spec.get("fail") and data.get("result") == spec["fail"]
    with transaction() as conn:
        cust = {"id": fs["customer_id"]}
        if spec.get("appointment"):
            sync_attempt(conn, fs, data, user["id"])     # date changed in the pop-up → re-appointment row first
        cur = conn.execute("SELECT * FROM stage_attempts WHERE file_service_id = %s ORDER BY attempt_no DESC LIMIT 1",
                           (sid,)).fetchone()
        outcome = "failed" if failed else "done"
        if spec.get("appointment"):
            if cur and cur["status"] == "scheduled":
                conn.execute("UPDATE stage_attempts SET status = %s, result = %s WHERE id = %s AND status = 'scheduled'",
                             (outcome, data.get("result") or None, cur["id"]))
            else:
                insert("stage_attempts", {"file_service_id": sid, "attempt_no": (cur["attempt_no"] + 1) if cur else 1,
                                          "status": outcome, "on_date": to_date(data.get("date")) or date.today(),
                                          "city": data.get("city") or None, "result": data.get("result") or None,
                                          "created_by": user["id"]}, conn=conn)
        msg = None
        if failed:
            # FR-4.7 / FR-14.3: retest charge = the centre's fee for that attempt
            fee = num(data.get("cost")) or num(fs["cost"]) or {"medical": 9500, "skill_test": 10000}.get(fs["service"], 0)
            n = conn.execute("SELECT count(*) AS n FROM stage_attempts WHERE file_service_id = %s AND status = 'failed'",
                             (sid,)).fetchone()["n"]
            if fee:
                insert("charges", {"file_id": file_id, "file_service_id": sid, "kind": "retest",
                                   "label": f"{spec['label']} retest #{n + 1}", "amount": fee, "created_by": user["id"]}, conn=conn)
            data["result"] = "Pending"
            data["date"], data["time"] = "", ""
            conn.execute("UPDATE file_services SET status = 'failed', data = %s, updated_at = now() WHERE id = %s",
                         (Jsonb(data), sid))
            msg = queue_message(conn, "stage_failed", cust["id"], file_id=file_id, stage=spec["label"],
                                result=spec["fail"].lower(), amount=fee)
            audit(user["id"], "stage_failed", "file", file_id, {"service": fs["service"], "retest_charge": fee}, conn=conn)
        else:
            done_on = to_date(data.get("done") if fs["service"] == "stamping" else data.get("date")) or date.today()
            conn.execute("UPDATE file_services SET status = 'done', data = %s, done_on = %s, details_pending = %s,"
                         " updated_at = now() WHERE id = %s",
                         (Jsonb(data), done_on, later or bool(missing_fields(fs["service"], data)), sid))
            if fs["service"] == "stamping":              # FR-4.8 'Visa Done' stamp
                conn.execute("UPDATE files SET visa_done_on = %s WHERE id = %s", (done_on, file_id))
                msg = queue_message(conn, "visa_done", cust["id"], file_id=file_id, date=done_on.strftime("%d %b %Y"),
                                    next_step=next_step(conn, file_id))
            else:
                msg = queue_message(conn, "stage_done", cust["id"], file_id=file_id, stage=spec["label"],
                                    next_step=next_step(conn, file_id))
            audit(user["id"], "stage_done", "file", file_id,
                  {"service": fs["service"], "details_pending": later, "warned": ctx["waiting"]}, conn=conn)
        refresh_file_stage(conn, file_id)
    return _stages_response(request, file_id, user, msg=msg,
                            toast=f"{spec['label']} – {'failed, retest charge added' if failed else 'marked done'}")


@router.post("/{file_id}/services/{sid}/details")
def save_details(file_id: int, sid: int, request: Request, form=Depends(form_data), user=Depends(WRITE)):
    """Fill / change a stage's details. A done stage can be changed by the owner only (FR-4.6).
    Changing an appointment date saves a re-appointment as a new attempt (FR-4.7)."""
    fs = _load_service(file_id, sid)
    if fs["status"] == "done" and user["role"] != "owner":
        resp = render(request, "files/_stage_modal.html", _modal_ctx(file_id, fs, "edit",
                      errors={"_": "Only the owner can change a stage that is already done."}))
        resp.headers["HX-Retarget"] = "#modal"
        return resp
    data = {**fs["data"], **clean_section(fs["service"], form)}
    with transaction() as conn:
        conn.execute("UPDATE file_services SET data = %s, cost = %s, details_pending = details_pending AND %s,"
                     " updated_at = now() WHERE id = %s",
                     (Jsonb(data), num(data.get("cost"), None), bool(missing_fields(fs["service"], data)), sid))
        kind = sync_attempt(conn, {**fs, "status": fs["status"]}, data, user["id"]) if fs["status"] != "done" else None
        msg = None
        if kind:
            msg = _queue_appointment(conn, kind, {"id": file_id}, {"id": fs["customer_id"]}, fs["service"], data)
        f = conn.execute("SELECT * FROM files WHERE id = %s", (file_id,)).fetchone()
        services = {s["service"]: s["data"] for s in conn.execute(
            "SELECT service, data FROM file_services WHERE file_id = %s LIMIT 20", (file_id,)).fetchall()}
        _sync_charges(conn, f, services, user)
        if fs["service"] == "stamping" and fs["status"] == "done" and to_date(data.get("done")):
            conn.execute("UPDATE files SET visa_done_on = %s WHERE id = %s", (to_date(data.get("done")), file_id))
        refresh_file_stage(conn, file_id)
        audit(user["id"], "stage_details", "file", file_id, {"service": fs["service"], "rebooked": kind}, conn=conn)
    return _stages_response(request, file_id, user, msg=msg,
                            toast="Re-appointment saved as a new attempt" if kind == "changed" else "Details saved")


@router.post("/{file_id}/services/{sid}/reopen")
def reopen_stage(file_id: int, sid: int, request: Request, user=Depends(require_role("owner"))):
    fs = _load_service(file_id, sid)
    with transaction() as conn:
        conn.execute("UPDATE file_services SET status = 'pending', done_on = NULL, updated_at = now() WHERE id = %s", (sid,))
        if fs["service"] == "stamping":
            conn.execute("UPDATE files SET visa_done_on = NULL WHERE id = %s", (file_id,))
        refresh_file_stage(conn, file_id)
        audit(user["id"], "stage_reopen", "file", file_id, {"service": fs["service"]}, conn=conn)
    return _stages_response(request, file_id, user, toast=f"{SERVICES[fs['service']]['label']} reopened")


@router.post("/{file_id}/services/{sid}/cancel")
def cancel_service(file_id: int, sid: int, request: Request, reason: str = Form(..., min_length=3, max_length=300),
                   user=Depends(WRITE)):
    """A service with a done stage or payment is not unticked – it is cancelled with a reason (FR-2.6)."""
    fs = _load_service(file_id, sid)
    with transaction() as conn:
        conn.execute("UPDATE file_services SET status = 'cancelled', cancel_reason = %s, updated_at = now() WHERE id = %s",
                     (reason.strip(), sid))
        conn.execute("UPDATE stage_attempts SET status = 'cancelled' WHERE file_service_id = %s AND status = 'scheduled'", (sid,))
        f = conn.execute("SELECT * FROM files WHERE id = %s", (file_id,)).fetchone()
        _sync_charges(conn, f, {}, user)
        refresh_file_stage(conn, file_id)
        audit(user["id"], "cancel_service", "file", file_id, {"service": fs["service"], "reason": reason.strip()}, conn=conn)
    return _stages_response(request, file_id, user, toast=f"{SERVICES[fs['service']]['label']} cancelled")


# ---------------------------------------------------------------- courier (FR-6.1, 6.2)

@router.get("/{file_id}/courier/new")
def courier_modal(file_id: int, request: Request, user=Depends(WRITE)):
    f = fetch_one("SELECT id, passport_location FROM files WHERE id = %s", (file_id,))
    if not f:
        raise HTTPException(404)
    couriers = fetch_all("SELECT id, name FROM parties WHERE kind = 'courier' AND is_active ORDER BY name LIMIT 30")
    return render(request, "files/_courier_modal.html", {"f": f, "couriers": couriers, "today": date.today()})


@router.post("/{file_id}/courier")
def add_courier(
    file_id: int, request: Request, item: str = Form(...), from_place: str = Form(..., max_length=80),
    to_place: str = Form(..., max_length=80), courier_id: str = Form(""), by_hand: str = Form(""),
    tracking_no: str = Form(""), sent_on: str = Form(""), received_now: str = Form(""), user=Depends(WRITE),
):
    if item not in ("passport", "documents", "ticket"):
        raise HTTPException(400)
    sent = to_date(sent_on) or date.today()
    with transaction() as conn:
        f = conn.execute("SELECT * FROM files WHERE id = %s", (file_id,)).fetchone()
        m = insert("courier_movements", {
            "file_id": file_id, "item": item, "from_place": from_place.strip(), "to_place": to_place.strip(),
            "courier_id": int(courier_id) if courier_id.isdigit() else None, "by_hand": by_hand.strip() or None,
            "tracking_no": tracking_no.strip() or None, "sent_on": sent,
            "received_on": sent if received_now else None, "created_by": user["id"],
        }, conn=conn)
        msg = _after_courier(conn, f, m, user)
        audit(user["id"], "courier", "file", file_id, {"movement_id": m["id"], "item": item,
                                                       "to": to_place.strip()}, conn=conn)
    return _stages_response(request, file_id, user, msg=msg, toast="Movement saved")


def _after_courier(conn, f, m, user):
    """Update 'where is the passport now' (FR-6.2) and the PPT-R stamp (FR-4.8); queue the message."""
    if m["item"] != "passport":
        return None
    at_office = m["to_place"].lower() in ("office", "kasia office", "office, kasia")
    if m["received_on"]:
        location = "Office" if at_office else m["to_place"]
    else:
        location = f"On the way to {m['to_place']}"
    conn.execute("UPDATE files SET passport_location = %s, updated_at = now() WHERE id = %s", (location, f["id"]))
    if at_office and m["received_on"] and f["visa_done_on"] and not f["passport_back_on"]:
        conn.execute("UPDATE files SET passport_back_on = %s WHERE id = %s", (m["received_on"], f["id"]))
    return queue_message(conn, "passport_moved", f["customer_id"], file_id=f["id"], item="passport",
                         location="in our office, Kasia" if location == "Office" else location,
                         date=(m["received_on"] or m["sent_on"]).strftime("%d %b %Y"))


@router.post("/courier/{mid}/received")
def courier_received(mid: int, request: Request, user=Depends(WRITE)):
    with transaction() as conn:
        m = conn.execute("UPDATE courier_movements SET received_on = current_date WHERE id = %s AND received_on IS NULL"
                         " RETURNING *", (mid,)).fetchone()
        if not m:
            raise HTTPException(404)
        f = conn.execute("SELECT * FROM files WHERE id = %s", (m["file_id"],)).fetchone()
        msg = _after_courier(conn, f, m, user)
        audit(user["id"], "courier_received", "file", f["id"], {"movement_id": mid}, conn=conn)
    return _stages_response(request, f["id"], user, msg=msg, toast="Marked received")


# ---------------------------------------------------------------- close the file

@router.post("/{file_id}/departed")
def mark_departed(file_id: int, request: Request, user=Depends(WRITE)):
    with transaction() as conn:
        conn.execute("UPDATE files SET status = 'departed', updated_at = now() WHERE id = %s AND status = 'active'", (file_id,))
        conn.execute("UPDATE ticket_bookings SET status = 'travelled', updated_at = now() WHERE file_id = %s"
                     " AND status = 'booked' AND travel_date <= current_date", (file_id,))
        refresh_file_stage(conn, file_id)
        audit(user["id"], "departed", "file", file_id, {}, conn=conn)
    return _stages_response(request, file_id, user, toast="Marked departed")


@router.post("/{file_id}/cancel")
def cancel_file(file_id: int, request: Request, reason: str = Form(..., min_length=3, max_length=300),
                user=Depends(require_role("owner"))):
    with transaction() as conn:
        conn.execute("UPDATE files SET status = 'cancelled', cancel_reason = %s, updated_at = now() WHERE id = %s",
                     (reason.strip(), file_id))
        conn.execute("""UPDATE stage_attempts SET status = 'cancelled' WHERE status = 'scheduled'
                        AND file_service_id IN (SELECT id FROM file_services WHERE file_id = %s)""", (file_id,))
        conn.execute("UPDATE messages SET status = 'skipped', done_by = %s, done_at = now()"
                     " WHERE file_id = %s AND status = 'to_send'", (user["id"], file_id))
        # the visa can now go to another candidate; its history stays (FR-5.3)
        conn.execute("UPDATE visa_assignments SET released_on = now(), reason = 'File cancelled'"
                     " WHERE file_id = %s AND released_on IS NULL", (file_id,))
        refresh_file_stage(conn, file_id)
        audit(user["id"], "cancel", "file", file_id, {"reason": reason.strip()}, conn=conn)
    return RedirectResponse(f"/app/payments/refund?file_id={file_id}", status_code=303)
