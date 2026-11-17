-- Noor Travels Services – base schema (PostgreSQL 16)
-- Matches SRS section 8. Money is NUMERIC(12,2) in rupees. Times are timestamptz.
-- Rule: history is never overwritten – attempts, payments, messages, audit are append-only.

CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- fast partial search (name, passport, mobile)

-- ---------- people & logins ----------

CREATE TABLE users (
    id              serial PRIMARY KEY,
    name            text NOT NULL,
    username        text NOT NULL UNIQUE,
    mobile          text,
    password_hash   text NOT NULL,
    role            text NOT NULL CHECK (role IN ('owner', 'staff', 'accountant')),
    is_active       boolean NOT NULL DEFAULT true,
    failed_logins   int NOT NULL DEFAULT 0,
    locked_until    timestamptz,
    last_login_at   timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE referrers (                       -- contact only, no commission (FR-8.3)
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    mobile      text,
    place       text,                          -- e.g. 'Riyadh, Saudi' / 'Siwan'
    notes       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE customers (                       -- one row per person (FR-17.1)
    id                  serial PRIMARY KEY,
    name                text NOT NULL,
    father_name         text,
    dob                 date,
    mobile              text NOT NULL,         -- WhatsApp number, second key
    family_mobile       text,
    address             text,
    district            text,
    state               text,
    pin                 text,
    passport_no         text UNIQUE,           -- main key; stored upper-case, no spaces
    passport_issue      date,
    passport_expiry     date,
    passport_place      text,
    ecr                 text CHECK (ecr IN ('ECR', 'ECNR')),
    profession          text,
    referrer_id         int REFERENCES referrers(id),
    whatsapp_consent    boolean NOT NULL DEFAULT true,
    portal_failed       int NOT NULL DEFAULT 0, -- customer portal lockout (FR-12.1)
    portal_locked_until timestamptz,
    created_by          int REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX customers_mobile_idx   ON customers (mobile);
CREATE INDEX customers_name_trgm    ON customers USING gin (name gin_trgm_ops);
CREATE INDEX customers_pass_trgm    ON customers USING gin (passport_no gin_trgm_ops);
CREATE INDEX customers_mobile_trgm  ON customers USING gin (mobile gin_trgm_ops);

-- ---------- partners & accounts ----------

CREATE TABLE parties (                         -- centres, Mumbai agencies, suppliers, couriers (FR-9.1)
    id          serial PRIMARY KEY,
    kind        text NOT NULL CHECK (kind IN ('medical', 'skill_test', 'agency', 'supplier', 'courier', 'umrah_agent')),
    name        text NOT NULL,
    city        text,
    contact     text,
    mobile      text,
    default_rate numeric(12,2),
    notes       text,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE bank_accounts (
    id          serial PRIMARY KEY,
    name        text NOT NULL,                 -- 'ICICI Bank, Kasia'
    account_no  text,
    ifsc        text,
    is_cash     boolean NOT NULL DEFAULT false,
    is_active   boolean NOT NULL DEFAULT true
);

-- ---------- visas ----------

CREATE TABLE sponsors (
    id          serial PRIMARY KEY,
    sponsor_no  text,                          -- company / sponsor ID
    name        text NOT NULL,
    country     text NOT NULL DEFAULT 'Saudi Arabia',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE visas (
    id              serial PRIMARY KEY,
    sponsor_id      int REFERENCES sponsors(id),
    country         text NOT NULL DEFAULT 'Saudi Arabia',
    visa_no         text NOT NULL,
    visa_date_hijri text,                      -- '03/04/1447' as written
    visa_date       date,                      -- Gregorian, auto-converted
    profession      text,
    visa_type       text,
    photo_path      text,                      -- the one visa photo (FR-1.5)
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX visas_no_idx ON visas (visa_no);

-- ---------- visa files ----------

CREATE TABLE files (
    id                  serial PRIMARY KEY,
    serial_no           int NOT NULL UNIQUE,   -- continues from the paper register (FR-1.1)
    customer_id         int NOT NULL REFERENCES customers(id),
    visa_id             int REFERENCES visas(id),
    visa_type           text NOT NULL CHECK (visa_type IN ('work', 'umrah', 'family', 'tourist')),
    country             text NOT NULL DEFAULT 'Saudi Arabia',
    status              text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'departed', 'cancelled')),
    current_stage       text,                  -- denormalised for fast lists, kept in sync by code
    service_charge      numeric(12,2) NOT NULL DEFAULT 0,
    package_total       numeric(12,2) NOT NULL DEFAULT 0,
    expected_travel     date,
    visa_done_on        date,                  -- 'Visa Done' stamp
    passport_back_on    date,                  -- 'PPT-R' stamp
    passport_location   text NOT NULL DEFAULT 'Office',
    cancel_reason       text,
    assigned_to         int REFERENCES users(id),
    created_by          int REFERENCES users(id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX files_customer_idx ON files (customer_id);
CREATE INDEX files_status_idx   ON files (status, visa_type);
-- one active file per passport/customer (FR-1.4)
CREATE UNIQUE INDEX files_one_active_per_customer ON files (customer_id) WHERE status = 'active';
-- a visa is linked to one active file at a time (FR-5.2)
CREATE UNIQUE INDEX files_one_active_per_visa ON files (visa_id) WHERE status = 'active' AND visa_id IS NOT NULL;

CREATE TABLE file_services (                   -- ticked services + their own fields (FR-2, 5.3)
    id          serial PRIMARY KEY,
    file_id     int NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    service     text NOT NULL CHECK (service IN ('medical', 'skill_test', 'mofa', 'vfs', 'stamping',
                                                 'contract', 'emigration', 'ticket', 'umrah', 'family')),
    sort_order  int NOT NULL,                  -- process order
    status      text NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'scheduled', 'done', 'failed', 'cancelled')),
    data        jsonb NOT NULL DEFAULT '{}',   -- section fields from SRS 5.3 (centre, ref no, ...)
    details_pending boolean NOT NULL DEFAULT false, -- 'Mark done, fill later' (FR-4.4)
    done_on     date,
    cost        numeric(12,2),
    cancel_reason text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (file_id, service)
);

CREATE TABLE stage_attempts (                  -- never overwritten (FR-4.7)
    id              serial PRIMARY KEY,
    file_service_id int NOT NULL REFERENCES file_services(id) ON DELETE CASCADE,
    attempt_no      int NOT NULL,
    status          text NOT NULL DEFAULT 'scheduled'
                    CHECK (status IN ('scheduled', 'done', 'failed', 'cancelled')),
    party_id        int REFERENCES parties(id),
    city            text,
    on_date         date,
    at_time         time,
    ref_no          text,
    result          text,                      -- fit / unfit / pass / fail
    remarks         text,
    charge          numeric(12,2),             -- retest charge added to customer
    created_by      int REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (file_service_id, attempt_no)
);
CREATE INDEX stage_attempts_date_idx ON stage_attempts (on_date);

-- ---------- tickets ----------

CREATE TABLE ticket_bookings (
    id              serial PRIMARY KEY,
    ticket_no       int NOT NULL UNIQUE,       -- continues from ticket register
    customer_id     int NOT NULL REFERENCES customers(id),   -- who booked / pays
    file_id         int REFERENCES files(id),  -- optional link to visa file (FR-7.5)
    supplier_id     int REFERENCES parties(id),
    airline         text,
    pnr             text,
    travel_date     date,
    cost_price      numeric(12,2) NOT NULL DEFAULT 0,
    selling_price   numeric(12,2) NOT NULL DEFAULT 0,
    status          text NOT NULL DEFAULT 'booked' CHECK (status IN ('booked', 'travelled', 'cancelled')),
    created_by      int REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ticket_bookings_customer_idx ON ticket_bookings (customer_id);
CREATE INDEX ticket_bookings_travel_idx   ON ticket_bookings (travel_date);
CREATE INDEX ticket_bookings_pnr_idx      ON ticket_bookings (upper(pnr));

CREATE TABLE ticket_passengers (
    id          serial PRIMARY KEY,
    booking_id  int NOT NULL REFERENCES ticket_bookings(id) ON DELETE CASCADE,
    customer_id int NOT NULL REFERENCES customers(id),
    UNIQUE (booking_id, customer_id)
);

CREATE TABLE ticket_legs (
    id          serial PRIMARY KEY,
    booking_id  int NOT NULL REFERENCES ticket_bookings(id) ON DELETE CASCADE,
    leg_no      int NOT NULL,
    from_code   text NOT NULL,                 -- LKO
    to_code     text NOT NULL,                 -- RUH
    depart_at   timestamptz,
    flight_no   text,
    UNIQUE (booking_id, leg_no)
);

-- ---------- courier ----------

CREATE TABLE courier_movements (               -- FR-6
    id          serial PRIMARY KEY,
    file_id     int REFERENCES files(id),
    booking_id  int REFERENCES ticket_bookings(id),
    item        text NOT NULL,                 -- passport / documents / ticket
    from_place  text NOT NULL,
    to_place    text NOT NULL,
    courier_id  int REFERENCES parties(id),
    by_hand     text,
    tracking_no text,
    sent_on     date NOT NULL,
    received_on date,
    created_by  int REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (file_id IS NOT NULL OR booking_id IS NOT NULL)
);
CREATE INDEX courier_open_idx ON courier_movements (sent_on) WHERE received_on IS NULL;

-- ---------- money ----------

CREATE TABLE charges (                         -- what the customer owes, line by line
    id          serial PRIMARY KEY,
    file_id     int REFERENCES files(id),
    booking_id  int REFERENCES ticket_bookings(id),
    label       text NOT NULL,                 -- 'Service charge', 'Medical retest #2'
    amount      numeric(12,2) NOT NULL,
    gst_rate    numeric(5,2) NOT NULL DEFAULT 0,
    created_by  int REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CHECK (file_id IS NOT NULL OR booking_id IS NOT NULL)
);

CREATE TABLE payments (                        -- never deleted; corrections are reversals (FR-14.8)
    id              serial PRIMARY KEY,
    direction       text NOT NULL CHECK (direction IN ('in', 'out')),
    file_id         int REFERENCES files(id),
    booking_id      int REFERENCES ticket_bookings(id),
    customer_id     int REFERENCES customers(id),
    party_id        int REFERENCES parties(id),       -- for money out
    bank_account_id int REFERENCES bank_accounts(id),
    amount          numeric(12,2) NOT NULL,           -- negative only for reversals
    mode            text NOT NULL CHECK (mode IN ('cash', 'upi', 'bank', 'cheque', 'dd')),
    ref_no          text,
    paid_on         date NOT NULL DEFAULT current_date,
    receipt_no      text UNIQUE,
    reverses_id     int REFERENCES payments(id),
    note            text,
    created_by      int REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (file_id IS NOT NULL OR booking_id IS NOT NULL)
);
CREATE INDEX payments_file_idx ON payments (file_id);
CREATE INDEX payments_date_idx ON payments (paid_on);

-- ---------- messages (free wa.me click-to-send) ----------

CREATE TABLE message_templates (               -- FR-11.4
    id          serial PRIMARY KEY,
    trigger     text NOT NULL UNIQUE,          -- file_created, appointment_reminder, ...
    title       text NOT NULL,
    body_en     text NOT NULL,
    body_hi     text,                          -- Hindi (Roman)
    body_hi_dev text,                          -- Hindi (Devanagari)
    is_active   boolean NOT NULL DEFAULT true,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE messages (                        -- the 'To send' queue + history (FR-11.3, 11.5)
    id          serial PRIMARY KEY,
    customer_id int NOT NULL REFERENCES customers(id),
    file_id     int REFERENCES files(id),
    booking_id  int REFERENCES ticket_bookings(id),
    trigger     text NOT NULL,
    body        text NOT NULL,
    due_at      timestamptz NOT NULL DEFAULT now(),
    status      text NOT NULL DEFAULT 'to_send' CHECK (status IN ('to_send', 'sent', 'skipped')),
    done_by     int REFERENCES users(id),
    done_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_queue_idx ON messages (due_at) WHERE status = 'to_send';

-- ---------- website enquiries ----------

CREATE TABLE enquiries (                       -- FR-16
    id          serial PRIMARY KEY,
    name        text NOT NULL,
    mobile      text NOT NULL,
    service     text,
    message     text,
    status      text NOT NULL DEFAULT 'new' CHECK (status IN ('new', 'follow_up', 'converted', 'closed')),
    customer_id int REFERENCES customers(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------- photos (only two kinds allowed, FR-1.5) ----------

CREATE TABLE documents (
    id          serial PRIMARY KEY,
    customer_id int NOT NULL REFERENCES customers(id),
    kind        text NOT NULL CHECK (kind IN ('profile_photo', 'visa_photo')),
    path        text NOT NULL,
    uploaded_by int REFERENCES users(id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (customer_id, kind)
);

-- ---------- settings, templates, audit ----------

CREATE TABLE settings (                        -- key/value: next serials, GST cap, GSTIN ...
    key     text PRIMARY KEY,
    value   text NOT NULL
);

CREATE TABLE template_stages (                 -- FR-9.2: stages & default charges per country/visa type
    id          serial PRIMARY KEY,
    country     text NOT NULL,
    visa_type   text NOT NULL,
    service     text NOT NULL,
    sort_order  int NOT NULL,
    default_on  boolean NOT NULL DEFAULT true,
    default_charge numeric(12,2),
    UNIQUE (country, visa_type, service)
);

CREATE TABLE audit_log (                       -- append-only (SRS 7)
    id          bigserial PRIMARY KEY,
    user_id     int REFERENCES users(id),
    action      text NOT NULL,                 -- create / update / delete / payment / login
    entity      text NOT NULL,                 -- 'file', 'customer', ...
    entity_id   int,
    details     jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_entity_idx ON audit_log (entity, entity_id);

-- ---------- seed data ----------

INSERT INTO settings (key, value) VALUES
    ('next_file_serial', '4277'),
    ('next_ticket_no', '3453'),
    ('service_charge_cap', '30000'),
    ('gst_rate', '18'),
    ('gstin', ''),
    ('ra_licence_no', '');

INSERT INTO bank_accounts (name, account_no, ifsc, is_cash) VALUES
    ('ICICI Bank, Kasia', '320605001885', 'ICIC0003206', false),
    ('HDFC Bank, Kasia', '50200086342104', 'HDFC0002285', false),
    ('Cash', NULL, NULL, true);

INSERT INTO parties (kind, name, city, default_rate) VALUES
    ('agency', 'Alif (Mumbai)', 'Mumbai', 5000),
    ('agency', 'Siddiqui Travel (Mumbai)', 'Mumbai', 5000),
    ('supplier', 'Akbar Travels', NULL, NULL);

-- Saudi Arabia defaults (SRS 4 and 6)
INSERT INTO template_stages (country, visa_type, service, sort_order, default_on, default_charge) VALUES
    ('Saudi Arabia', 'work', 'medical',    1, true, 9500),
    ('Saudi Arabia', 'work', 'skill_test', 2, true, 10000),
    ('Saudi Arabia', 'work', 'mofa',       3, true, NULL),
    ('Saudi Arabia', 'work', 'vfs',        4, true, NULL),
    ('Saudi Arabia', 'work', 'stamping',   5, true, 5000),
    ('Saudi Arabia', 'work', 'contract',   6, true, 4000),
    ('Saudi Arabia', 'work', 'emigration', 7, true, NULL),
    ('Saudi Arabia', 'work', 'ticket',     8, true, 22000),
    ('Saudi Arabia', 'umrah', 'umrah',     1, true, NULL),
    ('Saudi Arabia', 'umrah', 'ticket',    2, true, NULL),
    ('Saudi Arabia', 'family', 'family',   1, true, NULL),
    ('Saudi Arabia', 'family', 'mofa',     2, true, NULL),
    ('Saudi Arabia', 'family', 'vfs',      3, true, NULL),
    ('Saudi Arabia', 'family', 'ticket',   4, true, NULL),
    ('Saudi Arabia', 'tourist', 'vfs',     1, true, NULL),
    ('Saudi Arabia', 'tourist', 'ticket',  2, true, NULL);

INSERT INTO message_templates (trigger, title, body_en, body_hi) VALUES
    ('file_created', 'File created',
     'Dear {name}, your file No. {file_no} is opened at Noor Travels Services for {services}. Track status: {portal_link}',
     '{name} ji, aapki file No. {file_no} Noor Travels Services me khul gayi hai ({services}). Status dekhein: {portal_link}'),
    ('appointment_booked', 'Appointment booked',
     'Dear {name}, your {stage} appointment is on {date} at {time}, {place}. Please carry: {documents}.',
     '{name} ji, aapka {stage} appointment {date} ko {time} baje {place} me hai. Saath laayein: {documents}.'),
    ('appointment_reminder', 'Appointment reminder',
     'Reminder: {stage} tomorrow {date} at {time}, {place}.',
     'Yaad dilayein: kal {date} ko {time} baje {stage}, {place}.'),
    ('stage_done', 'Stage done',
     'Dear {name}, {stage} is complete. Next step: {next_step}.',
     '{name} ji, {stage} ho gaya hai. Agla step: {next_step}.'),
    ('payment_received', 'Payment received',
     'Received {amount} on {date}. Balance: {balance}. Receipt: {receipt_link}',
     '{date} ko {amount} mile. Baaki: {balance}. Receipt: {receipt_link}'),
    ('ticket_booked', 'Ticket booked',
     'Your ticket is booked: {route} on {date}, PNR {pnr}. Reach airport by {report_time}.',
     'Aapka ticket book ho gaya: {route}, {date}, PNR {pnr}. Airport {report_time} tak pahunchein.');
