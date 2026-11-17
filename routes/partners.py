"""Centres & agencies (FR-9.1) and referrers (FR-8.x). Design: docs/design/Partners.dc.html.

Parties are everyone NTS pays on a candidate's behalf; owner-only edits. Referrers are contacts only –
no commission is tracked anywhere (FR-8.3).
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from common.auth import require_role, require_user
from common.db import audit, fetch_all, fetch_one, insert, paginate, update
from common.web import num, pager, render

router = APIRouter(prefix="/app/partners", tags=["partners"])
KINDS = [("medical", "Medical centre"), ("skill_test", "Skill test centre"), ("agency", "Mumbai agency"),
         ("supplier", "Ticket supplier"), ("courier", "Courier"), ("umrah_agent", "Umrah agent")]


@router.get("")
def partner_list(request: Request, tab: str = "parties", kind: str = "", q: str = "", pg: dict = Depends(pager),
                 user=Depends(require_user)):
    if tab == "referrers":
        return _referrers(request, q, pg)
    where, params = ["true"], {}
    if kind:
        where.append("p.kind = %(kind)s"); params["kind"] = kind
    if q.strip():
        where.append("(p.name ILIKE %(q)s OR p.city ILIKE %(q)s)"); params["q"] = f"%{q.strip()}%"
    result = paginate(f"""
        SELECT p.*,
          coalesce((SELECT sum(x.amount) FROM payments x WHERE x.party_id = p.id AND x.direction = 'out'
                    AND x.paid_on >= date_trunc('month', current_date)), 0) AS paid_month,
          (SELECT count(DISTINCT coalesce(x.file_id, -x.booking_id)) FROM payments x WHERE x.party_id = p.id
                    AND x.paid_on >= date_trunc('month', current_date)) AS files_month
        FROM parties p WHERE {' AND '.join(where)}
        ORDER BY p.is_active DESC, p.kind, p.name
    """, params, **pg)
    stats = fetch_one("""
        SELECT coalesce(sum(amount) FILTER (WHERE direction = 'out' AND kind = 'expense'
                        AND paid_on >= date_trunc('month', current_date)), 0) AS paid_month,
               (SELECT count(*) FROM parties WHERE is_active) AS partners
        FROM payments
    """)
    # FR-13.3 dues payable: pass-through costs charged to customers but not yet paid out, on active files
    stats["payable"] = fetch_one("""
        SELECT coalesce(sum(greatest(c.cost - coalesce(p.paid, 0), 0)), 0) AS n FROM
          (SELECT file_id, sum(amount) AS cost FROM charges WHERE kind IN ('cost', 'retest') AND file_id IS NOT NULL GROUP BY 1) c
          JOIN files f ON f.id = c.file_id AND f.status = 'active'
          LEFT JOIN (SELECT file_id, sum(amount) AS paid FROM payments WHERE direction = 'out' AND kind = 'expense' GROUP BY 1) p
            ON p.file_id = c.file_id
    """)["n"]
    return render(request, "partners/list.html", {
        "tab": "parties", "result": result, "kind": kind, "q": q, "kinds": KINDS, "stats": stats,
        "active": "partners", "filters_on": 1 if kind else 0,
    }, partial="partners/_rows.html")


def _referrers(request, q, pg):
    where, params = ["true"], {}
    if q.strip():
        where.append("(r.name ILIKE %(q)s OR r.mobile ILIKE %(q)s OR r.place ILIKE %(q)s)"); params["q"] = f"%{q.strip()}%"
    result = paginate(f"""
        SELECT r.*,
          (SELECT count(*) FROM files f JOIN customers c ON c.id = f.customer_id WHERE c.referrer_id = r.id) AS files_total,
          (SELECT count(*) FROM files f JOIN customers c ON c.id = f.customer_id WHERE c.referrer_id = r.id
              AND f.created_at >= date_trunc('month', current_date)) AS files_month,
          (SELECT count(*) FROM files f JOIN customers c ON c.id = f.customer_id WHERE c.referrer_id = r.id
              AND f.status = 'active') AS files_active
        FROM referrers r WHERE {' AND '.join(where)}
        ORDER BY files_month DESC, files_total DESC, r.name
    """, params, **pg)
    return render(request, "partners/list.html", {"tab": "referrers", "result": result, "q": q, "kind": "",
                                                   "kinds": KINDS, "active": "partners", "filters_on": 0},
                  partial="partners/_ref_rows.html")


@router.get("/referrers/{rid}")
def referrer_detail(rid: int, request: Request, pg: dict = Depends(pager), user=Depends(require_user)):
    """Files each referrer brought, by month (FR-8.2)."""
    r = fetch_one("SELECT * FROM referrers WHERE id = %s", (rid,))
    if not r:
        raise HTTPException(404)
    months = fetch_all("""
        SELECT date_trunc('month', f.created_at)::date AS month, count(*) AS n
        FROM files f JOIN customers c ON c.id = f.customer_id WHERE c.referrer_id = %s
        GROUP BY 1 ORDER BY 1 DESC LIMIT 24
    """, (rid,))
    files = paginate("""
        SELECT f.id, f.serial_no, f.visa_type, f.status, f.current_stage, f.created_at, c.id AS customer_id, c.name
        FROM files f JOIN customers c ON c.id = f.customer_id WHERE c.referrer_id = %(rid)s
        ORDER BY f.created_at DESC
    """, {"rid": rid}, **pg)
    return render(request, "partners/referrer.html", {"r": r, "months": months, "result": files, "active": "partners"},
                  partial="partners/_ref_files.html")


@router.get("/new")
def new_party(request: Request, kind: str = "medical", user=Depends(require_role("owner"))):
    return render(request, "partners/form.html", {"p": {"kind": kind}, "kinds": KINDS, "errors": {}, "active": "partners"})


@router.get("/{pid}/edit")
def edit_party(pid: int, request: Request, user=Depends(require_role("owner"))):
    p = fetch_one("SELECT * FROM parties WHERE id = %s", (pid,))
    if not p:
        raise HTTPException(404)
    return render(request, "partners/form.html", {"p": p, "kinds": KINDS, "errors": {}, "active": "partners"})


@router.post("/save")
def save_party(request: Request, pid: str = Form(""), kind: str = Form(...), name: str = Form("", max_length=120),
               city: str = Form("", max_length=80), contact: str = Form("", max_length=120), mobile: str = Form("", max_length=20),
               default_rate: str = Form(""), notes: str = Form("", max_length=500), is_active: str = Form(""),
               user=Depends(require_role("owner"))):
    data = {"kind": kind, "name": name.strip(), "city": city.strip() or None, "contact": contact.strip() or None,
            "mobile": mobile.strip() or None, "default_rate": num(default_rate, None), "notes": notes.strip() or None,
            "is_active": bool(is_active) or not pid}
    if kind not in dict(KINDS) or not data["name"]:
        return render(request, "partners/form.html", {"p": {**data, "id": pid}, "kinds": KINDS, "active": "partners",
                                                      "errors": {"name": "Enter the name."}})
    if pid.isdigit():
        row = update("parties", int(pid), data)
        audit(user["id"], "update", "party", row["id"], {"name": row["name"]})
    else:
        row = insert("parties", data)
        audit(user["id"], "create", "party", row["id"], {"name": row["name"]})
    return RedirectResponse(f"/app/partners?kind={kind}", status_code=303)


@router.post("/referrers/save")
def save_referrer(rid: str = Form(""), name: str = Form(..., min_length=2, max_length=120), mobile: str = Form("", max_length=20),
                  place: str = Form("", max_length=80), notes: str = Form("", max_length=500),
                  user=Depends(require_role("owner", "staff"))):
    data = {"name": name.strip(), "mobile": mobile.strip() or None, "place": place.strip() or None, "notes": notes.strip() or None}
    if rid.isdigit():
        row = update("referrers", int(rid), data)
        audit(user["id"], "update", "referrer", row["id"], {})
    else:
        row = insert("referrers", data)
        audit(user["id"], "create", "referrer", row["id"], {})
    return RedirectResponse(f"/app/partners/referrers/{row['id']}", status_code=303)
