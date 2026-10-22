-- Iteration 26: immutable outcomes tied to the original authorized crawl.
-- Existing staged records have NULL staged_crawl_run_id: never invent their origin.
ALTER TABLE source_event_payloads
    ADD COLUMN staged_crawl_run_id uuid REFERENCES crawl_runs(id) ON DELETE RESTRICT;

CREATE TABLE canonical_processing_outcomes (
    id bigserial PRIMARY KEY,
    source_event_ref_id uuid NOT NULL REFERENCES source_event_refs(id) ON DELETE RESTRICT,
    source_id uuid NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    crawl_run_id uuid REFERENCES crawl_runs(id) ON DELETE RESTRICT,
    content_hash char(64) NOT NULL,
    outcome text NOT NULL CHECK (outcome IN ('CREATED','LINKED','UPDATED','REJECTED')),
    hackathon_id uuid REFERENCES hackathons(id) ON DELETE RESTRICT,
    processed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_event_ref_id, content_hash),
    CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CHECK ((outcome = 'REJECTED' AND hackathon_id IS NULL)
        OR (outcome <> 'REJECTED' AND hackathon_id IS NOT NULL))
);
CREATE INDEX canonical_processing_outcomes_run_idx
    ON canonical_processing_outcomes(crawl_run_id, processed_at)
    WHERE crawl_run_id IS NOT NULL;
CREATE INDEX canonical_processing_outcomes_source_idx
    ON canonical_processing_outcomes(source_id, processed_at);
