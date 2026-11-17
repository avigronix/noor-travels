"""Reminder job – run by cron every 30 minutes (deploy/crontab.txt):   python jobs.py

Adds due reminders to the WhatsApp 'To send' list (SRS 5.11). Safe to run any number of times: every
reminder has a dedupe_key, so it is inserted once. Quiet hours 9 pm – 7 am are applied by queue_message
(travel-day checklists are exempt); cancelled and departed files get nothing (FR-11.7).
"""
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from common.db import pool, transaction  # noqa: E402
from common.messages import IST, queue_message  # noqa: E402
from common.money import FILE_BALANCE_SQL  # noqa: E402
from common.stages import DOCUMENTS, SERVICES  # noqa: E402


def appointment_reminders(conn, now):
    """Evening before at 6 pm, and the morning of at 7 am (SRS trigger table)."""
    today, n = now.date(), 0
    rows = conn.execute("""
        SELECT a.id, a.on_date, a.at_time, a.city, s.service, s.data->>'centre' AS centre, f.id AS file_id, f.customer_id
        FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id JOIN files f ON f.id = s.file_id
        WHERE a.status = 'scheduled' AND a.on_date BETWEEN %s AND %s AND f.status = 'active'
        LIMIT 1000
    """, (today, today + timedelta(days=1))).fetchall()
    for r in rows:
        values = dict(stage=SERVICES[r["service"]]["label"], date=r["on_date"].strftime("%d %b"),
                      time=r["at_time"].strftime("%I:%M %p").lstrip("0") if r["at_time"] else "",
                      place=", ".join(x for x in (r["centre"], r["city"]) if x), documents=DOCUMENTS.get(r["service"], ""))
        day_before = datetime.combine(r["on_date"] - timedelta(days=1), datetime.min.time(), tzinfo=IST).replace(hour=18)
        morning = datetime.combine(r["on_date"], datetime.min.time(), tzinfo=IST).replace(hour=7)
        if now < datetime.combine(r["on_date"], datetime.min.time(), tzinfo=IST):
            n += bool(queue_message(conn, "appointment_reminder", r["customer_id"], file_id=r["file_id"],
                                    due_at=day_before, dedupe_key=f"appt:{r['id']}:eve", **values))
        n += bool(queue_message(conn, "appointment_reminder", r["customer_id"], file_id=r["file_id"],
                                due_at=morning, dedupe_key=f"appt:{r['id']}:morn", **values))
    # an appointment that moved or was cancelled: its old reminders are skipped, not deleted
    conn.execute("""
        UPDATE messages m SET status = 'skipped', done_at = now()
        FROM stage_attempts a WHERE m.status = 'to_send' AND m.dedupe_key IN ('appt:' || a.id || ':eve', 'appt:' || a.id || ':morn')
          AND a.status <> 'scheduled'
    """)
    return n


def balance_reminders(conn, now):
    """Every 7 days while a balance is due on an active file (one per file per ISO week)."""
    week = now.strftime("%G-W%V")
    rows = conn.execute(f"""
        SELECT f.id, f.serial_no, f.customer_id, {FILE_BALANCE_SQL} AS balance
        FROM files f WHERE f.status = 'active' AND {FILE_BALANCE_SQL} > 0
        ORDER BY f.id LIMIT 2000
    """).fetchall()
    bank = conn.execute("SELECT name, account_no, ifsc FROM bank_accounts WHERE NOT is_cash AND is_active ORDER BY id LIMIT 1").fetchone()
    upi = conn.execute("SELECT value FROM settings WHERE key = 'upi_id'").fetchone()
    pay_to = (f"{bank['name']} A/c {bank['account_no']} IFSC {bank['ifsc']}" if bank else "our office") + \
             (f" or UPI {upi['value']}" if upi and upi["value"] else "")
    due = now.replace(hour=10, minute=0, second=0, microsecond=0)
    n = 0
    for r in rows:
        n += bool(queue_message(conn, "balance_due", r["customer_id"], file_id=r["id"], due_at=max(due, now),
                                dedupe_key=f"bal:{r['id']}:{week}", file_no=r["serial_no"], balance=r["balance"], bank=pay_to))
    return n


def travel_reminders(conn, now):
    """Travel-day checklist at 6 am on the day of the flight (exempt from quiet hours)."""
    today, n = now.date(), 0
    rows = conn.execute("""
        SELECT t.id, t.customer_id, t.file_id, t.pnr, t.travel_date,
               (SELECT (array_agg(l.from_code ORDER BY l.leg_no))[1] || ' → ' || string_agg(l.to_code, ' → ' ORDER BY l.leg_no) FROM ticket_legs l WHERE l.booking_id = t.id) AS route
        FROM ticket_bookings t LEFT JOIN files f ON f.id = t.file_id
        WHERE t.status = 'booked' AND t.travel_date BETWEEN %s AND %s AND (f.id IS NULL OR f.status <> 'cancelled')
        LIMIT 1000
    """, (today, today + timedelta(days=1))).fetchall()
    for r in rows:
        at = datetime.combine(r["travel_date"], datetime.min.time(), tzinfo=IST).replace(hour=6)
        n += bool(queue_message(conn, "travel_day", r["customer_id"], file_id=r["file_id"], booking_id=r["id"], due_at=at,
                                dedupe_key=f"travel:{r['id']}", route=r["route"] or "", pnr=r["pnr"] or ""))
    return n


def main():
    now = datetime.now(IST)
    pool.open(wait=True)
    try:
        with transaction() as conn:
            counts = {"appointments": appointment_reminders(conn, now), "balance": balance_reminders(conn, now),
                      "travel": travel_reminders(conn, now)}
        print(f"{now:%Y-%m-%d %H:%M} reminders queued: {counts}")
    finally:
        pool.close()


if __name__ == "__main__":
    main()
