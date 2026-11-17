"""WhatsApp 'To send' queue (SRS 5.11). Free wa.me links only – nothing is sent by the server.

queue_message() fills a template and adds a row to `messages`; staff open it with wa_link() and press Send,
then Mark sent / Skip. Every trigger in the SRS table calls this from the save that causes it.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from common.web import BUSINESS, inr

IST = ZoneInfo("Asia/Kolkata")
QUIET_FROM, QUIET_TO = 21, 7          # nothing due 9 pm – 7 am except travel-day (FR-11.7)


class _Blank(dict):
    def __missing__(self, key):
        return ""


def now_ist():
    return datetime.now(IST)


def outside_quiet_hours(due, trigger):
    """Move a due time out of 9 pm – 7 am to the next 7 am (travel-day checklists are exempt)."""
    if trigger == "travel_day":
        return due
    local = due.astimezone(IST)
    if local.hour >= QUIET_FROM:
        local = (local + timedelta(days=1)).replace(hour=QUIET_TO, minute=0, second=0, microsecond=0)
    elif local.hour < QUIET_TO:
        local = local.replace(hour=QUIET_TO, minute=0, second=0, microsecond=0)
    return local


def portal_link(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = 'portal_url'").fetchone()
    base = (row and row["value"]) or BUSINESS["site_url"]
    return f"{base}/my" if base else "/my"


def queue_message(conn, trigger, customer_id, file_id=None, booking_id=None, due_at=None,
                  dedupe_key=None, **values):
    """Prepare one message in the To-send list. Returns the new row, or None when the trigger is off,
    the customer has not agreed to WhatsApp updates, or the same dedupe_key was already queued."""
    tpl = conn.execute("SELECT * FROM message_templates WHERE trigger = %s AND is_active", (trigger,)).fetchone()
    cust = conn.execute("SELECT name, mobile, whatsapp_consent FROM customers WHERE id = %s", (customer_id,)).fetchone()
    if not tpl or not cust or not cust["whatsapp_consent"]:
        return None
    lang = conn.execute("SELECT value FROM settings WHERE key = 'message_lang'").fetchone()
    lang = lang["value"] if lang else "hi"
    body = {"hi": tpl["body_hi"], "hi_dev": tpl["body_hi_dev"]}.get(lang) or tpl["body_en"]

    vals = _Blank(name=cust["name"].split(" ")[0], portal_link=portal_link(conn))
    for k, v in values.items():
        vals[k] = inr(v) if k in ("amount", "balance") and not isinstance(v, str) else ("" if v is None else v)
    text = body.format_map(vals).strip()

    due = outside_quiet_hours(due_at or now_ist(), trigger)
    return conn.execute("""
        INSERT INTO messages (customer_id, file_id, booking_id, trigger, body, due_at, dedupe_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING
        RETURNING *
    """, (customer_id, file_id, booking_id, trigger, text, due, dedupe_key)).fetchone()
