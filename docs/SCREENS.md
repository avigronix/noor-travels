# Screens → routes → design files

Design files are in `docs/design/` (open in a browser for layout reference; logo is linked to `static/img/logo.png`).
Live clickable design: https://claude.ai/artifact/L2VEBjGNM6sVPhr5ACZGm6

Status: ✅ built (all screens – prompts 01–12 done on 16 Nov 2026)

## Staff app (login required)

| # | Screen | Route | Template folder | Design file | SRS | Status |
|---|---|---|---|---|---|---|
| — | Shared top navigation + inline search | (all /app pages) | `partials/navbar.html`, `partials/search_results.html` | Nav | 5.17 | ✅ |
| 1 | Staff login | `/login` | `auth/` | Main | 3, 7 | ✅ |
| 2 | Dashboard | `/app` | `dashboard/` | Dashboard, MobileDashboard | 5.10 | ✅ |
| 3 | All files (filter button on phone) | `/app/files` | `files/` | Candidates, MobileFiles | 5.4, 7 | ✅ |
| 4 | New registration – visa type & services | `/app/files/new` | `files/_form.html` | NewFile | 5.1–5.3 | ✅ |
| 7 | File detail – stages, passport & courier | `/app/files/{id}` | `files/` | FileDetail, MobileFile | 5.4–5.6 | ✅ |
| 7b | Edit profile & services (same form) | `/app/files/{id}/edit` | `files/_form.html` | EditFile | FR-2.9 | ✅ |
| 7c | Customer profile hub | `/app/customers/{id}` | `customers/` | Customer | 5.16 | ✅ |
| — | Customers list | `/app/customers` | `customers/` | (pattern for all lists) | 5.16 | ✅ |
| 5 | Ticket desk | `/app/tickets` | `tickets/` | Tickets, MobileTickets | 5.7 | ✅ |
| 6 | Visas & sponsors | `/app/visas` | `visas/` | Visas | 5.5 | ✅ |
| 8 | Payments, receipt & refund | `/app/payments` | `payments/` | Payments | 6 | ✅ |
| 9 | Appointments | `/app/appointments` | `appointments/` | Appointments | 5.4, 5.10 | ✅ |
| 10 | Messages – free WhatsApp click-to-send | `/app/messages` | `messages/` | Messages | 5.11 | ✅ |
| — | Website enquiries | `/app/enquiries` | `enquiries/` | (Dashboard panel) | 5.15 | ✅ |
| 11 | Centres & agencies, referrers | `/app/partners` | `partners/` | Partners | 5.8, 5.9 | ✅ |
| 12 | Reports | `/app/reports` | `reports/` | Reports | 5.13 | ✅ |
| 13 | Settings (users, rates, templates, messages) | `/app/settings` | `settings/` | Settings | 5.9, FR-11.4 | ✅ |

## Customer portal (Phase 2)

| # | Screen | Route | Design file | SRS | Status |
|---|---|---|---|---|---|
| 14 | Customer login (mobile + DOB) | `/my` | CandidateLogin | 5.12 | ✅ |
| 15 | Customer – my file | `/my/file` | CandidateStatus | 5.12 | ✅ |

## Public website (no login)

| Page | Route | Template | Design file | Status |
|---|---|---|---|---|
| Home | `/` | `website/home.html` | SiteHome, SitePhone | ✅ |
| Services | `/services` | `website/services.html` | SiteServices | ✅ |
| Umrah | `/umrah` | `website/umrah.html` | SiteUmrah | ✅ |
| About | `/about` | `website/about.html` | SiteAbout | ✅ |
| Contact | `/contact` | `website/contact.html` | SiteContact | ✅ |
| Header / footer / enquiry | shared | `layouts/site.html`, `website/_enquiry_form.html` | SiteHeader, SiteFooter, SiteEnquiry | ✅ |
| Enquiry submit | `POST /enquiry` | `website/_enquiry_thanks.html` | SiteEnquiry | ✅ |
| Privacy / Terms | `/privacy`, `/terms` | `website/privacy.html`, `terms.html` | (SiteFooter links) | ✅ |
| SEO | `/sitemap.xml`, `/robots.txt` | `routes/website.py` | – | ✅ |

## Extra routes

| What | Route | SRS |
|---|---|---|
| GST receipt (staff / public signed link) | `/app/payments/{id}/receipt`, `/r/{token}` | FR-14.6 |
| Refund on cancellation | `/app/payments/refund?file_id=` | FR-14.7 |
| Referrer – files by month | `/app/partners/referrers/{id}` | FR-8.2 |
| Visa detail, release & reassign | `/app/visas/{id}` | FR-5.3 |
| Reports + Excel (CSV) / PDF (print) | `/app/reports/{name}`, `/app/reports/{name}.csv` | FR-13.5 |
| Message wording (EN / Hindi Roman / Devanagari) | `/app/messages/templates/{trigger}` | FR-11.4 |
| Reminder job (cron, every 30 min) | `python jobs.py` | 5.11 |
