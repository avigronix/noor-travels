"""Every router is listed here once; main.py includes them all.
Add a new module (e.g. routes/tickets.py with `router = APIRouter(...)`) and append it below."""
from routes import appointments, auth, customers, dashboard, enquiries, files, messages, payments, partners, portal, reports, settings, search, tickets, uploads, visas, website

all_routers = [
    website.router,
    auth.router,
    dashboard.router,
    customers.router,
    files.router,
    visas.router,
    appointments.router,
    tickets.router,
    partners.router,
    messages.router,
    enquiries.router,
    reports.router,
    settings.router,
    portal.router,
    payments.router,
    payments.public,
    uploads.router,
    search.router,
]
