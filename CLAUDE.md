# CLAUDE.md – Noor Travels Services (NTS)

You are building the staff software + public website for **Noor Travels Services**, Kasia, Kushinagar:
Gulf visa processing (Work / Umrah / Family / Tourist) and individual air tickets.

**Read before any task:** `docs/SRS.md` (what to build, FR ids), `docs/SCREENS.md` (screen → route → design file),
and the matching design file in `docs/design/`. The design files are the visual spec: copy their layout,
spacing, wording and states into Jinja + Tailwind. They are design markup, not code to import.

## Stack (fixed – do not add frameworks)

- **FastAPI** (sync `def` routes) + **Jinja2** templates + **HTMX 2** + **Tailwind CSS v4** (standalone CLI) + **PostgreSQL 16**
- DB driver: **psycopg 3** with `psycopg_pool`. Raw SQL. **No ORM, no Alembic, no SQLAlchemy.**
- Auth: Starlette `SessionMiddleware` cookie + `bcrypt`. No JWT.
- **Python 3.10** on the server (system Python, nothing else may be installed). Do not use 3.11+ features
  (`tomllib`, `ExceptionGroup`, `typing.Self`, `StrEnum`, `except*`). `X | None` and `match` are fine.
- No Node / npm / React / Alpine. Tiny vanilla JS only in `static/js/app.js`, event-delegated.

## Folder structure (keep it this flat)

```
main.py              app, middleware, error handlers, router include
migrate.py           apply migrations/*.sql, create-owner
jobs.py              cron reminders (idempotent)
deploy/              systemd, nginx, backup, crontab, DEPLOY.md
.env / .env.example
common/
  db.py              ALL Postgres access: fetch_one, fetch_all, execute, insert, update, transaction, paginate, stream, audit
  stages.py          service catalogue (fields, process order), current stage, attempts / re-appointments
  money.py           file / ticket / customer totals, receipt numbers, amount in words
  messages.py        queue_message() for the WhatsApp To-send list (templates, consent, quiet hours)
  hijri.py, uploads.py  Hijri dates; the two allowed photo uploads
  auth.py            login check, lockout, require_user, require_role
  web.py             render() (full page vs HTMX partial), pager dependency, filters (inr, dmy), wa_link
routes/              one file per area – ALL endpoints live here; register each in routes/__init__.py
templates/
  layouts/           base.html (head + hx-boost body), app.html (navbar/main/footer), site.html (website)
  partials/          navbar, footer, pagination, search_results, shared bits
  <area>/            page.html + _rows.html / _form.html partials (leading _ = fragment)
static/css|js|img
migrations/          001_schema.sql, 002_..., never edit an applied file
docs/                SRS.md, SCREENS.md, design/*.dc.html
prompts/             step-by-step build prompts
```

Allowed new files: `routes/<area>.py`, `templates/<area>/...`, `migrations/00N_*.sql`.
If an area grows real business logic shared by 2+ routes (e.g. money totals, message building), put it in
**one** module `common/<topic>.py` (e.g. `common/money.py`, `common/messages.py`). No `services/`, `repositories/`,
`schemas/`, `utils/` folders, no class hierarchies, no one-line wrapper functions.

## Rules that are not negotiable

1. **Every list endpoint is paginated** with `common.db.paginate()` + `Depends(pager)` (max 100 per page) and
   renders `partials/pagination.html`. Dropdown/preview queries use a hard `LIMIT` (5–50). Never `fetch_all`
   on something users can grow without a LIMIT.
2. **All SQL goes through `common/db.py`.** Routes never create connections. Values always as params
   (`%s` / `%(name)s`) – never f-string user input into SQL. f-strings only for fixed WHERE fragments you build.
   A literal `%` in SQL with params must be written `%%`.
3. Multi-step writes (file + services + charges) use `with transaction() as conn:` and pass `conn=conn`.
4. Every create / edit / payment / stage change writes `audit(...)` (SRS 7).
5. Payments, stage attempts, messages and audit rows are **never updated or deleted** – add a new row
   (reversal / new attempt). History is the product.
6. **HTMX pattern:** `<body hx-boost="true">` already gives no-reload navigation. For in-page updates use
   `hx-get/hx-post` + `hx-target`, and in the route `render(request, "x/page.html", ctx, partial="x/_rows.html")`.
   Forms that save return the updated fragment (or `HX-Redirect` header). Filters/search: `hx-push-url="true"`.
