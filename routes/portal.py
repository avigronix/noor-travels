"""Customer portal – Phase 2 (SRS 5.12, FR-12.1–12.3). Designs: CandidateLogin.dc.html, CandidateStatus.dc.html.

Login with mobile + date of birth (no OTP, no SMS). 5 wrong tries lock it for 30 minutes. Uses its own
session key 'cid' (staff use 'uid'). Read-only: never shows cost price, margin or staff notes; passport last 4 only.
"""
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from common.db import execute, fetch_all, fetch_one
from common.money import booking_totals, file_totals
from common.stages import DOCUMENTS, SERVICES, setting, to_date
from common.web import BUSINESS, render

router = APIRouter(prefix="/my", tags=["portal"])
MAX_TRIES, LOCK_MINUTES = 5, 30


@router.get("")
def portal_login(request: Request):
    if request.session.get("cid"):
        return RedirectResponse("/my/file", status_code=303)
    return render(request, "portal/login.html", {})


@router.post("")
def portal_login_submit(request: Request, mobile: str = Form(""), dob: str = Form("")):
    digits = re.sub(r"\D", "", mobile)
    digits = digits[2:] if len(digits) == 12 and digits.startswith("91") else digits
    born = to_date(dob)
    ctx = {"mobile": digits, "dob": dob}
    if len(digits) != 10 or not born:
        return render(request, "portal/login.html", {**ctx, "error": "Mobile number (10 digits) aur janam tithi dono bharein."})
    people = fetch_all("SELECT id, dob, portal_failed, portal_locked_until FROM customers WHERE mobile = %s LIMIT 20", (digits,))
    now = datetime.now(timezone.utc)
    locked = [p for p in people if p["portal_locked_until"] and p["portal_locked_until"] > now]
    if locked:
        mins = int((max(p["portal_locked_until"] for p in locked) - now).total_seconds() // 60) + 1
        return render(request, "portal/login.html", {**ctx, "error": f"Bahut baar galat koshish. {mins} minute baad try karein."})
    match = next((p for p in people if p["dob"] == born), None)
    if not match:
        for p in people:       # count the wrong try against everyone on that mobile (FR-12.1)
            failed = p["portal_failed"] + 1
            execute("UPDATE customers SET portal_failed = %s, portal_locked_until = %s WHERE id = %s",
                    (0 if failed >= MAX_TRIES else failed, now + timedelta(minutes=LOCK_MINUTES) if failed >= MAX_TRIES else None, p["id"]))
        return render(request, "portal/login.html", {**ctx, "error": "Mobile number ya janam tithi match nahi hui. Office se sampark karein."})
    execute("UPDATE customers SET portal_failed = 0, portal_locked_until = NULL WHERE id = %s", (match["id"],))
    request.session["cid"] = match["id"]
    return RedirectResponse("/my/file", status_code=303)


@router.post("/logout")
def portal_logout(request: Request):
    request.session.pop("cid", None)
    return RedirectResponse("/my", status_code=303)


@router.get("/file")
def my_file(request: Request, file: int | None = None, ticket: int | None = None):
    cid = request.session.get("cid")
    if not cid:
        return RedirectResponse("/my", status_code=303)
    c = fetch_one("SELECT id, name, mobile, passport_no, passport_expiry, profession FROM customers WHERE id = %s", (cid,))
    if not c:
        request.session.pop("cid", None)
        return RedirectResponse("/my", status_code=303)
    files = fetch_all("SELECT id, serial_no, visa_type, country, status, current_stage, created_at FROM files"
                      " WHERE customer_id = %s ORDER BY (status = 'active') DESC, created_at DESC LIMIT 20", (cid,))
    tickets = fetch_all("""SELECT t.id, t.ticket_no, t.pnr, t.travel_date, t.status, t.airline,
                              (SELECT (array_agg(l.from_code ORDER BY l.leg_no))[1] || ' → ' || string_agg(l.to_code, ' → ' ORDER BY l.leg_no) FROM ticket_legs l WHERE l.booking_id = t.id) AS route
                           FROM ticket_bookings t WHERE t.file_id IS NULL AND (t.customer_id = %(c)s OR EXISTS
                              (SELECT 1 FROM ticket_passengers p WHERE p.booking_id = t.id AND p.customer_id = %(c)s))
                           ORDER BY t.travel_date DESC NULLS LAST LIMIT 20""", {"c": cid})
    f = next((x for x in files if x["id"] == file), files[0] if files and not ticket else None)
    ctx = {"c": c, "files": files, "tickets": tickets, "f": f, "t": None}
    if f:
        ctx.update(_file_view(f, c))
    elif tickets:
        t = next((x for x in tickets if x["id"] == ticket), tickets[0])
        ctx.update(t=t, money=booking_totals(t["id"]), receipts=_receipts("booking_id", t["id"]))
    upi = setting("upi_id")
    if upi and ctx.get("money") and ctx["money"]["balance"] > 0:
        ctx["upi_link"] = (f"upi://pay?pa={quote(upi)}&pn={quote(BUSINESS['name'])}&am={ctx['money']['balance']:.2f}"
                           f"&cu=INR&tn={quote('File ' + str(f['serial_no']) if f else 'Ticket')}")
    return render(request, "portal/file.html", ctx)


def _receipts(col, val):
    from routes.payments import public_receipt_url
    rows = fetch_all(f"SELECT id, paid_on, amount, receipt_no FROM payments WHERE {col} = %s AND direction = 'in'"
                     " AND receipt_no IS NOT NULL ORDER BY paid_on DESC LIMIT 20", (val,))
    for r in rows:
        r["url"] = public_receipt_url(r["id"])
    return rows


def _file_view(f, c):
    services = fetch_all("SELECT id, service, status, data FROM file_services WHERE file_id = %s AND status <> 'cancelled'"
                         " ORDER BY sort_order LIMIT 20", (f["id"],))
    visa = fetch_one("SELECT v.visa_no FROM files x JOIN visas v ON v.id = x.visa_id WHERE x.id = %s", (f["id"],))
    nxt = fetch_one("""
        SELECT a.on_date, a.at_time, a.city, a.attempt_no, s.service, s.data->>'centre' AS centre
        FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id
        WHERE s.file_id = %s AND a.status = 'scheduled' AND a.on_date >= current_date ORDER BY a.on_date, a.at_time LIMIT 1
    """, (f["id"],))
    flight = fetch_one("SELECT travel_date, pnr, (SELECT (array_agg(l.from_code ORDER BY l.leg_no))[1] || ' → ' || string_agg(l.to_code, ' → ' ORDER BY l.leg_no)"
                       " FROM ticket_legs l WHERE l.booking_id = t.id) AS route FROM ticket_bookings t WHERE t.file_id = %s"
                       " AND t.status = 'booked' ORDER BY travel_date LIMIT 1", (f["id"],))
    location = fetch_one("SELECT passport_location FROM files WHERE id = %s", (f["id"],))["passport_location"]
    steps = [("Registration", "done", "✓ Ho gaya"), ("Visa", "done" if visa else "wait", "✓ Ho gaya" if visa else "Baaki")]
    for s in services:
        label = SERVICES[s["service"]]["label"]
        if s["status"] == "done":
            res = (s["data"] or {}).get("result")
            steps.append((label, "done", "✓ " + (res if res and res != "Pending" else "Ho gaya")))
        elif s["status"] == "failed":
            steps.append((label, "now", "Dobara test hoga"))
        elif s["status"] == "scheduled":
            steps.append((label, "now", "Book ho gaya"))
        else:
            steps.append((label, "wait", "Baaki"))
    steps.append(("Rawana (departure)", "done" if f["status"] == "departed" else "wait", "✓ Ho gaya" if f["status"] == "departed" else "Baaki"))
    money = file_totals(f["id"])
    retest = fetch_one("SELECT coalesce(sum(amount), 0) AS n FROM charges WHERE file_id = %s AND kind = 'retest'", (f["id"],))["n"]
    return {"steps": steps, "done": sum(1 for s in steps if s[1] == "done"), "next": nxt, "flight": flight,
            "docs": DOCUMENTS.get(nxt["service"], "original passport") if nxt else None, "location": location,
            "money": money, "retest": retest, "receipts": _receipts("file_id", f["id"]), "SERVICES": SERVICES,
            "today": date.today()}
