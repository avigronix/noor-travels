"""Global search in the navbar (SRS 5.17). Returns the dropdown partial only.

One box: passport, mobile, name, file no., ticket no., PNR, visa no., referrer.
Each group is capped at 5 rows – this is a quick-jump, not a list page.
"""
from fastapi import APIRouter, Depends, Request

from common.auth import require_user
from common.db import fetch_all
from common.web import render

router = APIRouter(prefix="/app/search", tags=["search"])

LIMIT = 5


@router.get("")
def search(request: Request, q: str = "", user=Depends(require_user)):
    q = q.strip()
    if len(q) < 2:
        # FR-18.4: empty box shows recent searches and recently opened customers (kept in the session)
        ids = [i for i in request.session.get("recent_customers", []) if isinstance(i, int)][:5]
        recent = fetch_all("SELECT id, name, mobile, passport_no FROM customers WHERE id = ANY(%s) LIMIT 5", (ids,)) if ids else []
        recent.sort(key=lambda r: ids.index(r["id"]))
        return render(request, "partials/search_results.html", {
            "q": q, "empty": True, "recent_searches": request.session.get("recent_searches", [])[:5], "recent": recent})
    keep = [x for x in request.session.get("recent_searches", []) if x.lower() != q.lower()]
    request.session["recent_searches"] = ([q] + keep)[:5]

    like = {"q": f"%{q}%", "exact": q.upper(), "num": int(q) if q.isdigit() else -1, "lim": LIMIT}

    customers = fetch_all("""
        SELECT c.id, c.name, c.mobile, c.passport_no
        FROM customers c LEFT JOIN referrers r ON r.id = c.referrer_id
        WHERE c.name ILIKE %(q)s OR c.mobile ILIKE %(q)s OR c.passport_no ILIKE %(q)s
              OR r.name ILIKE %(q)s
        ORDER BY (c.passport_no = %(exact)s) DESC, c.name
        LIMIT %(lim)s
    """, like)

    files = fetch_all("""
        SELECT f.id, f.serial_no, f.visa_type, f.status, f.current_stage, c.name
        FROM files f
        JOIN customers c ON c.id = f.customer_id
        LEFT JOIN visas v ON v.id = f.visa_id
        WHERE f.serial_no = %(num)s OR v.visa_no ILIKE %(q)s
              OR c.passport_no ILIKE %(q)s OR c.mobile ILIKE %(q)s OR c.name ILIKE %(q)s
        ORDER BY (f.serial_no = %(num)s) DESC, f.created_at DESC
        LIMIT %(lim)s
    """, like)

    tickets = fetch_all("""
        SELECT t.id, t.ticket_no, t.pnr, t.travel_date, t.airline, c.name
        FROM ticket_bookings t JOIN customers c ON c.id = t.customer_id
        WHERE t.ticket_no = %(num)s OR upper(t.pnr) = %(exact)s
              OR c.passport_no ILIKE %(q)s OR c.mobile ILIKE %(q)s OR c.name ILIKE %(q)s
        ORDER BY (upper(t.pnr) = %(exact)s) DESC, t.created_at DESC
        LIMIT %(lim)s
    """, like)

    return render(request, "partials/search_results.html", {
        "q": q, "customers": customers, "files": files, "tickets": tickets,
        "empty": not (customers or files or tickets),
    })
