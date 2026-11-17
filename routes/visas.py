"""Visas & sponsors (SRS 5.5). Design: docs/design/Visas.dc.html.

A visa is entered once, attached to one active file at a time (FR-5.2) and can be released and given to
another candidate; every assignment stays in visa_assignments (FR-5.3).
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from common.auth import require_role, require_user
from common.db import audit, fetch_all, fetch_one, insert, paginate, transaction
from common.hijri import to_gregorian
from common.messages import queue_message
from common.stages import next_step, to_date
from common.uploads import save_photo
from common.web import form_data, pager, render

router = APIRouter(prefix="/app/visas", tags=["visas"])
WRITE = require_role("owner", "staff")
COUNTRIES = ["Saudi Arabia", "Kuwait", "UAE", "Qatar", "Oman", "Bahrain"]


def _free_files():
    return fetch_all("""
        SELECT f.id, f.serial_no, c.name FROM files f JOIN customers c ON c.id = f.customer_id
        WHERE f.status = 'active' AND f.visa_id IS NULL AND f.visa_type = 'work'
        ORDER BY f.serial_no DESC LIMIT 50
    """)


def _list_ctx(q="", status="", attach=None, pg=None):
    where, params = ["true"], {}
    if q.strip():
        where.append("(v.visa_no ILIKE %(q)s OR s.sponsor_no ILIKE %(q)s OR s.name ILIKE %(q)s OR v.profession ILIKE %(q)s)")
        params["q"] = f"%{q.strip()}%"
    if status == "free":
        where.append("f.id IS NULL")
    elif status == "attached":
        where.append("f.id IS NOT NULL")
    result = paginate(f"""
        SELECT v.*, s.name AS sponsor_name, s.sponsor_no, f.id AS file_id, f.serial_no, c.name AS customer_name,
               EXISTS (SELECT 1 FROM visa_assignments a WHERE a.visa_id = v.id AND a.released_on IS NOT NULL) AS was_released
        FROM visas v LEFT JOIN sponsors s ON s.id = v.sponsor_id
        LEFT JOIN files f ON f.visa_id = v.id AND f.status = 'active'
        LEFT JOIN customers c ON c.id = f.customer_id
        WHERE {' AND '.join(where)}
        ORDER BY (f.id IS NULL) DESC, v.created_at DESC
    """, params, **(pg or {}))
    stats = fetch_one("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM files f WHERE f.visa_id = v.id AND f.status = 'active')) AS attached
        FROM visas v
    """)
    target = fetch_one("SELECT f.id, f.serial_no, c.name FROM files f JOIN customers c ON c.id = f.customer_id"
                       " WHERE f.id = %s", (attach,)) if attach else None
    return {"result": result, "q": q, "status": status, "stats": stats, "target": target, "attach": attach or "",
            "free_files": _free_files(), "countries": COUNTRIES, "errors": {}, "v": {}, "active": "visas",
            "filters_on": 1 if status else 0}


@router.get("")
def visa_list(request: Request, q: str = "", status: str = "", attach: int | None = None,
              pg: dict = Depends(pager), user=Depends(require_user)):
    return render(request, "visas/list.html", _list_ctx(q, status, attach, pg), partial="visas/_rows.html")


@router.get("/hijri")
def hijri_preview(request: Request, visa_date_hijri: str = "", user=Depends(require_user)):
    g = to_gregorian(visa_date_hijri)
    return render(request, "visas/_hijri.html", {"g": g, "typed": bool(visa_date_hijri.strip())})


@router.post("")
def create_visa(request: Request, form=Depends(form_data), user=Depends(WRITE)):
    v = {k: (form.get(k) or "").strip() for k in ("visa_no", "sponsor_no", "sponsor_type", "sponsor_name",
                                                 "visa_date_hijri", "visa_date", "country", "profession", "file_id")}
    v["visa_no"] = v["visa_no"].upper().replace(" ", "")
    errors = {}
    if len(v["visa_no"]) < 5: errors["visa_no"] = "Enter the visa number."
    if not v["sponsor_no"]: errors["sponsor_no"] = "Enter the sponsor / company ID."
    if not v["profession"]: errors["profession"] = "Enter the profession on the visa."
    if v["visa_date_hijri"] and not to_gregorian(v["visa_date_hijri"]) and not to_date(v["visa_date"]):
        errors["visa_date_hijri"] = "Write the Hijri date as DD-MM-YYYY, e.g. 14-05-1448."
    if fetch_one("SELECT 1 FROM visas WHERE visa_no = %s", (v["visa_no"],)):
        errors["visa_no"] = "This visa number is already saved."
    photo, err = save_photo(form.get("photo"), "visa") if not errors else (None, None)
    if err:
        errors["photo"] = err
    if errors:
        ctx = _list_ctx(attach=int(v["file_id"]) if v["file_id"].isdigit() else None)
        return render(request, "visas/list.html", {**ctx, "errors": errors, "v": v})

    with transaction() as conn:
        sp = conn.execute("SELECT * FROM sponsors WHERE sponsor_no = %s LIMIT 1", (v["sponsor_no"],)).fetchone()
        if not sp:      # FR-5.2 one sponsor, many visas
            name = v["sponsor_name"] or ("Individual" if v["sponsor_type"] != "Company" else "Company")
            sp = insert("sponsors", {"sponsor_no": v["sponsor_no"], "name": name,
                                     "country": v["country"] or "Saudi Arabia"}, conn=conn)
        visa = insert("visas", {
            "sponsor_id": sp["id"], "country": v["country"] or "Saudi Arabia", "visa_no": v["visa_no"],
            "visa_date_hijri": v["visa_date_hijri"] or None,
            "visa_date": to_date(v["visa_date"]) or to_gregorian(v["visa_date_hijri"]),
            "profession": v["profession"], "visa_type": "work", "photo_path": photo,
        }, conn=conn)
        audit(user["id"], "create", "visa", visa["id"], {"visa_no": v["visa_no"]}, conn=conn)
        if v["file_id"].isdigit():
            _attach(conn, visa, int(v["file_id"]), "Attached when the visa was entered", user)
    return RedirectResponse(f"/app/files/{v['file_id']}" if v["file_id"].isdigit() else f"/app/visas/{visa['id']}",
                            status_code=303)


