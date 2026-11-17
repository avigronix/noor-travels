"""Money totals used on file detail, ticket, customer profile, payments and portal (FR-14.5).

to_collect = charges incl. GST · received = money in (reversals are negative rows) · refunded = refunds out
balance    = to_collect − received + refunded · expenses = money out to centres/agencies/suppliers
margin     = charges excl. GST − expenses   (owner only – templates hide it from staff)
"""
from common.db import fetch_one

_TOTALS = """
    SELECT
      coalesce((SELECT sum(amount * (1 + gst_rate / 100)) FROM charges WHERE {col} = %(id)s), 0)::numeric(12,2) AS to_collect,
      coalesce((SELECT sum(amount) FROM charges WHERE {col} = %(id)s), 0)::numeric(12,2)                       AS charges_net,
      coalesce((SELECT sum(amount) FROM payments WHERE {col} = %(id)s AND direction = 'in'), 0)::numeric(12,2) AS received,
      coalesce((SELECT sum(amount) FROM payments WHERE {col} = %(id)s AND direction = 'out'
                AND kind = 'refund'), 0)::numeric(12,2)                                                        AS refunded,
      coalesce((SELECT sum(amount) FROM payments WHERE {col} = %(id)s AND direction = 'out'
                AND kind <> 'refund'), 0)::numeric(12,2)                                                       AS expenses,
      -- pass-through costs NTS must still pay (centre fees, agency, ticket cost) – margin is never shown before them
      (coalesce((SELECT sum(amount) FROM charges WHERE {col} = %(id)s AND kind IN ('cost', 'retest')), 0)
       + coalesce((SELECT sum(cost_price) FROM ticket_bookings WHERE {tcol} AND status <> 'cancelled'), 0))::numeric(12,2) AS costs_due
"""


def _finish(t):
    t["balance"] = t["to_collect"] - t["received"] + t["refunded"]
    # margin = what was charged (excl. GST) minus the costs – the paid ones, or the expected ones if not yet paid
    t["margin"] = t["charges_net"] - max(t["expenses"], t.get("costs_due") or 0)
    return t


def file_totals(file_id, conn=None):
    q = _TOTALS.format(col="file_id", tcol="file_id = %(id)s")
    return _finish(conn.execute(q, {"id": file_id}).fetchone() if conn else fetch_one(q, {"id": file_id}))


def booking_totals(booking_id, conn=None):
    q = _TOTALS.format(col="booking_id", tcol="id = %(id)s")
    return _finish(conn.execute(q, {"id": booking_id}).fetchone() if conn else fetch_one(q, {"id": booking_id}))


def customer_totals(customer_id):
    """Across every file and ticket of one person (FR-17.3). Charges linked to both a file and a
    booking are counted once."""
    return _finish(fetch_one("""
        WITH mine AS (
          SELECT ch.* FROM charges ch
          LEFT JOIN files f ON f.id = ch.file_id
          LEFT JOIN ticket_bookings t ON t.id = ch.booking_id
          WHERE f.customer_id = %(id)s OR t.customer_id = %(id)s
        ), pay AS (
          SELECT p.* FROM payments p
          LEFT JOIN files f ON f.id = p.file_id
          LEFT JOIN ticket_bookings t ON t.id = p.booking_id
          WHERE f.customer_id = %(id)s OR t.customer_id = %(id)s
        )
        SELECT
          coalesce((SELECT sum(amount * (1 + gst_rate / 100)) FROM mine), 0)::numeric(12,2) AS to_collect,
          coalesce((SELECT sum(amount) FROM mine), 0)::numeric(12,2)                       AS charges_net,
          coalesce((SELECT sum(amount) FROM pay WHERE direction = 'in'), 0)::numeric(12,2) AS received,
          coalesce((SELECT sum(amount) FROM pay WHERE direction = 'out' AND kind = 'refund'), 0)::numeric(12,2) AS refunded,
          coalesce((SELECT sum(amount) FROM pay WHERE direction = 'out' AND kind <> 'refund'), 0)::numeric(12,2) AS expenses
    """, {"id": customer_id}))


# SQL fragment for list pages: balance per file without N+1 queries
FILE_BALANCE_SQL = """
    (coalesce((SELECT sum(ch.amount * (1 + ch.gst_rate / 100)) FROM charges ch WHERE ch.file_id = f.id), 0)
     - coalesce((SELECT sum(p.amount) FROM payments p WHERE p.file_id = f.id AND p.direction = 'in'), 0)
     + coalesce((SELECT sum(p.amount) FROM payments p WHERE p.file_id = f.id AND p.direction = 'out' AND p.kind = 'refund'), 0)
    )::numeric(12,2)
"""
BOOKING_BALANCE_SQL = FILE_BALANCE_SQL.replace("ch.file_id = f.id", "ch.booking_id = t.id").replace("p.file_id = f.id", "p.booking_id = t.id")


def next_receipt_no(conn):
    """NTS/2026-27/0001 – one series per Indian financial year (Apr–Mar), FOR UPDATE so two staff
    never get the same number (FR-14.6)."""
    from datetime import date

    today = date.today()
    fy = today.year if today.month >= 4 else today.year - 1
    label = f"{fy}-{str(fy + 1)[-2:]}"
    rows = {r["key"]: r["value"] for r in conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('next_receipt_no', 'receipt_prefix', 'receipt_fy') FOR UPDATE").fetchall()}
    n = int(rows.get("next_receipt_no", "1"))
    if rows.get("receipt_fy") != label:          # new financial year starts again at 1
        n = 1
        conn.execute("INSERT INTO settings (key, value) VALUES ('receipt_fy', %s)"
                     " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (label,))
    conn.execute("UPDATE settings SET value = %s WHERE key = 'next_receipt_no'", (str(n + 1),))
    return f"{rows.get('receipt_prefix', 'NTS')}/{label}/{n:04d}"


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _below_1000(n):
    words = []
    if n >= 100:
        words += [_ONES[n // 100], "hundred"]
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n or not words:
        words.append(_ONES[n])
    return " ".join(words)


def rupees_in_words(amount):
    """20000 -> 'Rupees twenty thousand only' (Indian lakh / crore grouping) for receipts."""
    n = int(round(float(amount)))
    if n == 0:
        return "Rupees zero only"
    parts = []
    for size, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        if n >= size:
            parts.append(f"{_below_1000(n // size)} {name}")
            n %= size
    if n:
        parts.append(_below_1000(n))
    return "Rupees " + " ".join(parts) + " only"
