"""Reports (SRS 5.13, FR-13.1–13.5). Design: docs/design/Reports.dc.html.

Each report is one SQL in REPORTS below (readable in one place). The page shows it paginated; Excel export is
a streamed CSV (server-side cursor, never all rows in memory); PDF = the print view (browser Print → Save as PDF).
Margin columns and the margin report are owner-only (FR-13.2, CLAUDE rule 10).
"""
import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse

from common.auth import require_user
from common.db import paginate, stream
from common.money import FILE_BALANCE_SQL
from common.stages import to_date
from common.web import pager, render

router = APIRouter(prefix="/app/reports", tags=["reports"])

_MONTHS = "generate_series(date_trunc('month', %(start)s::date), date_trunc('month', %(end)s::date), interval '1 month')::date"

# key: (title, sql, columns[(key, label, kind)], owner-only column keys, owner-only report)
REPORTS = {
    "summary": ("Monthly summary", f"""
        SELECT m.month,
          (SELECT count(*) FROM files f WHERE date_trunc('month', f.created_at)::date = m.month AND (%(vt)s = '' OR f.visa_type = %(vt)s)) AS opened,
          (SELECT count(*) FROM files f WHERE date_trunc('month', f.visa_done_on)::date = m.month AND (%(vt)s = '' OR f.visa_type = %(vt)s)) AS visa_done,
          (SELECT count(*) FROM audit_log a JOIN files f ON f.id = a.entity_id WHERE a.entity = 'file' AND a.action = 'departed'
              AND date_trunc('month', a.created_at)::date = m.month AND (%(vt)s = '' OR f.visa_type = %(vt)s)) AS departed,
          (SELECT count(*) FROM audit_log a JOIN files f ON f.id = a.entity_id WHERE a.entity = 'file' AND a.action = 'cancel'
              AND date_trunc('month', a.created_at)::date = m.month AND (%(vt)s = '' OR f.visa_type = %(vt)s)) AS cancelled,
          (SELECT coalesce(sum(amount), 0) FROM payments p WHERE p.direction = 'in' AND date_trunc('month', p.paid_on)::date = m.month) AS received,
          (SELECT coalesce(sum(amount), 0) FROM payments p WHERE p.direction = 'out' AND date_trunc('month', p.paid_on)::date = m.month) AS paid_out,
          (SELECT coalesce(sum(amount), 0) FROM payments p WHERE date_trunc('month', p.paid_on)::date = m.month
              AND p.direction = 'in') - (SELECT coalesce(sum(amount), 0) FROM payments p WHERE p.direction = 'out'
              AND date_trunc('month', p.paid_on)::date = m.month) AS margin
        FROM (SELECT {_MONTHS} AS month) m ORDER BY m.month DESC
    """, [("month", "Month", "month"), ("opened", "Opened", "n"), ("visa_done", "Visa done", "n"), ("departed", "Departed", "n"),
          ("cancelled", "Cancelled", "n"), ("received", "Received (₹)", "inr"), ("paid_out", "Paid out (₹)", "inr"),
          ("margin", "Margin (₹) · owner", "inr")], {"margin"}, False),

    "tickets": ("Ticket sales", """
        SELECT date_trunc('month', t.created_at)::date AS month, coalesce(p.name, 'Airline / other') AS supplier,
               count(*) AS tickets, sum(t.selling_price) AS sales, sum(t.cost_price) AS cost, sum(t.selling_price - t.cost_price) AS margin
        FROM ticket_bookings t LEFT JOIN parties p ON p.id = t.supplier_id
        WHERE t.status <> 'cancelled' AND t.created_at::date BETWEEN %(start)s AND %(end)s
        GROUP BY 1, 2 ORDER BY 1 DESC, 2
    """, [("month", "Month", "month"), ("supplier", "Supplier", "text"), ("tickets", "Tickets", "n"), ("sales", "Sales (₹)", "inr"),
          ("cost", "Cost (₹) · owner", "inr"), ("margin", "Margin (₹) · owner", "inr")], {"cost", "margin"}, False),

    "balance": ("Balance due by customer", f"""
        SELECT c.id AS customer_id, c.name, c.mobile, string_agg('#' || f.serial_no, ', ') AS files, sum(b.balance) AS balance
        FROM (SELECT f.id, f.customer_id, {FILE_BALANCE_SQL} AS balance FROM files f WHERE f.status <> 'cancelled') b
        JOIN files f ON f.id = b.id JOIN customers c ON c.id = b.customer_id
        WHERE b.balance > 0 GROUP BY c.id, c.name, c.mobile ORDER BY balance DESC
    """, [("name", "Customer", "customer"), ("mobile", "Mobile", "text"), ("files", "Files", "text"), ("balance", "Balance (₹)", "inr")], set(), False),

    "payable": ("Payable to centres & agencies", """
        SELECT f.id AS file_id, f.serial_no, c.name, ch.cost, coalesce(pd.paid, 0) AS paid, ch.cost - coalesce(pd.paid, 0) AS payable
        FROM (SELECT file_id, sum(amount) AS cost FROM charges WHERE kind IN ('cost', 'retest') AND file_id IS NOT NULL GROUP BY 1) ch
        JOIN files f ON f.id = ch.file_id JOIN customers c ON c.id = f.customer_id
        LEFT JOIN (SELECT file_id, sum(amount) AS paid FROM payments WHERE direction = 'out' AND kind = 'expense' GROUP BY 1) pd ON pd.file_id = f.id
        WHERE f.status = 'active' AND ch.cost - coalesce(pd.paid, 0) > 0 ORDER BY payable DESC
    """, [("serial_no", "File", "file"), ("name", "Customer", "text"), ("cost", "Costs charged (₹)", "inr"), ("paid", "Paid out (₹)", "inr"),
          ("payable", "Still payable (₹)", "inr")], set(), False),

    "margin": ("File-wise margin", """
        SELECT f.id AS file_id, f.serial_no, c.name, f.visa_type, f.status,
               coalesce((SELECT sum(amount) FROM charges WHERE file_id = f.id), 0) AS charged,
               coalesce((SELECT sum(amount) FROM payments WHERE file_id = f.id AND direction = 'in'), 0) AS received,
               coalesce((SELECT sum(amount) FROM payments WHERE file_id = f.id AND direction = 'out' AND kind = 'expense'), 0) AS expenses,
               coalesce((SELECT sum(amount) FROM charges WHERE file_id = f.id), 0)
                 - coalesce((SELECT sum(amount) FROM payments WHERE file_id = f.id AND direction = 'out' AND kind = 'expense'), 0) AS margin
        FROM files f JOIN customers c ON c.id = f.customer_id
        WHERE f.created_at::date BETWEEN %(start)s AND %(end)s AND (%(vt)s = '' OR f.visa_type = %(vt)s)
        ORDER BY f.serial_no DESC
    """, [("serial_no", "File", "file"), ("name", "Customer", "text"), ("visa_type", "Visa", "title"), ("status", "Status", "title"),
          ("charged", "Charged excl. GST (₹)", "inr"), ("received", "Received (₹)", "inr"), ("expenses", "Expenses (₹)", "inr"),
          ("margin", "Margin (₹)", "inr")], set(), True),

    "gst": ("GST register", """
        SELECT p.paid_on, p.receipt_no, c.name, f.serial_no, p.file_id,
               round(p.amount / (1 + %(gst)s::numeric / 100), 2) AS taxable,
               round((p.amount - p.amount / (1 + %(gst)s::numeric / 100)) / 2, 2) AS cgst,
               p.amount - round(p.amount / (1 + %(gst)s::numeric / 100), 2) - round((p.amount - p.amount / (1 + %(gst)s::numeric / 100)) / 2, 2) AS sgst,
               p.amount AS total, p.mode
        FROM payments p LEFT JOIN customers c ON c.id = p.customer_id LEFT JOIN files f ON f.id = p.file_id
        WHERE p.direction = 'in' AND (p.for_service_charge OR p.kind = 'reversal') AND p.paid_on BETWEEN %(start)s AND %(end)s
        ORDER BY p.paid_on, p.id
    """, [("paid_on", "Date", "date"), ("receipt_no", "Receipt", "text"), ("name", "Customer", "text"), ("serial_no", "File", "file"),
          ("taxable", "Taxable (₹)", "money"), ("cgst", "CGST (₹)", "money"), ("sgst", "SGST (₹)", "money"), ("total", "Total (₹)", "money"),
          ("mode", "Mode", "upper")], set(), False),

    "closed": ("Departed & cancelled", """
        SELECT a.created_at::date AS on_date, a.action, f.id AS file_id, f.serial_no, c.name, f.visa_type, f.cancel_reason
        FROM audit_log a JOIN files f ON f.id = a.entity_id JOIN customers c ON c.id = f.customer_id
        WHERE a.entity = 'file' AND a.action IN ('departed', 'cancel') AND a.created_at::date BETWEEN %(start)s AND %(end)s
          AND (%(vt)s = '' OR f.visa_type = %(vt)s)
        ORDER BY a.created_at DESC
    """, [("on_date", "Date", "date"), ("action", "What", "title"), ("serial_no", "File", "file"), ("name", "Customer", "text"),
          ("visa_type", "Visa", "title"), ("cancel_reason", "Reason", "text")], set(), False),

    "retests": ("Retest log", """
        SELECT a.on_date, s.service, a.attempt_no, a.result, f.id AS file_id, f.serial_no, c.name,
               (SELECT ch.amount FROM charges ch WHERE ch.file_service_id = s.id AND ch.kind = 'retest' ORDER BY ch.id LIMIT 1) AS charge
        FROM stage_attempts a JOIN file_services s ON s.id = a.file_service_id JOIN files f ON f.id = s.file_id JOIN customers c ON c.id = f.customer_id
        WHERE a.status = 'failed' AND a.on_date BETWEEN %(start)s AND %(end)s ORDER BY a.on_date DESC
    """, [("on_date", "Date", "date"), ("service", "Stage", "stage"), ("attempt_no", "Attempt", "n"), ("result", "Result", "text"),
          ("serial_no", "File", "file"), ("name", "Customer", "text"), ("charge", "Retest charge (₹)", "inr")], set(), False),

    "passports": ("Passports outside the office", """
        SELECT f.id AS file_id, f.serial_no, c.name, c.mobile, f.passport_location,
               (SELECT max(coalesce(m.received_on, m.sent_on)) FROM courier_movements m WHERE m.file_id = f.id AND m.item = 'passport') AS since
        FROM files f JOIN customers c ON c.id = f.customer_id
        WHERE f.status = 'active' AND f.passport_location <> 'Office' ORDER BY since NULLS LAST
    """, [("serial_no", "File", "file"), ("name", "Customer", "text"), ("mobile", "Mobile", "text"), ("passport_location", "Where", "text"),
          ("since", "Since", "date")], set(), False),

    "messages": ("Messages sent", f"""
        SELECT m.month,
          (SELECT count(*) FROM messages x WHERE date_trunc('month', x.created_at)::date = m.month) AS prepared,
          (SELECT count(*) FROM messages x WHERE x.status = 'sent' AND date_trunc('month', x.created_at)::date = m.month) AS sent,
          (SELECT count(*) FROM messages x WHERE x.status = 'skipped' AND date_trunc('month', x.created_at)::date = m.month) AS skipped,
          (SELECT round(100.0 * count(*) FILTER (WHERE x.status = 'sent') / nullif(count(*) FILTER (WHERE x.status <> 'to_send'), 0))
             FROM messages x WHERE date_trunc('month', x.created_at)::date = m.month) AS rate
        FROM (SELECT {_MONTHS} AS month) m ORDER BY m.month DESC
    """, [("month", "Month", "month"), ("prepared", "Prepared", "n"), ("sent", "Sent", "n"), ("skipped", "Skipped", "n"),
          ("rate", "Sent rate %", "n")], set(), False),
}


