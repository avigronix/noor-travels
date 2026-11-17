"""Visa-file services and stages – the one place that knows their fields, order and rules.

Used by routes/files.py (form, detail, mark done), appointments, dashboard, portal and messages.
Field tuples: (key, label, type or list of options, placeholder). SRS 5.3 + docs/design/NewFile.dc.html.
"""
from datetime import date, datetime, time

from common.db import fetch_all, fetch_one, insert

CITIES = ["", "Lucknow", "Patna", "Delhi", "Mumbai", "Ranchi", "Other"]
AGENCIES = ["", "Alif, Mumbai", "Siddiqui Travel (SQT), Mumbai", "Other"]

# Process order (FR-2.4, FR-4.1). sort_order in file_services follows this list.
SERVICES = {
    "umrah": {
        "label": "Umrah package", "note": "Booked through a Saudi-licensed Umrah agent", "color": "bg-teal-500",
        "fields": [("agent", "Umrah agent", "text", "Agent name"), ("pkg", "Package", "text", "e.g. 15 days economy"),
                   ("hotel", "Hotels (Makkah / Madinah)", "text", ""), ("visa_no", "Umrah visa no.", "text", ""),
                   ("dep", "Departure date", "date", ""), ("ret", "Return date", "date", ""),
                   ("cost", "Package cost (₹)", "number", "")],
        "required": ["agent", "dep"],
    },
    "family": {
        "label": "Family visit details", "note": "Sponsor issues the MOFA reference slip in Saudi Arabia", "color": "bg-teal-500",
        "fields": [("sponsor", "Sponsor name", "text", ""), ("iqama", "Sponsor Iqama no.", "text", ""),
                   ("rel", "Relationship", ["", "Wife", "Son", "Daughter", "Father", "Mother", "Other"], ""),
                   ("slip", "MOFA reference slip no.", "text", "")],
        "required": ["sponsor", "slip"],
    },
    "medical": {
        "label": "GAMCA medical", "note": "Wafid (GAMCA) approved centre · ₹9,500 per attempt", "color": "bg-teal-600",
        "fields": [("centre", "Medical centre", ["", "Approved centre", "Al Arouba", "Shivvir", "Other"], ""),
                   ("city", "City", CITIES, ""), ("slip", "Wafid slip no.", "text", ""), ("date", "Date", "date", ""),
                   ("time", "Time", "time", ""), ("result", "Result", ["Pending", "Fit", "Unfit"], ""),
                   ("cost", "Cost (₹)", "number", "")],
        "required": ["centre", "date", "result"],
        "appointment": True, "fail": "Unfit", "pass": "Fit",
    },
    "skill_test": {
        "label": "Skill test", "note": "Skill Verification Program test in India", "color": "bg-orange-500",
        "fields": [("centre", "Test centre", "text", "e.g. Hala"), ("city", "City", CITIES, ""),
                   ("trade", "Trade", "text", ""), ("date", "Date", "date", ""), ("time", "Time", "time", ""),
                   ("result", "Result", ["Pending", "Pass", "Fail"], ""), ("cost", "Cost (₹)", "number", "")],
        "required": ["centre", "date", "result"],
        "appointment": True, "fail": "Fail", "pass": "Pass",
    },
    "mofa": {
        "label": "MOFA", "note": "Mail for MOFA and VFS reference", "color": "bg-brand-blue",
        "fields": [("mail", "Mail for MOFA date", "date", ""), ("app", "MOFA application no.", "text", ""),
                   ("vfsref", "VFS reference no.", "text", "e.g. 8028XXXXX")],
        "required": ["mail", "vfsref"],
    },
    "vfs": {
        "label": "VFS appointment", "note": "Tasheer / VFS biometric", "color": "bg-brand-blue",
        "fields": [("city", "City", CITIES, ""), ("date", "Date", "date", ""), ("time", "Time", "time", ""),
                   ("ref", "Appointment no.", "text", "")],
        "required": ["city", "date"],
        "appointment": True, "needs": ["mofa"],
    },
    "stamping": {
        "label": "Visa stamping", "note": "Through Mumbai agency · passport goes by courier", "color": "bg-brand-navy",
        "fields": [("agency", "Mumbai agency", AGENCIES, ""), ("sent", "Documents sent on", "date", ""),
                   ("courier", "Courier & tracking no.", "text", ""), ("done", "Visa done date", "date", ""),
                   ("cost", "Charge (₹)", "number", "")],
        "required": ["agency", "done"],
        "needs": ["medical", "skill_test", "mofa", "vfs"],
    },
    "contract": {
        "label": "Employment contract", "note": "Agreement through the Mumbai agency", "color": "bg-brand-navy",
        "fields": [("agency", "Agency", AGENCIES, ""), ("date", "Date", "date", ""), ("cost", "Charge (₹)", "number", "")],
        "required": ["agency", "date"],
    },
    "emigration": {
        "label": "Emigration", "note": "eMigrate clearance – needed for ECR passports only", "color": "bg-green-600",
        "fields": [("app", "eMigrate application no.", "text", ""), ("date", "Clearance date", "date", "")],
        "required": ["app", "date"],
        "needs": ["stamping"],
    },
    "ticket": {
        "label": "Ticket", "note": "Booked through Akbar Travels or the airline", "color": "bg-brand-red",
        "fields": [("from", "From", "text", "LKO"), ("to", "To", "text", "RUH"), ("date", "Travel date", "date", ""),
                   ("airline", "Airline", "text", ""), ("supplier", "Booked through", ["Akbar Travels", "Airline website", "Other"], ""),
                   ("pnr", "PNR", "text", ""), ("cost", "Cost price (₹)", "number", ""), ("sell", "Selling price (₹)", "number", "")],
        "required": ["date", "pnr"],
    },
}
ORDER = list(SERVICES)
CHECKBOX_SERVICES = ["medical", "skill_test", "mofa", "vfs", "ticket"]     # shown as checkboxes (FR-2.2)
ALL_PROCESS = ["medical", "skill_test", "mofa", "vfs", "stamping", "contract", "emigration", "ticket"]
VISA_TYPES = [("work", "Work", "Job visa – full process"), ("umrah", "Umrah", "Pilgrimage package"),
              ("family", "Family", "Visit family in KSA"), ("tourist", "Tourist", "Tourist visit")]
