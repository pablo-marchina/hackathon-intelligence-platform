-- Durable per-event handoff between an authorized crawl and canonicalization.
-- No retroactive authorization is conferred by the existence of a staged record.
CREATE TABLE source_event_payloads (
    source_event_ref_id uuid PRIMARY KEY REFERENCES source_event_refs(id) ON DELETE CASCADE,
    source_id uuid NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_key text NOT NULL,
    raw_document_id uuid REFERENCES raw_documents(id) ON DELETE SET NULL,
    payload jsonb NOT NULL,
    content_hash char(64) NOT NULL,
    processed_hash char(64),
    observed_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    last_error text,
    CHECK (jsonb_typeof(payload) = 'object'),
    CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    CHECK (processed_hash IS NULL OR processed_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX source_event_payloads_pending_idx
    ON source_event_payloads(observed_at, source_id)
    WHERE processed_hash IS DISTINCT FROM content_hash;
