"""Website enquiries (SRS 5.15): call, WhatsApp, follow-up, or convert to a registration / ticket (FR-16.2)."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request

from common.auth import require_role, require_user
from common.db import audit, fetch_one, paginate
from common.web import pager, render

router = APIRouter(prefix="/app/enquiries", tags=["enquiries"])
STATUSES = [("new", "New"), ("follow_up", "Follow-up"), ("converted", "Converted"), ("closed", "Closed")]


@router.get("")
def enquiry_list(request: Request, status: str = "open", q: str = "", pg: dict = Depends(pager), user=Depends(require_user)):
    where, params = ["true"], {}
    if status == "open":
        where.append("e.status IN ('new', 'follow_up')")
    elif status in dict(STATUSES):
        where.append("e.status = %(st)s"); params["st"] = status
    if q.strip():
        where.append("(e.name ILIKE %(q)s OR e.mobile ILIKE %(q)s)"); params["q"] = f"%{q.strip()}%"
    result = paginate(f"""
        SELECT e.*, c.name AS customer_name FROM enquiries e LEFT JOIN customers c ON c.id = e.customer_id
        WHERE {' AND '.join(where)} ORDER BY (e.status = 'new') DESC, e.created_at DESC
    """, params, **pg)
    counts = fetch_one("SELECT count(*) FILTER (WHERE status = 'new') AS new, count(*) FILTER (WHERE status = 'follow_up') AS follow_up FROM enquiries")
    return render(request, "enquiries/list.html", {"result": result, "status": status, "q": q, "statuses": STATUSES,
                                                    "counts": counts, "active": "enquiries"}, partial="enquiries/_rows.html")


@router.post("/{eid}/status")
def set_status(eid: int, request: Request, status: str = Form(...), note: str = Form("", max_length=300),
               user=Depends(require_role("owner", "staff"))):
    if status not in dict(STATUSES):
        raise HTTPException(400)
    e = fetch_one("UPDATE enquiries SET status = %s, note = coalesce(nullif(%s, ''), note), updated_at = now()"
                  " WHERE id = %s RETURNING *", (status, note.strip(), eid))
    if not e:
        raise HTTPException(404)
    audit(user["id"], "update", "enquiry", eid, {"status": status})
    return render(request, "enquiries/_card.html", {"e": {**e, "customer_name": None}, "statuses": STATUSES})