FALLBACK_DEFAULTS = {"work": ALL_PROCESS, "umrah": ["umrah", "ticket"],
                     "family": ["family", "mofa", "vfs", "ticket"], "tourist": ["vfs", "ticket"]}

# Labels for files.current_stage values that are not services
STAGE_LABELS = {k: v["label"] for k, v in SERVICES.items()}
STAGE_LABELS.update({"registration": "Registration", "ready": "Ready to fly", "departed": "Departed",
                     "cancelled": "Cancelled", "umrah": "Umrah", "family": "Family visit"})

# What to carry, used in appointment messages and the customer portal (FR-12.2)
DOCUMENTS = {
    "medical": "original passport, 2 photos, Wafid slip",
    "skill_test": "original passport, test slip, trade certificate if any",
    "vfs": "original passport, medical report, VFS appointment letter",
    "mofa": "passport copy, photos",
    "umrah": "passport, white-background photos, vaccination card",
    "family": "passport, 2 white-background photos, MOFA slip, sponsor Iqama copy",
}


def stage_label(key):
    return STAGE_LABELS.get(key or "", (key or "—").replace("_", " ").title())


def default_services(visa_type, country="Saudi Arabia"):
    """Services pre-ticked for a visa type, from template_stages (FR-2.3, FR-9.2)."""
    rows = fetch_all(
        "SELECT service FROM template_stages WHERE country = %s AND visa_type = %s AND default_on"
        " ORDER BY sort_order LIMIT 20", (country, visa_type))
    return [r["service"] for r in rows] or FALLBACK_DEFAULTS.get(visa_type, [])


def default_costs(visa_type, country="Saudi Arabia"):
    rows = fetch_all(
        "SELECT service, default_charge FROM template_stages WHERE country = %s AND visa_type = %s"
        " AND default_charge IS NOT NULL LIMIT 20", (country, visa_type))
    return {r["service"]: r["default_charge"] for r in rows}


def missing_fields(service, data):
    return [k for k in SERVICES[service].get("required", []) if str((data or {}).get(k, "")).strip() in ("", "Pending")]


