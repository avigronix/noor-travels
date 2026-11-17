"""Dashboard – 'what needs doing today?' (SRS 5.10, FR-10.1–10.9). Designs: Dashboard.dc.html, MobileDashboard.dc.html.

Each block is one small, indexed query with a LIMIT, so the page stays fast however large the data grows;
the full lists live on their own paginated pages. Staff see only their own files and never margin (FR-10.9).
"""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request

from common.auth import require_user
from common.db import fetch_all, fetch_one
from common.messages import IST
from common.money import BOOKING_BALANCE_SQL, FILE_BALANCE_SQL
from common.stages import SERVICES
from common.web import render

router = APIRouter(prefix="/app", tags=["dashboard"])


def _agenda(day, mine, p):
    """Appointments, courier hand-overs and flights of one day in time order (FR-10.4)."""
    return fetch_all(f"""
        SELECT * FROM (
          SELECT a.at_time AS at, s.service AS tag, f.id AS file_id, f.serial_no::text AS ref, c.id AS customer_id, c.name, c.mobile,
                 concat_ws(' · ', CASE WHEN a.attempt_no > 1 THEN 'attempt ' || a.attempt_no END, s.data->>'centre', a.city) AS what,
                 a.attempt_no > 1 AS retest
          FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id JOIN files f ON f.id = s.file_id
          JOIN customers c ON c.id = f.customer_id
          WHERE a.on_date = %(day)s AND a.status = 'scheduled' {mine}
          UNION ALL
          SELECT NULL, 'courier', f.id, f.serial_no::text, c.id, c.name, c.mobile,
                 initcap(m.item) || ' ' || m.from_place || ' → ' || m.to_place, false
          FROM courier_movements m JOIN files f ON f.id = m.file_id JOIN customers c ON c.id = f.customer_id
          WHERE m.sent_on = %(day)s AND m.received_on IS NULL {mine}
          UNION ALL
          SELECT (SELECT (l.depart_at AT TIME ZONE 'Asia/Kolkata')::time FROM ticket_legs l WHERE l.booking_id = t.id ORDER BY l.leg_no LIMIT 1),
                 'flight', t.file_id, 'T-' || t.ticket_no, c.id, c.name, c.mobile,
                 coalesce((SELECT (array_agg(l.from_code ORDER BY l.leg_no))[1] || ' → ' || string_agg(l.to_code, ' → ' ORDER BY l.leg_no) FROM ticket_legs l WHERE l.booking_id = t.id), '')
                 || coalesce(' · PNR ' || t.pnr, ''), false
          FROM ticket_bookings t JOIN customers c ON c.id = t.customer_id
          WHERE t.travel_date = %(day)s AND t.status = 'booked'
        ) x ORDER BY at NULLS LAST, name LIMIT 30
    """, {**p, "day": day})


