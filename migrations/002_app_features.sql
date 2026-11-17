-- 002: columns, tables, seed rows and indexes needed by the staff app (prompts 03–10).

-- ---------- charges: what each line is, so edits can keep them in sync ----------
ALTER TABLE charges ADD COLUMN kind text NOT NULL DEFAULT 'other'
    CHECK (kind IN ('service_charge', 'cost', 'retest', 'ticket', 'other'));
ALTER TABLE charges ADD COLUMN file_service_id int REFERENCES file_services(id) ON DELETE SET NULL;
ALTER TABLE charges ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX charges_file_idx    ON charges (file_id);
CREATE INDEX charges_booking_idx ON charges (booking_id);

-- ---------- payments: kind of money movement ----------
ALTER TABLE payments ADD COLUMN kind text NOT NULL DEFAULT 'payment'
    CHECK (kind IN ('payment', 'expense', 'refund', 'reversal'));
ALTER TABLE payments ADD COLUMN for_service_charge boolean NOT NULL DEFAULT false;
CREATE INDEX payments_booking_idx  ON payments (booking_id);
CREATE INDEX payments_customer_idx ON payments (customer_id);
CREATE INDEX payments_party_idx    ON payments (party_id);

-- refund approvals (FR-14.7): calculation shown, owner approves, then a refund payment row is added
CREATE TABLE refunds (
    id              serial PRIMARY KEY,
    file_id         int REFERENCES files(id),
    booking_id      int REFERENCES ticket_bookings(id),
    received        numeric(12,2) NOT NULL,
    costs_paid      numeric(12,2) NOT NULL,
    earned          numeric(12,2) NOT NULL,
    amount          numeric(12,2) NOT NULL,
    stage_reached   text,
    reason          text,
    payment_id      int REFERENCES payments(id),
    approved_by     int REFERENCES users(id),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (file_id IS NOT NULL OR booking_id IS NOT NULL)
);

-- ---------- visa ↔ file history (FR-5.2, FR-5.3) ----------
CREATE TABLE visa_assignments (
    id          serial PRIMARY KEY,
    visa_id     int NOT NULL REFERENCES visas(id),
    file_id     int NOT NULL REFERENCES files(id),
    assigned_on timestamptz NOT NULL DEFAULT now(),
    released_on timestamptz,
    reason      text,
    created_by  int REFERENCES users(id)
);
CREATE INDEX visa_assignments_visa_idx ON visa_assignments (visa_id);
CREATE INDEX visas_sponsor_idx ON visas (sponsor_id);