def clean_section(service, form):
    """Pick this service's fields out of a submitted form (names like 'medical.date')."""
    data = {}
    for key, _label, kind, _ph in SERVICES[service]["fields"]:
        if f"{service}.{key}" not in form:      # field not on this form – keep the saved value
            continue
        v = (form.get(f"{service}.{key}") or "").strip()
        if kind == "number":
            v = "".join(ch for ch in v if ch.isdigit() or ch == ".")
        if key in ("pnr", "from", "to"):
            v = v.upper()
        data[key] = v
    return data


def to_date(v):
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def to_time(v):
    if isinstance(v, time):
        return v
    try:
        return datetime.strptime(v, "%H:%M").time() if v else None
    except ValueError:
        return None


def sync_attempt(conn, fs, data, user_id):
    """Keep stage_attempts in step with an appointment service's date (FR-4.7).

    No scheduled attempt yet and a date is set → attempt 1 (or next no.) is scheduled.
    A scheduled attempt whose date/time/city changed → it is cancelled ('re-appointment') and a NEW
    attempt row is added; the old row keeps its original date. Returns 'booked', 'changed' or None.
    """
    if not SERVICES[fs["service"]].get("appointment"):
        return None
    on_date, at_time = to_date(data.get("date")), to_time(data.get("time"))
    if not on_date:
        return None
    cur = conn.execute(
        "SELECT * FROM stage_attempts WHERE file_service_id = %s ORDER BY attempt_no DESC LIMIT 1",
        (fs["id"],)).fetchone()
    place = data.get("city") or None
    if cur and cur["status"] == "scheduled":
        if (cur["on_date"], cur["at_time"], cur["city"]) == (on_date, at_time, place):
            return None
        conn.execute("UPDATE stage_attempts SET status = 'cancelled', remarks = coalesce(remarks || ' · ', '')"
                     " || 'Re-appointment' WHERE id = %s AND status = 'scheduled'", (cur["id"],))
        kind = "changed"
    elif cur and cur["status"] == "done":
        return None
    else:
        kind = "booked"
    insert("stage_attempts", {
        "file_service_id": fs["id"], "attempt_no": (cur["attempt_no"] + 1) if cur else 1,
        "status": "scheduled", "city": place, "on_date": on_date, "at_time": at_time,
        "ref_no": data.get("slip") or data.get("ref") or None, "created_by": user_id,
    }, conn=conn)
    conn.execute("UPDATE file_services SET status = 'scheduled', updated_at = now() WHERE id = %s AND status IN ('pending', 'failed')", (fs["id"],))
    return kind


def refresh_file_stage(conn, file_id):
    """Recompute files.current_stage (denormalised for fast lists) after any stage change."""
    f = conn.execute("SELECT status FROM files WHERE id = %s", (file_id,)).fetchone()
    if f["status"] in ("departed", "cancelled"):
        stage = f["status"]
    else:
        row = conn.execute(
            "SELECT service FROM file_services WHERE file_id = %s AND status NOT IN ('done', 'cancelled')"
            " ORDER BY sort_order LIMIT 1", (file_id,)).fetchone()
        stage = row["service"] if row else "ready"
    conn.execute("UPDATE files SET current_stage = %s, updated_at = now() WHERE id = %s", (stage, file_id))
    return stage


def next_step(conn, file_id):
    row = conn.execute(
        "SELECT service FROM file_services WHERE file_id = %s AND status NOT IN ('done', 'cancelled')"
        " ORDER BY sort_order LIMIT 1", (file_id,)).fetchone()
    return stage_label(row["service"]) if row else "ticket and travel"


def setting(key, default=""):
    row = fetch_one("SELECT value FROM settings WHERE key = %s", (key,))
    return row["value"] if row else default


def next_serial(conn, key):
    """Take the next number from settings (file serial, ticket no., receipt no.) inside the caller's
    transaction. FOR UPDATE stops two staff getting the same number."""
    row = conn.execute("SELECT value FROM settings WHERE key = %s FOR UPDATE", (key,)).fetchone()
    n = int(row["value"])
    conn.execute("UPDATE settings SET value = %s WHERE key = %s", (str(n + 1), key))
    return n
