# Noor Travels Services – Software Requirements Specification (Final, v1.0)

Nov 16, 2026 · Avigronix Technologies

Live doc: https://claude.ai/artifact/4APoeUGL3LRCnPhtcwQevd

## 1. Introduction

Noor Travels Services will run its Gulf visa and air-ticket business from one web app: each customer's passport, visa, stage dates, courier movements, payments and WhatsApp updates in one place. This document is the final scope for the first working version (MVP).

| Item | Detail |
| --- | --- |
| Client | Noor Travels Services |
| Office | Gorakhpur Road, Near Hitashi Hospital, Kasia, Distt. Kushinagar, U.P. 274402 |
| Phone | 99844 14350 / 88819 51234 |
| Services offered | Air tickets, visa stamping, GAMCA (Wafid) medical, skill test, MOFA, VFS appointment, employment contract, emigration |
| Visa types handled | Work, Umrah, Family, Tourist – Saudi Arabia first, other GCC countries through templates |
| Customers | From Uttar Pradesh, Bihar, Jharkhand and nearby states |
| Volume | About 15 visa files a month, plus individual air-ticket bookings |
| Users | Owner, up to 4 staff, accountant (reports only), customers (own status only) |
| Prepared by | Avigronix Technologies |
| Version | 1.0 final, replaces the draft of 11 Nov 2026 |

**In scope:** customer registration with visa type and chosen services, visa and sponsor records, stage tracking with retests, courier and passport tracking, individual ticket desk, referrers, charges, payments, GST receipts, refunds, free WhatsApp click-to-send reminders, customer status portal, dashboard, reports, and the public website with landing, services, Umrah, about and contact pages.

**Out of scope for the MVP:** direct integration with government or embassy portals (eMigrate, MOFA, Tasheer/VFS, Wafid, Nusuk, Qiwa) and with airline or Akbar Travels booking systems. Staff keep using those portals and record the outcome in the app.

## 2. Current manual process and problems

The office keeps two hand-written registers today. Their columns are the starting point for every screen in the app.

**Visa process register** – one block per candidate:

- Date and serial number (e.g. 4276), name, date of birth, passport number and expiry, full address with PIN.
- Reference person and mobile (e.g. a contact in Saudi Arabia or a local agent).
- Visa number, visa date in Hijri (e.g. 03/04/1447), sponsor/company ID, profession (construction worker, loader/unloader, house driver), ECR or ECNR.
- Medical fit date and centre (Lucknow, Patna), skill test date and centre, MOFA mail date with the VFS reference number, VFS appointment city, date and time.
- Courier movements of passport and documents (e.g. Siwan → Mumbai, Patna → Mumbai, Lucknow → Ranchi).
- Mumbai agency charges (visa stamping ₹5,000, employment contract ₹4,000, ticket ₹22,000), and each payment received with date and mode (ICICI, cash).
- Margin stamps "Visa Done – date" and "PPT-R – date" (passport received back), and the flight with route and PNR.

**Ticket register** – one row per passenger: serial, date, name, passport, date of birth, expiry, address, travel date, from, to (e.g. LKO → HAIL, DMM → LKO), booked through (Akbar Travels), airline code, PNR, fare, and amount received with mode.

Problems with this method:

- No single view of where each file stands, or which files are stuck.
- No record of where a passport is at any moment once it leaves the office.
- Appointment, re-appointment and travel dates are easy to miss or write wrongly.
- Payments are scattered across pages, banks and cash; balance due and margin are not totalled.
- Customers call repeatedly for status; reminders depend on someone remembering to call.
- Passport data sits in an open register with no access control.

## 3. Users and roles

Four roles cover all logins. The owner can change permissions later.

| Role | Count | Can do | Cannot do |
| --- | --- | --- | --- |
| Owner (Admin) | 1 | Everything: users, rates, templates, refunds, margin and all reports | — |
| Staff | Up to 4 | Register customers, update stages, record courier movements and payments, book tickets, send messages, print receipts | Delete files, change rates, approve refunds, see margin |
| Accountant | 1 | View payments, GST register and reports; export | Change any data |
| Customer | One per file | Log in with mobile + date of birth; see own status, next appointment, documents to bring, receipts and balance | See or change anything else |

