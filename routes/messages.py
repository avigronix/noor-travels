"""Messages & reminders – the free WhatsApp 'To send' queue (SRS 5.11). Design: docs/design/Messages.dc.html.

Nothing is sent by the server: each row opens wa.me with the number and text; staff press Send in WhatsApp
and tap Mark sent (or Skip). Rules (on/off) and wording are owner-only (FR-11.4).
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from common.auth import require_role, require_user
from common.db import audit, execute, fetch_all, fetch_one, paginate, transaction
from common.messages import IST, _Blank, queue_message
from common.stages import DOCUMENTS, SERVICES
from common.web import form_data, pager, render

router = APIRouter(prefix="/app/messages", tags=["messages"])
WRITE = require_role("owner", "staff")
TRIGGER_WHEN = {
    "file_created": "Right after saving a registration", "visa_attached": "Right after attaching a visa",
    "appointment_booked": "Right after booking an appointment", "appointment_changed": "Right after a re-appointment",
    "appointment_reminder": "6:00 pm the day before, and the morning of", "stage_done": "Right after Mark done",
    "stage_failed": "After staff saves unfit / failed", "passport_moved": "Right after a passport movement",
    "visa_done": "Right after visa stamping is marked done", "payment_received": "Right after a payment",
    "balance_due": "Every 7 days while due", "ticket_booked": "Right after booking a ticket",
    "travel_day": "Morning of travel (also in quiet hours)",
}
SAMPLE = {"name": "Arif", "file_no": "4277", "services": "GAMCA medical, Skill test", "portal_link": "https://…/my",
          "stage": "VFS appointment", "date": "11 Nov 2026", "time": "9:30 am", "place": "Lucknow", "documents": "passport, photos",
          "next_step": "Skill test", "result": "unfit", "amount": "₹10,000", "balance": "₹30,000", "receipt_link": "https://…/r/…",
          "visa_no": "19085XXXXX", "item": "passport", "location": "Mumbai agency", "bank": "ICICI Bank, Kasia",
          "route": "LKO → RUH", "pnr": "KX4P2M", "report_time": "3 hours before"}
GROUPS = [("medical", "Everyone with a medical tomorrow"), ("skill_test", "Everyone with a skill test tomorrow"),
          ("vfs", "Everyone with a VFS appointment tomorrow"), ("flight", "Everyone flying tomorrow")]


@router.get("")
def message_list(request: Request, tab: str = "queue", q: str = "", file_id: str = "", customer_id: str = "",
                 pg: dict = Depends(pager), user=Depends(require_user)):
    if tab in ("rules", "templates"):
        return _rules(request, tab, user)
    where, params = ["true"], {}
    if tab == "queue":
        where.append("m.status = 'to_send' AND m.due_at <= now()")
        order = "m.due_at, m.id"                       # oldest first (FR-11.3)
    elif tab in ("sent", "skipped"):
        where.append("m.status = %(st)s"); params["st"] = tab
        order = "m.done_at DESC, m.id DESC"
    elif tab == "later":
        where.append("m.status = 'to_send' AND m.due_at > now()")
        order = "m.due_at, m.id"
    else:
        tab, order = "all", "m.created_at DESC, m.id DESC"
    if file_id.isdigit():
        where.append("m.file_id = %(fid)s"); params["fid"] = int(file_id)
    if customer_id.isdigit():
        where.append("m.customer_id = %(cid)s"); params["cid"] = int(customer_id)
    if q.strip():
        where.append("(c.name ILIKE %(q)s OR c.mobile ILIKE %(q)s)"); params["q"] = f"%{q.strip()}%"
    result = paginate(f"""
        SELECT m.*, c.name, c.mobile, f.serial_no, t.ticket_no, u.name AS done_by_name, mt.title
        FROM messages m JOIN customers c ON c.id = m.customer_id
        LEFT JOIN files f ON f.id = m.file_id LEFT JOIN ticket_bookings t ON t.id = m.booking_id
        LEFT JOIN users u ON u.id = m.done_by LEFT JOIN message_templates mt ON mt.trigger = m.trigger
        WHERE {' AND '.join(where)} ORDER BY {order}
    """, params, **pg)
    stats = fetch_one("""
        SELECT count(*) FILTER (WHERE status = 'to_send' AND due_at <= now()) AS due,
               count(*) FILTER (WHERE status = 'sent' AND done_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata') AS sent_today,
               count(*) FILTER (WHERE status = 'to_send' AND due_at > now()) AS later
        FROM messages
    """)
    return render(request, "messages/list.html", {
        "result": result, "tab": tab, "q": q, "file_id": file_id, "customer_id": customer_id, "stats": stats,
        "groups": GROUPS, "active": "messages",
    }, partial="messages/_rows.html")


@router.post("/{mid}/sent")
def mark_sent(mid: int, request: Request, user=Depends(WRITE)):
    return _done(mid, "sent", user)


@router.post("/{mid}/skip")
def mark_skip(mid: int, request: Request, user=Depends(WRITE)):
    return _done(mid, "skipped", user)


def _done(mid, status, user):
    """The queue row's status is the only thing that changes – who and when are recorded (FR-11.5)."""
    row = fetch_one("UPDATE messages SET status = %s, done_by = %s, done_at = now() WHERE id = %s AND status = 'to_send'"
                    " RETURNING id, file_id", (status, user["id"], mid))
    if not row:
        return HTMLResponse('<p class="px-4 py-3 text-sm text-slate-500">Already done.</p>')
    audit(user["id"], f"message_{status}", "message", mid, {"file_id": row["file_id"]})
    return HTMLResponse(f'<div class="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600" role="status">'
                        f'✓ Marked {status}.</div>')


@router.post("/group")
def prepare_group(request: Request, group: str = Form(...), user=Depends(WRITE)):
    """FR-11.6: one message per customer for a group, then sent one by one from the list."""
    tomorrow = (datetime.now(IST) + timedelta(days=1)).date()
    n = 0
    with transaction() as conn:
        if group == "flight":
            rows = conn.execute("""SELECT t.id, t.customer_id, t.file_id, t.pnr,
                    (SELECT (array_agg(l.from_code ORDER BY l.leg_no))[1] || ' → ' || string_agg(l.to_code, ' → ' ORDER BY l.leg_no) FROM ticket_legs l WHERE l.booking_id = t.id) AS route
                FROM ticket_bookings t WHERE t.travel_date = %s AND t.status = 'booked' LIMIT 200""", (tomorrow,)).fetchall()
            for r in rows:
                n += bool(queue_message(conn, "travel_day", r["customer_id"], file_id=r["file_id"], booking_id=r["id"],
                                        dedupe_key=f"travel:{r['id']}", route=r["route"] or "", pnr=r["pnr"] or ""))
        elif group in SERVICES:
            rows = conn.execute("""SELECT a.id, a.on_date, a.at_time, a.city, s.service, s.data->>'centre' AS centre, f.id AS file_id, f.customer_id
                FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id JOIN files f ON f.id = s.file_id
                WHERE a.on_date = %s AND a.status = 'scheduled' AND s.service = %s AND f.status = 'active' LIMIT 200""",
                                (tomorrow, group)).fetchall()
            for r in rows:
                n += bool(queue_message(conn, "appointment_reminder", r["customer_id"], file_id=r["file_id"],
                                        dedupe_key=f"appt:{r['id']}:eve", stage=SERVICES[r["service"]]["label"],
                                        date=r["on_date"].strftime("%d %b"), time=r["at_time"].strftime("%I:%M %p").lstrip("0") if r["at_time"] else "",
                                        place=", ".join(x for x in (r["centre"], r["city"]) if x), documents=DOCUMENTS.get(r["service"], "")))
        audit(user["id"], "message_group", "message", None, {"group": group, "prepared": n}, conn=conn)
    return RedirectResponse(f"/app/messages?tab={'queue' if n else 'later'}", status_code=303)


# ---------------------------------------------------------------- rules & templates (FR-11.4, owner)

def _rules(request, tab, user):
    templates = fetch_all("SELECT * FROM message_templates ORDER BY id LIMIT 50")
    return render(request, "messages/list.html", {"tab": tab, "templates": templates, "when": TRIGGER_WHEN, "q": "",
                                                   "file_id": "", "customer_id": "", "groups": GROUPS, "active": "messages",
                                                   "stats": fetch_one("SELECT count(*) FILTER (WHERE status = 'to_send' AND due_at <= now()) AS due,"
                                                                      " 0 AS sent_today, count(*) FILTER (WHERE status = 'to_send' AND due_at > now()) AS later FROM messages")})


@router.post("/rules/{trigger}")
def toggle_rule(trigger: str, request: Request, user=Depends(require_role("owner"))):
    row = fetch_one("UPDATE message_templates SET is_active = NOT is_active, updated_at = now() WHERE trigger = %s RETURNING *", (trigger,))
    if not row:
        raise HTTPException(404)
    audit(user["id"], "update", "message_template", row["id"], {"is_active": row["is_active"]})
    return render(request, "messages/_rule.html", {"t": row, "when": TRIGGER_WHEN})


@router.get("/templates/{trigger}")
def edit_template(trigger: str, request: Request, user=Depends(require_role("owner"))):
    t = fetch_one("SELECT * FROM message_templates WHERE trigger = %s", (trigger,))
    if not t:
        raise HTTPException(404)
    return render(request, "messages/template.html", {"t": t, "preview": _preview(t["body_en"]),
                                                       "placeholders": list(SAMPLE), "active": "messages", "saved": False})


def _preview(text):
    try:
        return (text or "").format_map(_Blank(SAMPLE))
    except (ValueError, IndexError):
        return "⚠ Check the {placeholders} – a brace is not closed."


@router.post("/templates/{trigger}/preview")
def template_preview(trigger: str, request: Request, form=Depends(form_data), user=Depends(require_role("owner"))):
    """Live preview of the message text while it is typed."""
    return HTMLResponse(f'<div class="whitespace-pre-line">{_escape(_preview(form.get("body_en", "")))}</div>')


def _escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@router.post("/templates/{trigger}")
def save_template(trigger: str, request: Request, title: str = Form(..., max_length=80), body_en: str = Form(..., max_length=1000),
                  user=Depends(require_role("owner"))):
    t = fetch_one("UPDATE message_templates SET title = %s, body_en = %s, updated_at = now() WHERE trigger = %s RETURNING *",
                  (title.strip(), body_en.strip(), trigger))
    if not t:
        raise HTTPException(404)
    audit(user["id"], "update", "message_template", t["id"], {"trigger": trigger})
    return render(request, "messages/template.html", {"t": t, "preview": _preview(t["body_en"]),
                                                       "placeholders": list(SAMPLE), "active": "messages", "saved": True})

