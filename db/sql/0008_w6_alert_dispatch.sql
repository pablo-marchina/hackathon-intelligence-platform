-- Durable claim/fencing metadata for email dispatch. No SMTP delivery is enabled by this migration.
ALTER TABLE alert_deliveries
  ADD COLUMN IF NOT EXISTS dispatch_token uuid,
  ADD COLUMN IF NOT EXISTS dispatch_expires_at timestamptz;

CREATE INDEX IF NOT EXISTS alert_delivery_due_idx
  ON alert_deliveries(next_attempt_at, id)
  WHERE status = 'PENDING';
