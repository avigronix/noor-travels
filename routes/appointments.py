"""Appointments: medical, skill test, VFS attempts + flights, by day (SRS 5.4, 5.10). Design: Appointments.dc.html.

One paginated, time-ordered list (UNION of stage attempts and ticket travel dates), grouped by day in the template.
"""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request

from common.auth import require_user
from common.db import fetch_all, fetch_one, paginate
from common.stages import CITIES, DOCUMENTS
from common.web import pager, render

router = APIRouter(prefix="/app/appointments", tags=["appointments"])
TYPES = [("medical", "Medical"), ("skill_test", "Skill test"), ("vfs", "VFS"), ("flight", "Flight")]


@router.get("")
def appointment_list(request: Request, day: str = "week", start: str = "", type: str = "", city: str = "",
                     pg: dict = Depends(pager), user=Depends(require_user)):
    today = date.today()
    try:
        first = date.fromisoformat(start) if start else today
    except ValueError:
        first = today
    if day == "today":
        first, last = today, today
    elif day == "tomorrow":
        first = last = today + timedelta(days=1)
    else:
        day, last = "week", first + timedelta(days=6)
    params = {"d1": first, "d2": last, "city": city}
    parts = []
    if type in ("", "medical", "skill_test", "vfs"):
        svc = "AND s.service = %(svc)s" if type else ""
        parts.append(f"""
            SELECT 'stage' AS kind, s.service AS type, a.on_date, a.at_time, a.attempt_no, a.status, a.city AS place,
                   s.data->>'centre' AS centre, f.id AS file_id, f.serial_no, c.id AS customer_id, c.name, c.mobile,
                   NULL::int AS booking_id, NULL::int AS ticket_no,
                   (SELECT m.status FROM messages m WHERE m.dedupe_key = 'appt:' || a.id || ':eve') AS reminder
            FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id
            JOIN files f ON f.id = s.file_id JOIN customers c ON c.id = f.customer_id
            WHERE a.on_date BETWEEN %(d1)s AND %(d2)s AND a.status IN ('scheduled', 'done', 'failed') {svc}
              AND (%(city)s = '' OR a.city = %(city)s)
        """)
        params["svc"] = type
    if type in ("", "flight"):
        parts.append("""
            SELECT 'flight' AS kind, 'flight' AS type, t.travel_date AS on_date,
                   (SELECT (l.depart_at AT TIME ZONE 'Asia/Kolkata')::time FROM ticket_legs l WHERE l.booking_id = t.id ORDER BY l.leg_no LIMIT 1) AS at_time,
                   NULL::int AS attempt_no, t.status, (SELECT (array_agg(l.from_code ORDER BY l.leg_no))[1] || ' → ' || string_agg(l.to_code, ' → ' ORDER BY l.leg_no) FROM ticket_legs l WHERE l.booking_id = t.id) AS place,
                   t.airline AS centre, t.file_id, f.serial_no, c.id AS customer_id, c.name, c.mobile,
                   t.id AS booking_id, t.ticket_no,
                   (SELECT m.status FROM messages m WHERE m.dedupe_key = 'travel:' || t.id) AS reminder
            FROM ticket_bookings t JOIN customers c ON c.id = t.customer_id LEFT JOIN files f ON f.id = t.file_id
            WHERE t.travel_date BETWEEN %(d1)s AND %(d2)s AND t.status <> 'cancelled' AND %(city)s = ''
        """)
    result = paginate(" UNION ALL ".join(parts) + " ORDER BY on_date, at_time NULLS LAST, name", params, **pg)

    days = []
    for r in result["rows"]:
        if not days or days[-1]["date"] != r["on_date"]:
            days.append({"date": r["on_date"], "entries": []})
        days[-1]["entries"].append(r)

    # missed or not updated: appointment date passed but still 'scheduled' (FR-10.6)
    missed = fetch_all("""
        SELECT a.on_date, s.service, f.id AS file_id, f.serial_no, c.name
        FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id
        JOIN files f ON f.id = s.file_id JOIN customers c ON c.id = f.customer_id
        WHERE a.status = 'scheduled' AND a.on_date < current_date AND f.status = 'active'
        ORDER BY a.on_date LIMIT 10
    """)
    tpl = fetch_one("SELECT body_hi, body_en FROM message_templates WHERE trigger = 'appointment_reminder'")
    return render(request, "appointments/list.html", {
        "result": result, "days": days, "day": day, "first": first, "last": last, "today": today, "tomorrow": today + timedelta(days=1),
        "type": type, "city": city, "types": TYPES, "cities": [c for c in CITIES if c], "missed": missed,
        "preview": tpl and (tpl["body_hi"] or tpl["body_en"]), "docs": DOCUMENTS, "active": "appointments",
        "filters_on": sum(1 for x in (type, city) if x),
        "prev": first - timedelta(days=7), "next": first + timedelta(days=7),
    }, partial="appointments/_rows.html")