def _period(period, start, end):
    today = date.today()
    if period == "month":
        return today.replace(day=1), today
    if period == "fy":
        y = today.year if today.month >= 4 else today.year - 1
        return date(y, 4, 1), today
    if period == "custom" and to_date(start) and to_date(end):
        return to_date(start), to_date(end)
    m, y = today.month - 5, today.year                 # default: last 6 months
    if m <= 0:
        m, y = m + 12, y - 1
    return date(y, m, 1), today


def _spec(name, user):
    spec = REPORTS.get(name)
    if not spec or (spec[4] and user["role"] != "owner"):
        raise HTTPException(404)
    title, sql, cols, owner_cols, _ = spec
    if user["role"] != "owner":
        cols = [c for c in cols if c[0] not in owner_cols]
    return title, sql, cols


def _params(period, start, end, visa_type):
    from common.stages import setting
    d1, d2 = _period(period, start, end)
    return d1, d2, {"start": d1, "end": d2, "vt": visa_type if visa_type in ("work", "umrah", "family", "tourist") else "",
                    "gst": float(setting("gst_rate", "18") or 0)}


@router.get("")
def reports_home(user=Depends(require_user)):
    return RedirectResponse("/app/reports/summary", status_code=303)


@router.get("/{name}")
def report(request: Request, name: str, period: str = "6m", start: str = "", end: str = "", visa_type: str = "",
           print: str = "", pg: dict = Depends(pager), user=Depends(require_user)):
    if name.endswith(".csv"):
        return export(name[:-4], period, start, end, visa_type, user)
    title, sql, cols = _spec(name, user)
    d1, d2, params = _params(period, start, end, visa_type)
    if print:
        pg = {"page": 1, "per_page": 100}
    result = paginate(sql, params, **pg)
    chart = None
    if name == "summary":
        rows = list(reversed(result["rows"]))
        top = max([max(r["opened"], r["departed"]) for r in rows] + [1])
        chart = {"rows": rows, "top": top}
    tabs = [(k, v[0]) for k, v in REPORTS.items() if not v[4] or user["role"] == "owner"]
    return render(request, "reports/page.html", {
        "name": name, "title": title, "cols": cols, "result": result, "tabs": tabs, "period": period, "start": start,
        "end": end, "visa_type": visa_type, "d1": d1, "d2": d2, "chart": chart, "print_view": bool(print),
        "active": "reports", "qs": request.url.query,
    }, partial="reports/_table.html")


def export(name, period, start, end, visa_type, user):
    """FR-13.5: CSV opens in Excel. Streamed in chunks through a server-side cursor."""
    title, sql, cols = _spec(name, user)
    _d1, _d2, params = _params(period, start, end, visa_type)

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf)
        buf.write("﻿")                              # BOM so Excel reads ₹ / Hindi names correctly
        w.writerow([c[1] for c in cols])
        for i, r in enumerate(stream(sql, params)):
            w.writerow(["" if r[c[0]] is None else r[c[0]] for c in cols])
            if i % 500 == 0:
                yield buf.getvalue(); buf.seek(0); buf.truncate()
        yield buf.getvalue()
    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}-{date.today()}.csv"'})
