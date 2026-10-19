-- W3: Open-web discovery persistence and auditability

CREATE TABLE IF NOT EXISTS discovery_query_templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    template_key text NOT NULL,
    version text NOT NULL,
    language text NOT NULL,
    event_term text NOT NULL,
    intent_term text NOT NULL,
    time_strategy text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(template_key, version)
);

CREATE TABLE IF NOT EXISTS discovery_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type text NOT NULL,
    provider text,
    status text NOT NULL DEFAULT 'RUNNING',
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    generated_query_count integer NOT NULL DEFAULT 0,
    result_count integer NOT NULL DEFAULT 0,
    new_candidate_count integer NOT NULL DEFAULT 0,
    known_canonical_count integer NOT NULL DEFAULT 0,
    policy_blocked_count integer NOT NULL DEFAULT 0,
    classified_count integer NOT NULL DEFAULT 0,
    accepted_count integer NOT NULL DEFAULT 0,
    stats jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_summary text
);

CREATE TABLE IF NOT EXISTS discovery_queries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    discovery_run_id uuid REFERENCES discovery_runs(id) ON DELETE CASCADE,
    template_id uuid NOT NULL REFERENCES discovery_query_templates(id) ON DELETE RESTRICT,
    attribution_key text NOT NULL,
    query_text text NOT NULL,
    language text NOT NULL,
    year smallint NOT NULL,
    quarter smallint,
    time_window text NOT NULL,
    generated_at timestamptz NOT NULL DEFAULT now(),
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (quarter IS NULL OR quarter BETWEEN 1 AND 4),
    UNIQUE(discovery_run_id, attribution_key)
);

CREATE TABLE IF NOT EXISTS discovery_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    discovery_run_id uuid REFERENCES discovery_runs(id) ON DELETE CASCADE,
    discovery_query_id uuid REFERENCES discovery_queries(id) ON DELETE SET NULL,
    provider text NOT NULL,
    rank integer NOT NULL CHECK (rank > 0),
    url text NOT NULL,
    canonical_url text NOT NULL,
    domain citext NOT NULL,
    title text,
    snippet text,
    evidence_authority text NOT NULL DEFAULT 'DISCOVERY_ONLY',
    policy_decision text NOT NULL,
    action text NOT NULL,
    frontier_id bigint REFERENCES url_frontier(id) ON DELETE SET NULL,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CHECK (evidence_authority = 'DISCOVERY_ONLY'),
    UNIQUE(discovery_query_id, provider, canonical_url)
);

CREATE INDEX IF NOT EXISTS discovery_results_domain_idx ON discovery_results(domain, discovered_at DESC);
CREATE INDEX IF NOT EXISTS discovery_results_action_idx ON discovery_results(action, discovered_at DESC);

CREATE TABLE IF NOT EXISTS domain_discovery_budgets (
    id bigserial PRIMARY KEY,
    discovery_run_id uuid NOT NULL REFERENCES discovery_runs(id) ON DELETE CASCADE,
    domain citext NOT NULL,
    max_depth smallint NOT NULL DEFAULT 2 CHECK (max_depth BETWEEN 0 AND 10),
    max_pages integer NOT NULL DEFAULT 40 CHECK (max_pages > 0),
    max_external_domains integer NOT NULL DEFAULT 12 CHECK (max_external_domains >= 0),
    pages_seen integer NOT NULL DEFAULT 0 CHECK (pages_seen >= 0),
    external_domains_seen integer NOT NULL DEFAULT 0 CHECK (external_domains_seen >= 0),
    cap_reached boolean NOT NULL DEFAULT false,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(discovery_run_id, domain),
    CHECK (pages_seen <= max_pages),
    CHECK (external_domains_seen <= max_external_domains)
);

CREATE TABLE IF NOT EXISTS page_classifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    discovery_run_id uuid REFERENCES discovery_runs(id) ON DELETE CASCADE,
    frontier_id bigint REFERENCES url_frontier(id) ON DELETE SET NULL,
    raw_document_id uuid REFERENCES raw_documents(id) ON DELETE SET NULL,
    url text NOT NULL,
    classifier_version text NOT NULL,
    is_event boolean NOT NULL,
    is_hackathon_like boolean NOT NULL,
    event_type event_type NOT NULL DEFAULT 'other',
    confidence numeric(6,5) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    score integer NOT NULL,
    negative_class text,
    reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    classified_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS page_classifications_accept_idx
    ON page_classifications(is_hackathon_like, confidence DESC, classified_at DESC);
CREATE INDEX IF NOT EXISTS page_classifications_version_idx
    ON page_classifications(classifier_version, classified_at DESC);

CREATE TABLE IF NOT EXISTS discovery_classifier_feedback (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    classification_id uuid NOT NULL REFERENCES page_classifications(id) ON DELETE CASCADE,
    human_label boolean NOT NULL,
    feedback_type text NOT NULL,
    note text,
    created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION enqueue_discovery_false_positive_review()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    c page_classifications%ROWTYPE;
BEGIN
    SELECT * INTO c FROM page_classifications WHERE id = NEW.classification_id;
    IF NEW.human_label = false AND c.is_hackathon_like = true THEN
        INSERT INTO review_queue_items(queue_type, entity_type, entity_id, severity, payload)
        VALUES (
            'DISCOVERY_FALSE_POSITIVE',
            'page_classification',
            NEW.classification_id::text,
            'HIGH',
            jsonb_build_object(
                'url', c.url,
                'classifier_version', c.classifier_version,
                'confidence', c.confidence,
                'feedback_type', NEW.feedback_type,
                'note', NEW.note
            )
        )
        ON CONFLICT (queue_type, entity_type, entity_id, state) DO NOTHING;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS discovery_false_positive_review_trg ON discovery_classifier_feedback;
CREATE TRIGGER discovery_false_positive_review_trg
AFTER INSERT ON discovery_classifier_feedback
FOR EACH ROW EXECUTE FUNCTION enqueue_discovery_false_positive_review();

CREATE OR REPLACE FUNCTION ingest_discovery_result_to_frontier(
    p_source_id uuid,
    p_discovered_by_source_id uuid,
    p_discovery_query text,
    p_url text,
    p_canonical_url text,
    p_domain citext,
    p_priority integer,
    p_depth smallint,
    p_metadata jsonb
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_id bigint;
BEGIN
    INSERT INTO url_frontier(
        source_id, discovered_by_source_id, discovery_query, url, canonical_url,
        domain, state, priority, depth, metadata
    ) VALUES (
        p_source_id, p_discovered_by_source_id, p_discovery_query, p_url, p_canonical_url,
        p_domain, 'DISCOVERED', p_priority, p_depth, COALESCE(p_metadata, '{}'::jsonb)
    )
    ON CONFLICT (canonical_url) DO UPDATE SET
        priority = GREATEST(url_frontier.priority, EXCLUDED.priority),
        metadata = url_frontier.metadata || EXCLUDED.metadata
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;