Reference persons (referrers) do not log in. They are kept as contacts linked to the files they bring (section 5.8).

## 4. Visa types, services and workflows

Every registration is one customer, one visa type and the services that customer is buying. The visa type pre-ticks the usual services; staff can change them. Ticket-only customers use the ticket desk instead (section 5.7).

| Visa type | Services usually ticked | Route the office follows |
| --- | --- | --- |
| Work | All Process: GAMCA medical, skill test, MOFA, VFS appointment, visa stamping, employment contract, emigration (ECR only), ticket | Employer gets the visa authorisation; candidate passes a Wafid (formerly GAMCA) medical at an approved centre, sits the Skill Verification Program test in India for listed trades, then the visa is stamped ([Wafid/SVP](https://nriinformation.com/immigration/work-visa-saudi-arabia)) |
| Umrah | Umrah package/visa, ticket | Indian pilgrims cannot apply on Nusuk themselves; the visa comes through an agent licensed by the Saudi Ministry of Hajj and Umrah, usually bundled with hotel and transport ([Umrah 2026](https://blog.wego.com/2026-umrah-visa/)) |
| Family | MOFA reference slip, VFS appointment, ticket | Sponsor in Saudi issues the MOFA reference slip; the applicant submits the form with two white-background photos ([Saudi Embassy](https://embassies.mofa.gov.sa/sites/india/EN/Services/Visa-Instructions/Pages/Family-Visit-or-Residency-Visa.aspx)); applicants aged 12+ give biometrics at a Tasheer/VFS centre |
| Tourist | VFS appointment, ticket | Applicants not eligible for the e-visa apply through a Tasheer (VFS) centre in India ([Saudi visa guide](https://www.indianholiday.com/international-tours/saudi-arabia/visa.html)) |

Full work-visa flow (10 stages, 2 retest loops); the other visa types use a shorter subset of the same stages:

```
Registration → Visa & sponsor → GAMCA medical ⟲(unfit → retest) → Skill test ⟲(fail → retest)
→ MOFA → VFS appointment → Visa stamping + Employment contract (Mumbai agency)
→ Emigration (ECR only) → Ticket → Departed
```

A failed medical or skill test does not close the file: it opens a new attempt with an extra charge and the file returns to that stage. Medical, MOFA and skill test can run in any order, since their dates depend on centre availability; any stage can be marked done at any time, with a warning only if a related earlier stage is not done (FR-4.3 to FR-4.5). The employment contract (₹4,000 through the Mumbai agency) is recorded with visa stamping.

## 5. Functional requirements

Requirement IDs (FR-x.y) are used for sign-off and testing.

### 5.1 Customer and passport

- FR-1.1 Create a file with an auto serial number, continuing from the register (next number set once in settings).
- FR-1.2 Capture name, father's name, date of birth, mobile (WhatsApp), family contact, full address with district, state and PIN, passport number, issue and expiry dates, place of issue, ECR/ECNR, profession.
- FR-1.3 Capture reference person (name, mobile, where based) by picking from the referrer list or adding a new one.
- FR-1.4 Warn when the passport expires within 6 months of the expected travel date; block a duplicate passport number from opening a second active file.
- FR-1.5 The only uploads in the whole app are one optional profile photo of the customer and one photo of the visa (FR-5.1). No other documents, scans or receipts are uploaded; receipts and PDFs are generated by the app.
- FR-1.6 Record the customer's consent to receive WhatsApp updates.

### 5.2 Registration: visa type and services

- FR-2.1 Visa type is a required single choice: Work, Umrah, Family, Tourist.
- FR-2.2 Service checkboxes: All Process, GAMCA Medical, Skill Test, MOFA, VFS Appointment, Ticket. All Process ticks every service and also adds Visa Stamping, Employment Contract and Emigration (ECR only).
- FR-2.3 Choosing a visa type pre-ticks its usual services (section 4 table); staff can change them.
- FR-2.4 Ticking a service opens its section below the checkboxes, as a separate card, always in process order.
- FR-2.5 Unticking a service that already holds data asks for confirmation ("Remove GAMCA Medical details?"); on Yes the section and its data are removed and an Undo link shows for 10 seconds. An empty section closes at once.
- FR-2.6 A service with a completed stage or a recorded payment cannot be unticked; it can only be cancelled with a reason, which stays in the activity log.
- FR-2.7 Each section has its own Reset button that clears only that section's fields, after confirmation.
- FR-2.8 Unticking any one service unticks All Process; ticking all services individually ticks All Process.
- FR-2.9 Edit profile opens the same form as registration, with the same visa type options, service checkboxes, sections and Reset buttons, filled with the saved data. The same rules apply; services whose stage is already done show as locked.

### 5.3 Service sections: fields

| Service | Fields |
| --- | --- |
| GAMCA medical | Centre, city, Wafid slip/appointment no., date, time, Result (fit/unfit), cost |
| Skill test | Centre, city, date, time, attempt no., result, cost |
| MOFA | Mail/application date, MOFA application no., VFS reference no. |
| VFS appointment | City (e.g. Lucknow, Patna), date, time, reference no., status |
| Visa stamping | Mumbai agency, documents sent date, courier, visa done date, passport received back date, charge |
| Employment contract | Agency, date, charge |
| Emigration | eMigrate application no., clearance date (ECR only) |
| Ticket | Legs (from, to, date), airline, PNR, supplier (e.g. Akbar Travels), cost price, selling price |
| Umrah package | Licensed Umrah agent, package name, hotel, visa no., dates |
| Family visit | Sponsor name and Iqama no., relationship, MOFA reference slip no. |

### 5.4 Stage tracking

- FR-4.1 Each file shows only the stages of its selected services, in process order.
- FR-4.2 Each stage stores status (Pending, Scheduled, Done, Failed, Cancelled), date, time, place, reference number and remarks.
- FR-4.3 Any stage can be marked Done with one tap, in any order, as soon as it is complete in real life.
- FR-4.4 If that stage's details are not filled yet, a pop-up opens with its fields so staff can fill them now; they can also choose "Mark done, fill later". Such stages show a "details pending" tag until filled.
- FR-4.5 Nothing is blocked. If an earlier related stage is not done (e.g. stamping before medical fit), the pop-up shows a warning only.
- FR-4.6 A done stage can be reopened or edited by the owner; every change goes to the activity log.
- FR-4.7 A failed medical or skill test, or a re-appointment, is saved as a new attempt; earlier attempts are never overwritten. A retest adds its charge to the customer.
- FR-4.8 "Visa Done" and "Passport received back" are shown as stamps on the file, as in the register.

### 5.5 Visa and sponsor

- FR-5.1 Record country, visa number, sponsor/company ID, sponsor name, visa date in Hijri with automatic Gregorian date, profession and visa type, plus one photo of the visa.
- FR-5.2 One sponsor can hold many visas; a visa is linked to one active file at a time.
- FR-5.3 If a candidate drops out, the visa can be released and given to another candidate; the history stays on the visa.

### 5.6 Courier and passport tracking

- FR-6.1 Record every movement of passport, documents or ticket: what, from, to, by (courier name or by hand), tracking no., date sent, date received.
- FR-6.2 Each file shows where the passport is now (e.g. "With Mumbai agency since 31 Oct").
- FR-6.3 Alert staff if a courier is not marked received within 3 days, or the passport is not back 7 days after visa done.
- FR-6.4 A page lists all passports currently outside the office.

### 5.7 Ticket desk

- FR-7.1 Book a ticket for any customer, with or without a visa file. Typing the passport number or mobile finds the existing customer and links the ticket to that customer (and to the visa file, if any); otherwise a new customer is created from the passenger details.
- FR-7.2 One booking can hold several passengers and several legs (e.g. IXR → BOM → RUH).
- FR-7.3 Record supplier (Akbar Travels by default), airline, PNR, travel date, cost price and selling price; the app shows the margin to the owner.
- FR-7.4 Record payments as in section 6; send the flight details (PNR, date, times) and a travel-day reminder on WhatsApp.
- FR-7.5 Tickets booked inside a visa file appear on the ticket desk too, so all tickets are in one list.

### 5.8 Referrers

- FR-8.1 Keep a list of reference persons: name, mobile, place (India or Saudi), notes.
- FR-8.2 Show the files each referrer brought, by month.
- FR-8.3 A reference person is only a contact for follow-up; no commission or payment is tracked for them.

### 5.9 Centres, agencies and templates

- FR-9.1 Keep medical centres, skill test centres, Mumbai stamping agencies (e.g. Alif, Siddiqui Travel), ticket suppliers (Akbar Travels) and courier services, each with rates and contact.
- FR-9.2 The owner sets stages and default charges per country and visa type; Saudi Arabia is set up first, other GCC countries are added without code changes.

### 5.10 Dashboard and staff alerts

- FR-10.1 The dashboard is the home screen after login and answers "what needs doing today?" at a glance, on laptop and phone.
- FR-10.2 Quick actions: New registration, New ticket, Record payment, Open To-send list, Global search.
- FR-10.3 Key numbers, each clickable to its list: active files, visa done this month, departed this month, tickets this month, today's appointments, flights in the next 7 days, balance due, passports outside the office, WhatsApp messages to send, new website enquiries.
- FR-10.4 Today panel: one time-ordered list of appointments, courier hand-overs and flights for today and tomorrow.
- FR-10.5 Files by stage (pipeline) and files by visa type (Work, Umrah, Family, Tourist), each bar opening the filtered list.
- FR-10.6 Needs attention: medical unfit or test failed, results not entered, files stuck 7+ days, passports not back, balance due before stamping, passport expiring within 6 months.
- FR-10.7 Money this month: collected, paid out and margin (owner only), with collection by account (ICICI, HDFC, cash).
- FR-10.8 Also shown: next WhatsApp messages to send, passports outside the office, new enquiries, upcoming flights and work per staff member.
- FR-10.9 Staff see the same dashboard limited to their own files and without margin.

### 5.11 Messaging and reminders

WhatsApp is used free, with no paid API or third-party service. The app writes each message and shows a "Send on WhatsApp" button; tapping it opens WhatsApp (phone app, WhatsApp Web or Desktop) with the customer's number and the text already typed, and staff press Send. Every stage change and reminder therefore appears in a "To send" list so nothing is forgotten.

| Trigger | Appears in the "To send" list | Message tells the customer |
| --- | --- | --- |
| File created | Right after saving | File number, services, package, portal login |
| Visa attached | Right after saving | Visa received; next steps |
| Appointment booked or changed | Right after saving | Date, time, place, documents to carry |
| Appointment reminder | From 6:00 pm the day before (and the morning of) | Same details, short form |
| Stage marked done | Right after marking | Result and the next step |
| Medical unfit / skill test failed | After staff confirms | Retest needed, extra charge, new date when booked |
| Passport sent / received | Right after saving | Where the passport is now |
| Visa done | Right after marking | Visa stamped; next steps (emigration or ticket) |
| Payment received | Right after saving | Amount, balance, receipt link |
| Balance due | Every 7 days while due; again before stamping | Amount due and bank/UPI details |
| Ticket booked | Right after saving | Flight, PNR, date, reporting time |
| Travel day | Morning of travel | Checklist: passport, ticket, visa copy, contract |

- FR-11.1 Messages are sent with WhatsApp's free click-to-chat link (wa.me with the customer's number and the message text). No WhatsApp Business API, SMS gateway or other paid service is used.
- FR-11.2 After any save (registration, stage done, payment, courier, ticket), the app shows "Send on WhatsApp" with the ready text; one tap opens WhatsApp, staff press Send.
- FR-11.3 A "To send" list on the dashboard and Messages page holds every message that is due (including day-before reminders), oldest first, each with its own Send button. After sending, staff tap "Mark sent" (or the app asks when they come back); "Skip" is also available.
- FR-11.4 The owner can switch each trigger on or off and edit its wording in Hindi (Roman), Hindi (Devanagari) and English. No template approval is needed.
- FR-11.5 Each file shows its message history: prepared, sent by whom and when, or skipped. WhatsApp's own delivered/read ticks are seen in WhatsApp, not in the app.
- FR-11.6 Group messages (e.g. everyone with a medical tomorrow) are prepared together and sent one by one from the list.
- FR-11.7 Reminders are not prepared for cancelled or departed files; nothing is due between 9:00 pm and 7:00 am except travel-day checklists.
- FR-11.8 Messages go out from the office mobile that staff use (the free WhatsApp Business app is recommended), so replies come back to the office.

### 5.12 Customer portal

- FR-12.1 Login with mobile number and date of birth (no OTP, so no paid SMS or WhatsApp service is needed); 5 wrong tries lock the login for 30 minutes.
- FR-12.2 Shows current stage, next appointment with place and time, documents to bring, passport location, receipts and balance due.
- FR-12.3 Read-only; passport number shown as last 4 digits only.

### 5.13 Reports

- FR-13.1 Files opened, visa done, departed and cancelled, by month and visa type.
- FR-13.2 Ticket sales by month and supplier, with margin (owner only).
- FR-13.3 Balance due by customer; dues payable to centres and agencies.
- FR-13.4 GST register, retest log, passports outside the office, messages sent and delivery rate.
- FR-13.5 Export any report to Excel and PDF.

### 5.14 Public website

A fast, mobile-first website at the business's own domain brings in enquiries and builds trust. It is written fresh for Noor Travels Services, in its logo colours; nothing is copied from other agencies.

| Page | What it holds |
| --- | --- |
| Home (landing) | Clear headline on Gulf visas, Umrah and air tickets; Call, WhatsApp and Enquire buttons; services grid; 7-step work-visa journey; why choose us; countries served; Umrah strip; FAQ; enquiry form |
| Services | One section per service: work visa (all process), GAMCA medical, skill test, MOFA and VFS, visa stamping, emigration, Umrah, family and tourist visas, air tickets – what we do and documents to bring |
| Umrah | How Umrah booking works, what a package includes, documents, enquiry button |
| About | Story, what the office does, values, licence and GST details, office address |
| Contact | Address with map link, both mobiles (tap to call), WhatsApp, email, office hours, enquiry form |
| Privacy policy and Terms | How customer data is used and kept; service terms and refund policy summary |

- FR-15.1 The only way into the software from the website is one Login button in the header; it opens the login page for staff and customers.
- FR-15.2 Every page works on phones first, loads in under 3 seconds on 4G, and has sticky Call and WhatsApp buttons on mobile.
- FR-15.3 Basic SEO: page titles, descriptions, Google Business link, sitemap; content in English with key lines in Hindi.
- FR-15.4 No invented numbers, reviews or photos; real figures, photos and Google reviews are added only when Noor Travels Services provides them.

### 5.15 Website enquiries

- FR-16.1 Every enquiry form (name, mobile, service, message) creates an enquiry in the app and shows it at the top of the dashboard with a Call and a WhatsApp button.
- FR-16.2 Staff can call, mark as follow-up, or convert an enquiry into a registration or ticket booking in one click.

### 5.16 Customer profile and linking

- FR-17.1 Every person is one customer record, identified by passport number (unique); mobile number is the second key. The same customer is never entered twice.
- FR-17.2 A customer can have many visa files and many ticket bookings over the years. New registration and New ticket both look up the passport number (and mobile) first and offer "Use this customer" when a match is found.
- FR-17.3 The customer profile is the hub: photo, passport and contact details, reference person, all visa files with their current stage, all tickets, payments and balance across everything, messages, and the visa photo.
- FR-17.4 From any list (All files, Tickets, Payments, Appointments, Messages, search results) a tap on the name opens the customer profile; a tap on the file or ticket number opens that file or ticket. Every file and ticket page links back to the profile.
- FR-17.5 A ticket added later to a visa file appears both in the file and in the ticket desk, and on the customer profile.

### 5.17 Global search

- FR-18.1 A search box sits in the top bar on every screen; clicking it lets staff type right there, and results drop down directly under the box (no separate pop-up). On a laptop the "/" key jumps to the box; Esc or the × button clears it.
- FR-18.2 One box searches by passport number, mobile number, name, file number, ticket number, PNR, visa number and reference person; partial entries work (e.g. last 4 digits of passport or mobile).
- FR-18.3 Results appear while typing, grouped as Customers, Visa files and Tickets, each showing the key details and current stage; Enter or tap opens the profile, file or ticket.
- FR-18.4 Recent searches and recently opened customers are shown when the box is empty.
- FR-18.5 Staff see results only for data their role allows.

## 6. Charges and payments

Each work-visa customer pays a package decided individually at registration, split into Noor Travels Services' service charge and pass-through costs (centre fees, Mumbai agency, ticket). The Emigration Rules cap what a recruiting agent may charge an emigrant as service charge at ₹30,000 + 18% GST, with a receipt for every amount and payment only by digital mode or bank draft ([MEA advisory](https://www.goa.gov.in/wp-content/uploads/2024/08/ADVISORY-ON-OVERCHARGING.pdf), [Rule 25](https://future.indiankanoon.org/doc/5536687/)). Umrah, family, tourist and ticket customers are charged per service.

Default rate card, taken from the registers (editable in settings):

| Item | Cost (₹) | Paid to |
| --- | --- | --- |
| GAMCA medical | 9,500 per attempt | Medical centre |
| Skill test | 10,000 per attempt | Skill test centre |
| Visa stamping | 5,000 | Mumbai agency |
| Employment contract | 4,000 | Mumbai agency |
| Ticket (Saudi, one way) | Actual fare, about 22,000 | Akbar Travels / airline |
| Emigration | Government fee as applicable | eMigrate |

- FR-14.1 Package entered at registration as service charge + pass-through costs; a warning shows if the service charge exceeds the cap set in settings.
- FR-14.2 Every payment from a customer: date, amount, mode (cash, UPI, bank transfer, cheque, DD) and the account it went into (ICICI Bank, Kasia or HDFC Bank, Kasia), with reference number. Cash for service charge raises a warning.
- FR-14.3 A retest adds an extra charge equal to the centre's fee for that attempt.
- FR-14.4 Every payment to a centre, agency, supplier or courier is an expense of the file or ticket booking.
- FR-14.5 Per file and per ticket booking: amount to collect, received, balance, expenses and margin (margin for owner only).
- FR-14.6 GST invoice/receipt with the Noor Travels Services logo, address, GSTIN, RA licence number, SAC code, CGST/SGST break-up and both bank accounts for payment; printed, or shared on WhatsApp as a message with a secure link to view and download it.
- FR-14.7 Refund on cancellation = received − costs already paid − service charge earned up to the stage reached (shares per stage set by the owner); the owner approves; the refund policy is signed at registration.
- FR-14.8 Payments are never deleted; corrections are reversal entries.

## 7. Non-functional requirements

| Area | Requirement |
| --- | --- |
| Security | HTTPS only; passwords hashed; staff accounts locked after 5 failed logins |
| Data privacy | Passport numbers and documents stored encrypted, in private storage, visible only to logged-in staff; handled in line with India's Digital Personal Data Protection Act, 2023 |
| Audit | Every create, edit and payment logged with user and time; log cannot be edited |
| Backup | Automatic daily database and document backup, kept for 30 days |
| Performance | Pages load in under 3 seconds on a normal 4G connection |
| Devices | Every app screen and website page is fully usable on a phone from 360 px wide: forms in one column, tap targets at least 44 px, wide tables scroll sideways inside their box, the menu scrolls sideways, pop-ups fit the screen; on phones every list page (files, tickets, appointments, payments, reports) keeps only the search box on top and folds its filters behind a Filter button that shows how many filters are on, with removable filter chips, and long tables turn into easy-to-read cards; same screens on laptop |
| Language | Interface in English; candidate messages in Hindi and English |
| Availability | Hosted on a cloud server, target 99% uptime |

## 8. Data model overview

The system uses a relational database (PostgreSQL). Main tables:

| Table | Holds | Key links |
| --- | --- | --- |
| customers | Personal, passport (unique) and contact details, ECR/ECNR, message consent; one row per person, linked to all their files and tickets | referrer |
| referrers | Name, mobile, place, notes | — |
| files | Serial no., visa type, country, package, status, visa done and passport-back dates | customer, visa |
| file_services | Services ticked on the file and their own fields (medical, skill test, MOFA, VFS, stamping, contract, emigration, ticket, Umrah, family) | file |
| stage_attempts | One row per attempt or re-appointment: date, place, result, remarks | file_service |
| sponsors / visas | Sponsor ID and name; visa no., Hijri and Gregorian date, profession, assignment history | file |
| courier_movements | What moved, from, to, by, tracking no., sent and received dates | file or ticket booking |
| ticket_bookings / ticket_passengers / ticket_legs | Supplier, airline, PNR, legs, cost and selling price; passengers | customer, file (optional) |
| parties | Centres, Mumbai agencies, ticket suppliers, couriers with rate cards | — |
| charges / payments | Amounts to collect; money in (with bank account) and money out | file or ticket booking, party |
| bank_accounts | ICICI Kasia, HDFC Kasia, cash | — |
| documents | Visa photo and optional profile photo only | file |
| message_rules / message_templates / messages | Triggers, wording, every prepared message with who sent it and when (or skipped) | file or ticket booking |
| country_templates / template_stages | Stages, order, dependencies, default charges per country and visa type | — |
| users / audit_log | Logins, roles, change history | — |

Stage history is never overwritten: each attempt is its own row, so retests and re-appointments stay visible.

## 9. MVP scope and phases

The MVP alone replaces both paper registers; later phases add convenience and growth.

| Phase | Delivers | Requirements |
| --- | --- | --- |
| MVP | Public website with login button; login and roles; registration and edit profile with visa type and service checkboxes; visa and sponsor; one-tap stage marking with details pop-up; courier and passport tracking; ticket desk; referrers; charges, payments, GST receipts, refunds; free WhatsApp click-to-send messages and reminders; enquiries; dashboard; customer profile linking files and tickets; global search | FR-1 to FR-11, FR-14 to FR-18 |
| Phase 2 | Customer portal, UPI payment link, bulk messages, full reports and exports | FR-12, FR-13, FR-11.4 |
| Phase 3 | Other GCC country templates, import of old register entries, mobile app wrapper | FR-9.2 |

Timeline and cost are shared in a separate proposal once the open questions below are closed.

## 10. Assumptions and open questions

Assumptions made for this version (correct any that are wrong):

- "Ref" in the register is a reference person kept as a contact only; no commission is paid (confirmed).
- "@ AKBT" in the ticket register means the ticket was booked through Akbar Travels; the small figure beside the fare (e.g. 1K) is Noor Travels Services' margin.
- The ₹4,000 "Agreement" is the employment contract fee paid to the Mumbai agency.
- "PPT-R" is the date the passport came back to the office; "Visa Done" is the date the visa was stamped.
- Noor Travels Services holds its own recruiting agent (RA) licence and files emigration on eMigrate.

Open questions:

- [ ] Is Umrah done through a Saudi-licensed Umrah agent partner? Which one, and what is the usual package price?
- [ ] Which pass-through costs fall inside the ₹30,000 service charge cap (Rule 25 lists trade test fees among items covered by the service charge); confirm with the accountant or legal adviser.
- [ ] Earned share of service charge at each stage, for refunds.
- [ ] GST rate and taxable portion of the package; GST on ticket service charges.
- [ ] Should old register entries (visa serials up to the current number, ticket serials up to 3452) be entered into the app?
- [ ] Which office mobile number customers should get WhatsApp messages from (free WhatsApp Business app on that phone)?
- [ ] Website: domain name, email address, office hours, year started, RA licence number, office and team photos, and the short story for the About page.
- [ ] Expected go-live date, budget, and who bears server costs.

Sources:

- [How Indians can get a work visa for Saudi Arabia, 2026](https://nriinformation.com/immigration/work-visa-saudi-arabia)
- [Umrah Visa 2026: How to Apply, Nusuk Rules](https://blog.wego.com/2026-umrah-visa/)
- [Saudi Embassy India – Family Visit or Residency Visa](https://embassies.mofa.gov.sa/sites/india/EN/Services/Visa-Instructions/Pages/Family-Visit-or-Residency-Visa.aspx)
- [Saudi Arabia visa for Indians – Tasheer centres](https://www.indianholiday.com/international-tours/saudi-arabia/visa.html)
- [MEA advisory on overcharging by recruiting agents](https://www.goa.gov.in/wp-content/uploads/2024/08/ADVISORY-ON-OVERCHARGING.pdf)
- [Emigration Rules 1983 – Rule 25](https://future.indiankanoon.org/doc/5536687/)
