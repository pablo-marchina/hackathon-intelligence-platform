-- Transactional watermark for new catalog events -> saved-search alert production.
-- Initialized only on first enabled run, not at migration time, to avoid
-- unexpected historical notification floods.
CREATE TABLE alert_match_progress (
    processor_key text PRIMARY KEY,
    last_created_at timestamptz NOT NULL,
    last_hackathon_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hackathons_match_scan_idx ON hackathons(created_at, id);
