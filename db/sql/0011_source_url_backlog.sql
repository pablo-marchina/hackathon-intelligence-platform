-- Iteration 20: source-scoped durable URL work queue for authorized RSS/sitemaps.
-- No collector is activated by this migration.
CREATE TABLE source_url_backlog (
    id bigserial PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    url text NOT NULL,
    canonical_url text NOT NULL,
    domain citext NOT NULL,
    kind text NOT NULL CHECK (kind IN ('SEED', 'DISCOVERED')),
    state text NOT NULL DEFAULT 'PENDING'
        CHECK (state IN ('PENDING', 'CLAIMED', 'DONE', 'RETRY', 'EXHAUSTED')),
    priority integer NOT NULL DEFAULT 0,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    last_finished_at timestamptz,
    next_fetch_at timestamptz NOT NULL DEFAULT now(),
    claim_token uuid,
    claim_until timestamptz,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    last_error text,
    CONSTRAINT source_url_backlog_unique UNIQUE(source_id, canonical_url),
    CONSTRAINT source_url_backlog_claim_consistency CHECK (
        (state = 'CLAIMED' AND claim_token IS NOT NULL AND claim_until IS NOT NULL)
        OR (state <> 'CLAIMED' AND claim_token IS NULL AND claim_until IS NULL)
    )
);
CREATE INDEX source_url_backlog_claim_idx
    ON source_url_backlog(source_id, next_fetch_at, priority DESC, first_seen_at)
    WHERE state IN ('PENDING', 'RETRY', 'CLAIMED');
CREATE INDEX source_url_backlog_domain_idx ON source_url_backlog(source_id, domain, state);
