-- W2 entity-resolution hardening: auditable series assignment and reversible merges.

CREATE TABLE IF NOT EXISTS event_series_assignment_audit (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    hackathon_id uuid NOT NULL REFERENCES hackathons(id) ON DELETE CASCADE,
    previous_event_series_id uuid REFERENCES event_series(id) ON DELETE SET NULL,
    new_event_series_id uuid REFERENCES event_series(id) ON DELETE SET NULL,
    previous_edition_label text,
    new_edition_label text,
    previous_edition_year smallint,
    new_edition_year smallint,
    assignment_method text NOT NULL CHECK (assignment_method IN ('INFERRED','MANUAL','CORRECTION')),
    confidence numeric(5,4),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    model_version text,
    actor_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
);
CREATE INDEX IF NOT EXISTS event_series_assignment_audit_event_idx
    ON event_series_assignment_audit(hackathon_id, created_at DESC);
CREATE INDEX IF NOT EXISTS event_series_assignment_audit_series_idx
    ON event_series_assignment_audit(new_event_series_id, created_at DESC);

ALTER TABLE merge_audit
    ADD COLUMN IF NOT EXISTS reversed_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL;

ALTER TABLE hackathon_aliases
    ADD COLUMN IF NOT EXISTS created_by_merge_audit_id uuid REFERENCES merge_audit(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS merge_source_ref_moves (
    merge_audit_id uuid NOT NULL REFERENCES merge_audit(id) ON DELETE CASCADE,
    source_event_ref_id uuid NOT NULL REFERENCES source_event_refs(id) ON DELETE RESTRICT,
    from_hackathon_id uuid NOT NULL REFERENCES hackathons(id) ON DELETE RESTRICT,
    to_hackathon_id uuid NOT NULL REFERENCES hackathons(id) ON DELETE RESTRICT,
    PRIMARY KEY (merge_audit_id, source_event_ref_id),
    CHECK (from_hackathon_id <> to_hackathon_id)
);

CREATE TABLE IF NOT EXISTS merge_observation_moves (
    merge_audit_id uuid NOT NULL REFERENCES merge_audit(id) ON DELETE CASCADE,
    observation_id bigint NOT NULL REFERENCES source_observations(id) ON DELETE RESTRICT,
    from_hackathon_id uuid NOT NULL REFERENCES hackathons(id) ON DELETE RESTRICT,
    to_hackathon_id uuid NOT NULL REFERENCES hackathons(id) ON DELETE RESTRICT,
    PRIMARY KEY (merge_audit_id, observation_id),
    CHECK (from_hackathon_id <> to_hackathon_id)
);

CREATE TABLE IF NOT EXISTS merge_alias_changes (
    id bigserial PRIMARY KEY,
    merge_audit_id uuid NOT NULL REFERENCES merge_audit(id) ON DELETE CASCADE,
    alias_id uuid REFERENCES hackathon_aliases(id) ON DELETE SET NULL,
    alias_type text NOT NULL,
    alias_value text NOT NULL,
    action text NOT NULL CHECK (action = 'CREATED')
);
CREATE INDEX IF NOT EXISTS merge_alias_changes_merge_idx ON merge_alias_changes(merge_audit_id);

CREATE OR REPLACE FUNCTION assign_hackathon_series(
    p_hackathon_id uuid,
    p_event_series_id uuid,
    p_edition_year smallint,
    p_edition_label text,
    p_assignment_method text,
    p_confidence numeric,
    p_evidence jsonb DEFAULT '{}'::jsonb,
    p_model_version text DEFAULT NULL,
    p_actor_user_id uuid DEFAULT NULL,
    p_reason text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
    v_hackathon hackathons%ROWTYPE;
    v_audit_id uuid;
BEGIN
    IF p_assignment_method NOT IN ('INFERRED','MANUAL','CORRECTION') THEN
        RAISE EXCEPTION 'invalid assignment_method: %', p_assignment_method;
    END IF;
    IF p_confidence IS NOT NULL AND (p_confidence < 0 OR p_confidence > 1) THEN
        RAISE EXCEPTION 'confidence outside [0,1]';
    END IF;

    SELECT * INTO v_hackathon
      FROM hackathons
     WHERE id = p_hackathon_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'hackathon % not found', p_hackathon_id;
    END IF;
    PERFORM 1 FROM event_series WHERE id = p_event_series_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'event_series % not found', p_event_series_id;
    END IF;

    UPDATE hackathons
       SET event_series_id = p_event_series_id,
           edition_year = p_edition_year,
           edition_label = p_edition_label,
           updated_at = now()
     WHERE id = p_hackathon_id;

    INSERT INTO event_series_assignment_audit (
        hackathon_id, previous_event_series_id, new_event_series_id,
        previous_edition_label, new_edition_label,
        previous_edition_year, new_edition_year,
        assignment_method, confidence, evidence, model_version,
        actor_user_id, reason
    ) VALUES (
        p_hackathon_id, v_hackathon.event_series_id, p_event_series_id,
        v_hackathon.edition_label, p_edition_label,
        v_hackathon.edition_year, p_edition_year,
        p_assignment_method, p_confidence, COALESCE(p_evidence, '{}'::jsonb), p_model_version,
        p_actor_user_id, p_reason
    ) RETURNING id INTO v_audit_id;

    RETURN v_audit_id;
END;
$$;

CREATE OR REPLACE FUNCTION merge_hackathons_transactional(
    p_survivor_hackathon_id uuid,
    p_retired_hackathon_id uuid,
    p_reason text,
    p_decision_source text,
    p_model_version text DEFAULT NULL,
    p_actor_user_id uuid DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
    v_survivor hackathons%ROWTYPE;
    v_retired hackathons%ROWTYPE;
    v_merge_id uuid;
BEGIN
    IF p_survivor_hackathon_id = p_retired_hackathon_id THEN
        RAISE EXCEPTION 'cannot merge a hackathon into itself';
    END IF;

    -- Stable lock order prevents deadlocks when two merge requests race.
    PERFORM 1 FROM hackathons
     WHERE id IN (p_survivor_hackathon_id, p_retired_hackathon_id)
     ORDER BY id
     FOR UPDATE;

    SELECT * INTO v_survivor FROM hackathons WHERE id = p_survivor_hackathon_id;
    SELECT * INTO v_retired FROM hackathons WHERE id = p_retired_hackathon_id;
    IF v_survivor.id IS NULL OR v_retired.id IS NULL THEN
        RAISE EXCEPTION 'merge requires two existing hackathons';
    END IF;
    IF NOT v_survivor.is_active OR v_survivor.merged_into_id IS NOT NULL THEN
        RAISE EXCEPTION 'survivor must be active and unmerged';
    END IF;
    IF NOT v_retired.is_active OR v_retired.merged_into_id IS NOT NULL THEN
        RAISE EXCEPTION 'retired hackathon must be active and unmerged';
    END IF;

    INSERT INTO merge_audit (
        survivor_hackathon_id, retired_hackathon_id, reason,
        decision_source, model_version, actor_user_id
    ) VALUES (
        p_survivor_hackathon_id, p_retired_hackathon_id, p_reason,
        p_decision_source, p_model_version, p_actor_user_id
    ) RETURNING id INTO v_merge_id;

    INSERT INTO merge_source_ref_moves (
        merge_audit_id, source_event_ref_id, from_hackathon_id, to_hackathon_id
    )
    SELECT v_merge_id, id, p_retired_hackathon_id, p_survivor_hackathon_id
      FROM source_event_refs
     WHERE hackathon_id = p_retired_hackathon_id;

    INSERT INTO merge_observation_moves (
        merge_audit_id, observation_id, from_hackathon_id, to_hackathon_id
    )
    SELECT v_merge_id, id, p_retired_hackathon_id, p_survivor_hackathon_id
      FROM source_observations
     WHERE hackathon_id = p_retired_hackathon_id;

    UPDATE source_event_refs
       SET hackathon_id = p_survivor_hackathon_id
     WHERE hackathon_id = p_retired_hackathon_id;

    UPDATE source_observations
       SET hackathon_id = p_survivor_hackathon_id
     WHERE hackathon_id = p_retired_hackathon_id;

    WITH inserted AS (
        INSERT INTO hackathon_aliases (
            hackathon_id, alias_type, alias_value, normalized_alias, created_by_merge_audit_id
        ) VALUES (
            p_survivor_hackathon_id, 'retired_slug', v_retired.slug, lower(v_retired.slug), v_merge_id
        )
        ON CONFLICT (alias_type, normalized_alias) DO NOTHING
        RETURNING id, alias_type, alias_value
    )
    INSERT INTO merge_alias_changes(merge_audit_id, alias_id, alias_type, alias_value, action)
    SELECT v_merge_id, id, alias_type, alias_value, 'CREATED' FROM inserted;

    WITH inserted AS (
        INSERT INTO hackathon_aliases (
            hackathon_id, alias_type, alias_value, normalized_alias, created_by_merge_audit_id
        )
        SELECT p_survivor_hackathon_id, 'retired_name', v_retired.name, lower(v_retired.normalized_name), v_merge_id
        WHERE v_retired.name IS NOT NULL
        ON CONFLICT (alias_type, normalized_alias) DO NOTHING
        RETURNING id, alias_type, alias_value
    )
    INSERT INTO merge_alias_changes(merge_audit_id, alias_id, alias_type, alias_value, action)
    SELECT v_merge_id, id, alias_type, alias_value, 'CREATED' FROM inserted;

    WITH inserted AS (
        INSERT INTO hackathon_aliases (
            hackathon_id, alias_type, alias_value, normalized_alias, created_by_merge_audit_id
        )
        SELECT p_survivor_hackathon_id, 'retired_official_url', v_retired.official_url,
               lower(regexp_replace(v_retired.official_url, '/+$', '')), v_merge_id
        WHERE v_retired.official_url IS NOT NULL
        ON CONFLICT (alias_type, normalized_alias) DO NOTHING
        RETURNING id, alias_type, alias_value
    )
    INSERT INTO merge_alias_changes(merge_audit_id, alias_id, alias_type, alias_value, action)
    SELECT v_merge_id, id, alias_type, alias_value, 'CREATED' FROM inserted;

    UPDATE hackathons
       SET is_active = false,
           merged_into_id = p_survivor_hackathon_id,
           updated_at = now()
     WHERE id = p_retired_hackathon_id;

    UPDATE hackathons
       SET source_count = (
               SELECT count(DISTINCT COALESCE(source_id::text, id::text))
                 FROM source_event_refs
                WHERE hackathon_id = p_survivor_hackathon_id AND active
           ),
           updated_at = now()
     WHERE id = p_survivor_hackathon_id;

    RETURN v_merge_id;
END;
$$;

CREATE OR REPLACE FUNCTION undo_hackathon_merge(
    p_merge_audit_id uuid,
    p_reversal_note text,
    p_actor_user_id uuid DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_audit merge_audit%ROWTYPE;
    v_expected_refs bigint;
    v_current_refs bigint;
    v_expected_obs bigint;
    v_current_obs bigint;
BEGIN
    SELECT * INTO v_audit
      FROM merge_audit
     WHERE id = p_merge_audit_id
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'merge audit % not found', p_merge_audit_id;
    END IF;
    IF v_audit.reversed_at IS NOT NULL THEN
        RAISE EXCEPTION 'merge % already reversed', p_merge_audit_id;
    END IF;

    PERFORM 1 FROM hackathons
     WHERE id IN (v_audit.survivor_hackathon_id, v_audit.retired_hackathon_id)
     ORDER BY id
     FOR UPDATE;

    IF NOT EXISTS (
        SELECT 1 FROM hackathons
         WHERE id = v_audit.retired_hackathon_id
           AND NOT is_active
           AND merged_into_id = v_audit.survivor_hackathon_id
    ) THEN
        RAISE EXCEPTION 'retired hackathon is no longer in the expected merged state';
    END IF;

    SELECT count(*) INTO v_expected_refs
      FROM merge_source_ref_moves WHERE merge_audit_id = p_merge_audit_id;
    SELECT count(*) INTO v_current_refs
      FROM source_event_refs r
      JOIN merge_source_ref_moves m ON m.source_event_ref_id = r.id
     WHERE m.merge_audit_id = p_merge_audit_id
       AND r.hackathon_id = v_audit.survivor_hackathon_id;
    IF v_expected_refs <> v_current_refs THEN
        RAISE EXCEPTION 'source refs changed after merge; safe automatic reversal refused';
    END IF;

    SELECT count(*) INTO v_expected_obs
      FROM merge_observation_moves WHERE merge_audit_id = p_merge_audit_id;
    SELECT count(*) INTO v_current_obs
      FROM source_observations o
      JOIN merge_observation_moves m ON m.observation_id = o.id
     WHERE m.merge_audit_id = p_merge_audit_id
       AND o.hackathon_id = v_audit.survivor_hackathon_id;
    IF v_expected_obs <> v_current_obs THEN
        RAISE EXCEPTION 'observations changed after merge; safe automatic reversal refused';
    END IF;

    UPDATE source_event_refs r
       SET hackathon_id = m.from_hackathon_id
      FROM merge_source_ref_moves m
     WHERE m.merge_audit_id = p_merge_audit_id
       AND r.id = m.source_event_ref_id;

    UPDATE source_observations o
       SET hackathon_id = m.from_hackathon_id
      FROM merge_observation_moves m
     WHERE m.merge_audit_id = p_merge_audit_id
       AND o.id = m.observation_id;

    DELETE FROM hackathon_aliases a
     USING merge_alias_changes c
     WHERE c.merge_audit_id = p_merge_audit_id
       AND c.alias_id = a.id
       AND a.created_by_merge_audit_id = p_merge_audit_id;

    UPDATE hackathons
       SET is_active = true,
           merged_into_id = NULL,
           source_count = (
               SELECT count(DISTINCT COALESCE(source_id::text, id::text))
                 FROM source_event_refs
                WHERE hackathon_id = v_audit.retired_hackathon_id AND active
           ),
           updated_at = now()
     WHERE id = v_audit.retired_hackathon_id;

    UPDATE hackathons
       SET source_count = (
               SELECT count(DISTINCT COALESCE(source_id::text, id::text))
                 FROM source_event_refs
                WHERE hackathon_id = v_audit.survivor_hackathon_id AND active
           ),
           updated_at = now()
     WHERE id = v_audit.survivor_hackathon_id;

    UPDATE merge_audit
       SET reversed_at = now(),
           reversal_note = p_reversal_note,
           reversed_by_user_id = p_actor_user_id
     WHERE id = p_merge_audit_id;
END;
$$;

CREATE OR REPLACE FUNCTION split_hackathon_merge(
    p_merge_audit_id uuid,
    p_reason text,
    p_actor_user_id uuid DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM undo_hackathon_merge(p_merge_audit_id, p_reason, p_actor_user_id);
END;
$$;

CREATE OR REPLACE FUNCTION enqueue_dedup_candidate_for_review() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.decision = 'REVIEW' THEN
    INSERT INTO review_queue_items(queue_type,entity_type,entity_id,severity,state,payload)
    VALUES('DEDUP','DEDUP_CANDIDATE',NEW.id::text,'MEDIUM','OPEN',jsonb_build_object('left_hackathon_id',NEW.left_hackathon_id,'right_hackathon_id',NEW.right_hackathon_id,'score',NEW.score,'model_version',NEW.model_version,'blocking_reason',NEW.blocking_reason))
    ON CONFLICT DO NOTHING;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS dedup_candidate_review_queue_trg ON dedup_candidates;
CREATE TRIGGER dedup_candidate_review_queue_trg
AFTER INSERT OR UPDATE OF decision ON dedup_candidates
FOR EACH ROW EXECUTE FUNCTION enqueue_dedup_candidate_for_review();