@router.get("")
def dashboard(request: Request, day: str = "today", user=Depends(require_user)):
    staff = user["role"] == "staff"
    mine = "AND f.assigned_to = %(uid)s" if staff else ""
    p = {"uid": user["id"]}
    today = date.today()
    agenda_day = today + timedelta(days=1) if day == "tomorrow" else today
    agenda = _agenda(agenda_day, mine, p)
    if request.headers.get("HX-Request") and request.headers.get("HX-Boosted") != "true":
        return render(request, "dashboard/_agenda.html", {"agenda": agenda, "day": day, "SERVICES": SERVICES})

    stats = fetch_one(f"""
        SELECT
          (SELECT count(*) FROM files f WHERE f.status = 'active' {mine})                                       AS active_files,
          (SELECT count(*) FROM files f WHERE f.status = 'active' AND f.created_at >= date_trunc('month', current_date) {mine}) AS new_month,
          (SELECT count(*) FROM files f WHERE f.visa_done_on >= date_trunc('month', current_date) {mine})       AS visa_done_month,
          (SELECT count(*) FROM files f WHERE f.status = 'departed' AND f.updated_at >= date_trunc('month', current_date) {mine}) AS departed_month,
          (SELECT count(*) FROM ticket_bookings WHERE created_at >= date_trunc('month', current_date))          AS tickets_month,
          (SELECT count(*) FROM ticket_bookings WHERE travel_date BETWEEN current_date AND current_date + 7 AND status = 'booked') AS flights_7d,
          (SELECT count(*) FROM ticket_bookings WHERE travel_date = current_date AND status = 'booked')         AS flights_today,
          (SELECT count(*) FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id JOIN files f ON f.id = s.file_id
              WHERE a.on_date = current_date AND a.status = 'scheduled' {mine})                                  AS appointments_today,
          (SELECT count(*) FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id JOIN files f ON f.id = s.file_id
              WHERE a.on_date = current_date + 1 AND a.status = 'scheduled' {mine})                              AS appointments_tomorrow,
          (SELECT count(*) FROM files f WHERE f.status = 'active' AND f.passport_location <> 'Office' {mine})   AS passports_out,
          (SELECT count(*) FROM messages WHERE status = 'to_send' AND due_at <= now())                         AS to_send,
          (SELECT count(*) FROM enquiries WHERE status = 'new')                                                AS new_enquiries,
          (SELECT count(*) FROM files f WHERE f.status = 'active' AND f.updated_at < now() - interval '7 days' {mine}) AS stuck
    """, p)
    due = fetch_one(f"""
        SELECT coalesce(sum(b), 0) AS amount, count(*) FILTER (WHERE b > 0) AS customers FROM (
          SELECT {FILE_BALANCE_SQL} AS b FROM files f WHERE f.status = 'active' {mine}
          UNION ALL
          SELECT {BOOKING_BALANCE_SQL} FROM ticket_bookings t WHERE t.file_id IS NULL AND t.status <> 'cancelled' {'AND false' if staff else ''}
        ) x WHERE b > 0
    """, p)

    pipeline = fetch_all(f"""
        SELECT f.current_stage AS stage, count(*) AS n FROM files f WHERE f.status = 'active' {mine}
        GROUP BY 1 ORDER BY min(CASE f.current_stage {' '.join(f"WHEN '{k}' THEN {i}" for i, k in enumerate(SERVICES))} ELSE 99 END) LIMIT 20
    """, p)
    by_type = fetch_all(f"SELECT f.visa_type, count(*) AS n FROM files f WHERE f.status = 'active' {mine} GROUP BY 1 ORDER BY 2 DESC LIMIT 10", p)

    money = by_account = None
    if user["role"] in ("owner", "accountant"):          # FR-10.7
        money = fetch_one("""
            SELECT coalesce(sum(amount) FILTER (WHERE direction = 'in'), 0)  AS collected,
                   coalesce(sum(amount) FILTER (WHERE direction = 'out'), 0) AS paid_out
            FROM payments WHERE paid_on >= date_trunc('month', current_date)
        """)
        money["margin"] = money["collected"] - money["paid_out"]
        by_account = fetch_all("""
            SELECT a.name, coalesce(sum(p.amount), 0) AS n FROM bank_accounts a
            LEFT JOIN payments p ON p.bank_account_id = a.id AND p.direction = 'in' AND p.paid_on >= date_trunc('month', current_date)
            WHERE a.is_active GROUP BY a.id, a.name ORDER BY a.id LIMIT 10
        """)

    flights = fetch_all(f"""
        SELECT t.id, t.travel_date, t.pnr, t.file_id, c.id AS customer_id, c.name, f.serial_no,
               (SELECT (array_agg(l.from_code ORDER BY l.leg_no))[1] || ' → ' || string_agg(l.to_code, ' → ' ORDER BY l.leg_no) FROM ticket_legs l WHERE l.booking_id = t.id) AS route,
               {BOOKING_BALANCE_SQL} AS balance
        FROM ticket_bookings t JOIN customers c ON c.id = t.customer_id LEFT JOIN files f ON f.id = t.file_id
        WHERE t.travel_date BETWEEN current_date AND current_date + 7 AND t.status = 'booked'
        ORDER BY t.travel_date LIMIT 8
    """)
    to_send = fetch_all("""
        SELECT m.id, m.body, m.trigger, c.name, c.mobile, mt.title FROM messages m JOIN customers c ON c.id = m.customer_id
        LEFT JOIN message_templates mt ON mt.trigger = m.trigger
        WHERE m.status = 'to_send' AND m.due_at <= now() ORDER BY m.due_at LIMIT 5
    """)

    # FR-10.6 needs attention – each kind capped, newest problems first
    attention = fetch_all(f"""
        (SELECT 'failed' AS kind, f.id AS file_id, f.serial_no, c.name, s.service AS detail, NULL::numeric AS amount, NULL::date AS on_date
         FROM file_services s JOIN files f ON f.id = s.file_id JOIN customers c ON c.id = f.customer_id
         WHERE s.status = 'failed' AND f.status = 'active' {mine} ORDER BY s.updated_at DESC LIMIT 3)
        UNION ALL
        (SELECT 'no_result', f.id, f.serial_no, c.name, s.service, NULL, a.on_date
         FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id JOIN files f ON f.id = s.file_id JOIN customers c ON c.id = f.customer_id
         WHERE a.status = 'scheduled' AND a.on_date < current_date AND f.status = 'active' {mine} ORDER BY a.on_date LIMIT 3)
        UNION ALL
        (SELECT 'ppt_late', f.id, f.serial_no, c.name, NULL, NULL, f.visa_done_on
         FROM files f JOIN customers c ON c.id = f.customer_id
         WHERE f.status = 'active' AND f.visa_done_on < current_date - 7 AND f.passport_back_on IS NULL {mine} LIMIT 3)
        UNION ALL
        (SELECT 'due_stamping', f.id, f.serial_no, c.name, NULL, {FILE_BALANCE_SQL}, NULL
         FROM files f JOIN customers c ON c.id = f.customer_id
         WHERE f.status = 'active' AND f.current_stage = 'stamping' AND {FILE_BALANCE_SQL} > 0 {mine} LIMIT 3)
        UNION ALL
        (SELECT 'expiry', f.id, f.serial_no, c.name, NULL, NULL, c.passport_expiry
         FROM files f JOIN customers c ON c.id = f.customer_id
         WHERE f.status = 'active' AND c.passport_expiry < coalesce(f.expected_travel, current_date) + interval '6 months' {mine}
         ORDER BY c.passport_expiry LIMIT 3)
        UNION ALL
        (SELECT 'stuck', f.id, f.serial_no, c.name, f.current_stage, NULL, f.updated_at::date
         FROM files f JOIN customers c ON c.id = f.customer_id
         WHERE f.status = 'active' AND f.updated_at < now() - interval '7 days' {mine} ORDER BY f.updated_at LIMIT 3)
    """, p)

    passports = fetch_all(f"""
        SELECT f.id, f.serial_no, f.passport_location, c.id AS customer_id, c.name,
               (SELECT max(coalesce(m.received_on, m.sent_on)) FROM courier_movements m WHERE m.file_id = f.id AND m.item = 'passport') AS since
        FROM files f JOIN customers c ON c.id = f.customer_id
        WHERE f.status = 'active' AND f.passport_location <> 'Office' {mine} ORDER BY since NULLS LAST LIMIT 5
    """, p)
    enquiries = fetch_all("SELECT id, name, mobile, service, message, created_at FROM enquiries WHERE status = 'new' ORDER BY created_at DESC LIMIT 5")
    team = [] if staff else fetch_all("""
        SELECT u.name, count(f.id) AS n FROM users u LEFT JOIN files f ON f.assigned_to = u.id AND f.status = 'active'
        WHERE u.is_active AND u.role <> 'accountant' GROUP BY u.id, u.name ORDER BY n DESC LIMIT 10
    """)

    return render(request, "dashboard/index.html", {
        "stats": stats, "due": due, "agenda": agenda, "day": day, "pipeline": pipeline, "by_type": by_type,
        "money": money, "by_account": by_account, "flights": flights, "to_send": to_send, "attention": attention,
        "passports": passports, "enquiries": enquiries, "team": team, "today": today, "SERVICES": SERVICES,
        "now_hour": datetime.now(IST).hour,
        "active": "dashboard",
    })
