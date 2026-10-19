-- W6 source scheduling is opt-in and the migration only stores state, never starts network work.
CREATE TABLE source_operational_state (
    source_id uuid PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    next_due_at timestamptz,
    recrawl_priority numeric(6,4) NOT NULL DEFAULT 0,
    interval_seconds integer NOT NULL DEFAULT 86400 CHECK (interval_seconds >= 60),
    plan_reason text NOT NULL DEFAULT 'not evaluated',
    last_processed_run_id uuid REFERENCES crawl_runs(id) ON DELETE SET NULL,
    lease_token uuid,
    lease_until timestamptz,
    evaluated_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((lease_token IS NULL) = (lease_until IS NULL))
);
CREATE INDEX source_operational_due_idx ON source_operational_state(next_due_at)
    WHERE next_due_at IS NOT NULL;

-- Last-seen source/run pair is used for exactly-once signal processing across maintenance retries.
-- A review item is created only on first detection or after the prior review was closed and
-- the emission cooldown has expired. Incidents are scoped to a source + signal code.
CREATE TABLE source_regression_state (
    source_id uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    signal_code text NOT NULL,
    last_seen_run_id uuid REFERENCES crawl_runs(id) ON DELETE SET NULL,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    occurrence_count integer NOT NULL DEFAULT 0,
    last_emitted_at timestamptz,
    last_review_id uuid REFERENCES review_queue_items(id) ON DELETE SET NULL,
    PRIMARY KEY(source_id, signal_code)
);