7. **Mobile first** (SRS 7 Devices): one-column forms on phones, tap targets ≥ 44px (`h-11`), tables → cards
   under `md:`, list filters behind the `<details data-filters>` Filter button with count + removable chips
   (copy `templates/customers/`). Test at 360px width.
8. **WhatsApp = free `wa.me` link only** (`wa_link(mobile, text)`), with the `messages` table as the "To send"
   queue (Mark sent / Skip). No WhatsApp API, no SMS, no OTP, no paid service of any kind.
9. **Uploads:** only `visa_photo` and `profile_photo` (jpg/png/webp, ≤ 5 MB), saved under `UPLOAD_DIR`,
   served through an auth-checked route – never from `static/`.
10. Roles: `owner`, `staff`, `accountant`. Staff never see margin / cost price; accountant is read-only.
    Check with `Depends(require_role(...))` in the route, and hide in templates with `user.role`.
11. Money is `numeric(12,2)` in rupees; show with the `inr` filter. Dates with `dmy`. Passport numbers are
    stored upper-case without spaces.
12. Website content: no invented numbers, reviews, photos or testimonials (FR-15.4).

## UI system (keep every new screen consistent)

- Shell: `partials/navbar.html` – laptop (≥1024px) fixed left sidebar (collapsible to an icon rail via the ☰ button / `[` key; width = `--sb-w`, offset with `sb-offset`, never `lg:pl-64`), phone/tablet top bar + bottom tab bar
  (Home, Files, + New sheet, WhatsApp, More drawer). Set `active` in the route context to highlight the menu.
- Page header: `{{ page_head(title, subtitle, icon_name) }}` from `partials/_fields.html`.
- Surfaces: `card` (and `card card-sm`), table head `thead`, empty lists `empty-state`, status `pill(text, tone)`,
  icons `{{ icon('name', 'icon-sm') }}` from the cached sprite `static/img/icons.svg`.
- Forms: macros `input / select / btn` in `partials/_fields.html`; sticky save bar on phones must sit above the tab bar
  (`bottom-[calc(3.5rem+env(safe-area-inset-bottom))] lg:static`).
- Grids that change columns need a base `grid-cols-1` (otherwise phones overflow sideways).
- Assets: always `{{ asset('path') }}` (adds ?v=hash → cached a year). Logo in UI: `img/logo-sm.webp`
  (`logo-md.webp` for big places and print; the original PNGs are not in git). Fonts are self-hosted in `static/fonts/`.
- After changing classes in templates, rebuild `static/css/app.css` (it is committed).

## Code style

- Readable over clever. A route function may be 40–80 lines of straightforward SQL + render – that is fine.
  Do not split it into many tiny helpers. Extract a function only when the same code is needed in 2+ places.
- Validate input with FastAPI `Form(...)` / `Query(...)` constraints; show errors inline in the form partial.
- Keep comments short and only where the *why* is not obvious; reference FR ids (e.g. `# FR-4.4`).
- Brand: `brand-red #E8202A`, `brand-blue #0070B8`, `brand-orange #F7A21B`, `brand-brown #5A3818`,
  `brand-navy #0A2E57`; fonts Inter (body) + Space Grotesk (`font-display`).

## Commands

```
source .venv/bin/activate
uvicorn main:app --reload --port 8030
./tailwindcss -i static/css/input.css -o static/css/app.css --watch
python migrate.py                # apply new migrations
python migrate.py create-owner
python jobs.py                   # reminders into the To-send list (cron every 30 min)
python -m common.hijri           # Hijri → Gregorian self-check
python -m py_compile main.py jobs.py common/*.py routes/*.py   # quick check
```

## Deployment notes (shared VPS)

- System Python 3.10 + venv; run `python main.py` under systemd; nginx proxies to `127.0.0.1:$PORT`.
- Port **8020 is taken** – use 8030 (or another free port) via `.env`.
- nginx has no SSL; HTTPS comes from Cloudflare. Set `APP_ENV=prod` so the session cookie is secure.
- Daily `pg_dump` + uploads backup, keep 30 days (SRS 7).

## Definition of done for any task

- Works on laptop and at 360px; no full page reload on navigation; list is paginated.
- `python -m py_compile` passes; page opens without template errors; SQL tried against the real schema.
- FR ids covered are listed in your summary.
