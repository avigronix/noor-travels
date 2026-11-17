# Noor Travels Services – Visa & Ticket Software

Staff software + public website for Noor Travels Services, Kasia, Kushinagar.
FastAPI · Jinja2 · HTMX · Tailwind CSS v4 · PostgreSQL 16 · Python 3.10

## What's in this folder

| Path | What |
|---|---|
| `docs/SRS.md` | Final requirements (v1.0) – every feature has an FR id |
| `docs/SCREENS.md` | Each screen → route → template → design file, with build status |
| `docs/design/*.dc.html` | UI design of every screen (laptop + phone + website) |
| `CLAUDE.md` | Project rules Claude follows in VS Code (stack, structure, pagination, HTMX) |
| `prompts/` | Build prompts in order – start with `00-how-to-use.md` |
| `main.py`, `common/`, `routes/`, `templates/`, `static/`, `migrations/` | Working starter code |

Everything in the SRS MVP + Phase 2 is built: website (5 pages + privacy/terms, sitemap), staff login with lockout,
dashboard, registration / edit form with service sections, file detail with one-tap stages, retests, courier and
passport tracking, visas & sponsors (Hijri date), appointments, ticket desk, centres & referrers, payments with GST
receipts, reversals and refunds, the free WhatsApp "To send" queue with reminders, enquiries, reports with Excel/PDF,
settings, users & roles, and the customer portal. See `docs/SCREENS.md` for every route.

## Run locally

```bash
# 1. Python (3.10+; server is 3.10)
python3 -m venv .venv                # or, to match the server exactly: uv venv --python 3.10 .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Database (PostgreSQL 16)
sudo -u postgres psql -c "CREATE ROLE noor LOGIN PASSWORD 'noor_password';"
sudo -u postgres psql -c "CREATE DATABASE noor_travels OWNER noor;"
cp .env.example .env                 # then set SECRET_KEY and DATABASE_URL
python migrate.py
python migrate.py create-owner

# 3. Front-end files (one time)
#    htmx 2.x  -> static/js/htmx.min.js   (https://unpkg.com/htmx.org@2/dist/htmx.min.js)
#    Tailwind v4 standalone CLI -> ./tailwindcss   (https://github.com/tailwindlabs/tailwindcss/releases)
#    macOS: chmod +x tailwindcss && codesign --force -s - tailwindcss   (else macOS kills it, exit 137)
./tailwindcss -i static/css/input.css -o static/css/app.css --watch    # keep running in a 2nd terminal

# 4. Start
uvicorn main:app --reload --port 8030

# 5. Reminders (day-before 6 pm, morning-of, balance due, travel day) – cron runs this every 30 min on the server
python jobs.py
```

Self-check of the Hijri date conversion: `python -m common.hijri`.

Open http://127.0.0.1:8030 (website) and http://127.0.0.1:8030/login (staff).

## Deploy

See `deploy/DEPLOY.md` (systemd service, nginx, Cloudflare, nightly backup, cron). Port 8030.

## Changing things with Claude in VS Code

`CLAUDE.md` has the rules (flat folders, raw SQL in `common/db.py`, pagination, HTMX, mobile first).
The original build prompts are kept in `prompts/` for reference.

## Key decisions

- **Pagination everywhere:** all list endpoints use `common.db.paginate()` (max 100 rows per page);
  previews/dropdowns use a fixed LIMIT. A DB connection pool with a wait timeout keeps the app responsive under load.
- **One place for SQL:** `common/db.py`. Raw SQL with parameters, no ORM.
- **No page reloads:** `hx-boost` on `<body>` + HTMX partials for search, filters, pagination and forms.
- **WhatsApp is free:** `wa.me` click-to-chat links + a "To send" queue. No paid API, SMS or OTP.
- **Fast on slow internet:** gzip on every response, self-hosted fonts, 11 KB WebP logo, minified htmx (17 KB gz),
  static files fingerprinted (`?v=`) and cached for a year; pages are ~6 KB gzipped. Measured on Slow 3G:
  first load ≈ 3.4 s, moving between pages ≈ 1.1 s.
- **Responsive:** sidebar on laptop, bottom tab bar + drawer on phones/tablets; tested at 360, 390, 768 and 1366 px.
- **Production:** system Python 3.10, systemd, nginx → `127.0.0.1:8030`, HTTPS via Cloudflare (`deploy/DEPLOY.md`).