def _attach(conn, visa, file_id, reason, user):
    f = conn.execute("SELECT * FROM files WHERE id = %s AND status = 'active'", (file_id,)).fetchone()
    if not f:
        raise HTTPException(400, "File is not active")
    taken = conn.execute("SELECT id, serial_no FROM files WHERE visa_id = %s AND status = 'active' AND id <> %s",
                         (visa["id"], file_id)).fetchone()
    if taken:
        raise HTTPException(409, f"Visa is attached to #{taken['serial_no']} – release it first")
    if f["visa_id"] and f["visa_id"] != visa["id"]:      # the file had another visa – release that one
        conn.execute("UPDATE visa_assignments SET released_on = now(), reason = 'Replaced by another visa'"
                     " WHERE file_id = %s AND visa_id = %s AND released_on IS NULL", (file_id, f["visa_id"]))
    conn.execute("UPDATE files SET visa_id = %s, updated_at = now() WHERE id = %s", (visa["id"], file_id))
    insert("visa_assignments", {"visa_id": visa["id"], "file_id": file_id, "reason": reason, "created_by": user["id"]}, conn=conn)
    audit(user["id"], "visa_attached", "file", file_id, {"visa_no": visa["visa_no"], "reason": reason}, conn=conn)
    return queue_message(conn, "visa_attached", f["customer_id"], file_id=file_id, visa_no=visa["visa_no"],
                         next_step=next_step(conn, file_id))


@router.get("/{visa_id}")
def visa_detail(visa_id: int, request: Request, user=Depends(require_user)):
    visa = fetch_one("""
        SELECT v.*, s.name AS sponsor_name, s.sponsor_no, f.id AS file_id, f.serial_no, c.name AS customer_name
        FROM visas v LEFT JOIN sponsors s ON s.id = v.sponsor_id
        LEFT JOIN files f ON f.visa_id = v.id AND f.status = 'active' LEFT JOIN customers c ON c.id = f.customer_id
        WHERE v.id = %s
    """, (visa_id,))
    if not visa:
        raise HTTPException(404)
    history = fetch_all("""
        SELECT a.*, f.serial_no, c.name, u.name AS by_name FROM visa_assignments a
        JOIN files f ON f.id = a.file_id JOIN customers c ON c.id = f.customer_id LEFT JOIN users u ON u.id = a.created_by
        WHERE a.visa_id = %s ORDER BY a.assigned_on DESC LIMIT 50
    """, (visa_id,))
    others = fetch_all("SELECT id, visa_no FROM visas WHERE sponsor_id = %s AND id <> %s ORDER BY created_at DESC LIMIT 20",
                       (visa["sponsor_id"], visa_id))
    return render(request, "visas/detail.html", {"visa": visa, "history": history, "others": others,
                                                 "free_files": _free_files(), "active": "visas"})


@router.post("/{visa_id}/attach")
def attach_visa(visa_id: int, file_id: int = Form(...), reason: str = Form("", max_length=200), user=Depends(WRITE)):
    visa = fetch_one("SELECT * FROM visas WHERE id = %s", (visa_id,))
    if not visa:
        raise HTTPException(404)
    with transaction() as conn:
        msg = _attach(conn, visa, file_id, reason.strip() or "Attached", user)
    return RedirectResponse(f"/app/files/{file_id}" + (f"?msg={msg['id']}" if msg else ""), status_code=303)


@router.post("/{visa_id}/release")
def release_visa(visa_id: int, reason: str = Form(..., min_length=3, max_length=200), user=Depends(WRITE)):
    """Candidate dropped out → the visa becomes free for someone else; history stays (FR-5.3)."""
    with transaction() as conn:
        f = conn.execute("SELECT id FROM files WHERE visa_id = %s AND status = 'active'", (visa_id,)).fetchone()
        if f:
            conn.execute("UPDATE files SET visa_id = NULL, updated_at = now() WHERE id = %s", (f["id"],))
            audit(user["id"], "visa_released", "file", f["id"], {"visa_id": visa_id, "reason": reason}, conn=conn)
        conn.execute("UPDATE visa_assignments SET released_on = now(), reason = %s WHERE visa_id = %s AND released_on IS NULL",
                     (reason.strip(), visa_id))
        audit(user["id"], "release", "visa", visa_id, {"reason": reason.strip()}, conn=conn)
    return RedirectResponse(f"/app/visas/{visa_id}", status_code=303)
