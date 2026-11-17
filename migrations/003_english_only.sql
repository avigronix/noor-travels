-- Customer messages, portal and website are English only: drop the Hindi template columns and the
-- language setting, and polish three English bodies (the reminder also goes out on the morning of, so no "tomorrow").
ALTER TABLE message_templates DROP COLUMN IF EXISTS body_hi, DROP COLUMN IF EXISTS body_hi_dev;
DELETE FROM settings WHERE key = 'message_lang';

UPDATE message_templates SET body_en =
    'Dear {name}, reminder: your {stage} appointment is on {date} at {time}, {place}. Please carry: {documents}.'
WHERE trigger = 'appointment_reminder';
UPDATE message_templates SET body_en =
    'Dear {name}, we have received {amount} on {date}. Balance: {balance}. Receipt: {receipt_link}'
WHERE trigger = 'payment_received';
UPDATE message_templates SET body_en =
    'Dear {name}, your ticket is booked: {route} on {date}, PNR {pnr}. Please reach the airport by {report_time}.'
WHERE trigger = 'ticket_booked';