-- ---------- messages: idempotent reminders from jobs.py ----------
ALTER TABLE messages ADD COLUMN dedupe_key text;
CREATE UNIQUE INDEX messages_dedupe_idx ON messages (dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX messages_file_idx     ON messages (file_id);
CREATE INDEX messages_customer_idx ON messages (customer_id, created_at DESC);
CREATE INDEX messages_status_idx   ON messages (status, done_at DESC);

-- ---------- enquiries ----------
CREATE INDEX enquiries_status_idx ON enquiries (status, created_at DESC);
ALTER TABLE enquiries ADD COLUMN note text;

-- ---------- lists & dashboard ----------
CREATE INDEX files_assigned_idx       ON files (assigned_to, status);
CREATE INDEX files_created_idx        ON files (created_at DESC);
CREATE INDEX files_visa_done_idx      ON files (visa_done_on);
CREATE INDEX file_services_file_idx   ON file_services (file_id, sort_order);
CREATE INDEX file_services_status_idx ON file_services (service, status);
CREATE INDEX stage_attempts_sched_idx ON stage_attempts (on_date, at_time) WHERE status = 'scheduled';
CREATE INDEX courier_file_idx         ON courier_movements (file_id, sent_on DESC);
CREATE INDEX ticket_bookings_file_idx ON ticket_bookings (file_id);
CREATE INDEX ticket_bookings_created_idx ON ticket_bookings (created_at DESC);
CREATE INDEX ticket_passengers_customer_idx ON ticket_passengers (customer_id);
CREATE INDEX audit_created_idx        ON audit_log (created_at DESC);
CREATE INDEX customers_created_idx    ON customers (created_at DESC, id DESC);
CREATE INDEX customers_referrer_idx   ON customers (referrer_id);
CREATE INDEX files_serial_trgm        ON files USING gin ((serial_no::text) gin_trgm_ops);

-- ---------- settings ----------
INSERT INTO settings (key, value) VALUES
    ('sac_code', '998555'),
    ('next_receipt_no', '1'),
    ('receipt_prefix', 'NTS'),
    ('business_gstin', ''),
    -- share of the service charge earned once each stage is reached (FR-14.7), percent
    ('refund_shares', '{"registration": 10, "medical": 25, "skill_test": 35, "mofa": 45, "vfs": 55, "stamping": 80, "contract": 85, "emigration": 95, "ticket": 100}'),
    ('upi_id', ''),
    ('portal_url', '')
ON CONFLICT (key) DO NOTHING;

INSERT INTO parties (kind, name, city, default_rate) VALUES
    ('medical', 'Approved GAMCA centre', 'Lucknow', 9500),
    ('medical', 'Approved GAMCA centre', 'Patna', 9500),
    ('skill_test', 'Skill test centre', 'Lucknow', 10000),
    ('courier', 'DTDC', NULL, NULL),
    ('courier', 'By hand', NULL, NULL);

-- ---------- message templates for every trigger in SRS 5.11 ----------
INSERT INTO message_templates (trigger, title, body_en, body_hi) VALUES
    ('visa_attached', 'Visa attached',
     'Dear {name}, your visa No. {visa_no} is received at Noor Travels Services. Next step: {next_step}.',
     '{name} ji, aapka visa No. {visa_no} aa gaya hai. Agla step: {next_step}.'),
    ('appointment_changed', 'Appointment changed',
     'Dear {name}, your {stage} appointment is moved to {date} at {time}, {place}. Please carry: {documents}.',
     '{name} ji, aapka {stage} appointment ab {date} ko {time} baje {place} me hai. Saath laayein: {documents}.'),
    ('stage_failed', 'Medical unfit / test failed',
     'Dear {name}, your {stage} result is {result}. A retest is needed (extra charge {amount}). We will send the new date when booked.',
     '{name} ji, aapka {stage} result {result} aaya hai. Dobara test hoga (extra {amount}). Nayi date book hote hi bhejenge.'),
    ('passport_moved', 'Passport sent / received',
     'Dear {name}, your {item} is now: {location} ({date}).',
     '{name} ji, aapka {item} ab yahan hai: {location} ({date}).'),
    ('visa_done', 'Visa done',
     'Dear {name}, your visa is stamped on {date}. Next step: {next_step}.',
     '{name} ji, aapka visa {date} ko lag gaya hai. Agla step: {next_step}.'),
    ('balance_due', 'Balance due',
     'Dear {name}, balance due on file {file_no}: {balance}. Pay to {bank}. Thank you – Noor Travels Services.',
     '{name} ji, file {file_no} ka baaki {balance} hai. {bank} me jama karein. Shukriya – Noor Travels Services.'),
    ('travel_day', 'Travel day checklist',
     'Dear {name}, today is your flight {route} (PNR {pnr}). Carry: passport, ticket, visa copy, contract. Have a safe journey!',
     '{name} ji, aaj aapki flight {route} hai (PNR {pnr}). Saath rakhein: passport, ticket, visa copy, contract. Safar mubarak!')
ON CONFLICT (trigger) DO NOTHING;

-- portal link in the file-created message (prompt 10)
UPDATE message_templates SET
    body_en = 'Dear {name}, your file No. {file_no} is opened at Noor Travels Services for {services}. Check status any time: {portal_link} (login with your mobile and date of birth).',
    body_hi = '{name} ji, aapki file No. {file_no} Noor Travels Services me khul gayi hai ({services}). Status dekhein: {portal_link} (mobile aur janam tithi se login karein).'
WHERE trigger = 'file_created';
