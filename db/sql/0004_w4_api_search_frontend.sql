-- W4 API, search and authenticated user foundation.

CREATE TABLE IF NOT EXISTS hackathon_search_documents (
    hackathon_id uuid PRIMARY KEY REFERENCES hackathons(id) ON DELETE CASCADE,
    plain_text text NOT NULL,
    document tsvector NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS hackathon_search_documents_gin_idx ON hackathon_search_documents USING gin(document);

CREATE OR REPLACE FUNCTION refresh_hackathon_search_document(p_hackathon_id uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE v_active boolean;
BEGIN
    SELECT is_active INTO v_active FROM hackathons WHERE id=p_hackathon_id;
    IF v_active IS DISTINCT FROM true THEN
        DELETE FROM hackathon_search_documents WHERE hackathon_id=p_hackathon_id;
        RETURN;
    END IF;
    INSERT INTO hackathon_search_documents(hackathon_id,plain_text,document,indexed_at)
    SELECT h.id,
      concat_ws(' ',h.name,h.subtitle,h.short_description,h.full_description,
        (SELECT string_agg(o.name,' ' ORDER BY o.name) FROM hackathon_organizations ho JOIN organizations o ON o.id=ho.organization_id WHERE ho.hackathon_id=h.id),
        (SELECT string_agg(t.name,' ' ORDER BY t.name) FROM hackathon_tracks ht JOIN tracks t ON t.id=ht.track_id WHERE ht.hackathon_id=h.id),
        (SELECT string_agg(t.name,' ' ORDER BY t.name) FROM hackathon_technologies hx JOIN technologies t ON t.id=hx.technology_id WHERE hx.hackathon_id=h.id)),
      setweight(to_tsvector('simple',coalesce(h.name,'')),'A') ||
      setweight(to_tsvector('simple',coalesce(h.subtitle,'')),'B') ||
      setweight(to_tsvector('simple',coalesce(h.short_description,'')),'B') ||
      setweight(to_tsvector('simple',coalesce(h.full_description,'')),'C') ||
      setweight(to_tsvector('simple',coalesce((SELECT string_agg(o.name,' ') FROM hackathon_organizations ho JOIN organizations o ON o.id=ho.organization_id WHERE ho.hackathon_id=h.id),'')),'A') ||
      setweight(to_tsvector('simple',coalesce((SELECT string_agg(t.name,' ') FROM hackathon_tracks ht JOIN tracks t ON t.id=ht.track_id WHERE ht.hackathon_id=h.id),'')),'A') ||
      setweight(to_tsvector('simple',coalesce((SELECT string_agg(t.name,' ') FROM hackathon_technologies hx JOIN technologies t ON t.id=hx.technology_id WHERE hx.hackathon_id=h.id),'')),'A'),
      now()
    FROM hackathons h WHERE h.id=p_hackathon_id AND h.is_active=true
    ON CONFLICT(hackathon_id) DO UPDATE SET plain_text=EXCLUDED.plain_text,document=EXCLUDED.document,indexed_at=EXCLUDED.indexed_at;
END; $$;

CREATE OR REPLACE FUNCTION rebuild_hackathon_search_documents() RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v_id uuid; v_count integer:=0;
BEGIN
  DELETE FROM hackathon_search_documents sd WHERE NOT EXISTS (SELECT 1 FROM hackathons h WHERE h.id=sd.hackathon_id AND h.is_active=true);
  FOR v_id IN SELECT id FROM hackathons WHERE is_active=true LOOP PERFORM refresh_hackathon_search_document(v_id); v_count:=v_count+1; END LOOP;
  RETURN v_count;
END; $$;

CREATE OR REPLACE FUNCTION refresh_search_for_hackathon_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM refresh_hackathon_search_document(CASE WHEN TG_OP='DELETE' THEN OLD.hackathon_id ELSE NEW.hackathon_id END); RETURN COALESCE(NEW,OLD); END; $$;
CREATE OR REPLACE FUNCTION refresh_search_for_hackathon_row_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN PERFORM refresh_hackathon_search_document(CASE WHEN TG_OP='DELETE' THEN OLD.id ELSE NEW.id END); RETURN COALESCE(NEW,OLD); END; $$;

DROP TRIGGER IF EXISTS w4_hackathon_search_refresh_trg ON hackathons;
CREATE TRIGGER w4_hackathon_search_refresh_trg AFTER INSERT OR UPDATE OF name,subtitle,short_description,full_description,is_active ON hackathons FOR EACH ROW EXECUTE FUNCTION refresh_search_for_hackathon_row_trigger();
DROP TRIGGER IF EXISTS w4_hackathon_org_search_refresh_trg ON hackathon_organizations;
CREATE TRIGGER w4_hackathon_org_search_refresh_trg AFTER INSERT OR UPDATE OR DELETE ON hackathon_organizations FOR EACH ROW EXECUTE FUNCTION refresh_search_for_hackathon_trigger();
DROP TRIGGER IF EXISTS w4_hackathon_track_search_refresh_trg ON hackathon_tracks;
CREATE TRIGGER w4_hackathon_track_search_refresh_trg AFTER INSERT OR UPDATE OR DELETE ON hackathon_tracks FOR EACH ROW EXECUTE FUNCTION refresh_search_for_hackathon_trigger();
DROP TRIGGER IF EXISTS w4_hackathon_tech_search_refresh_trg ON hackathon_technologies;
CREATE TRIGGER w4_hackathon_tech_search_refresh_trg AFTER INSERT OR UPDATE OR DELETE ON hackathon_technologies FOR EACH ROW EXECUTE FUNCTION refresh_search_for_hackathon_trigger();

CREATE TABLE IF NOT EXISTS auth_sessions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 provider auth_provider NOT NULL, token_hash char(64) NOT NULL UNIQUE, csrf_hash char(64) NOT NULL,
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
 revoked_at timestamptz, user_agent_hash char(64), ip_hash char(64), CHECK(expires_at>created_at)
);
CREATE INDEX IF NOT EXISTS auth_sessions_user_active_idx ON auth_sessions(user_id,expires_at DESC) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS auth_sessions_token_active_idx ON auth_sessions(token_hash) WHERE revoked_at IS NULL;
CREATE TABLE IF NOT EXISTS privacy_requests (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 request_type text NOT NULL CHECK(request_type IN ('EXPORT','DELETE')),
 status text NOT NULL DEFAULT 'REQUESTED' CHECK(status IN ('REQUESTED','PROCESSING','COMPLETED','FAILED')),
 requested_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz, metadata jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS privacy_requests_user_time_idx ON privacy_requests(user_id,requested_at DESC);
CREATE INDEX IF NOT EXISTS prize_pools_w4_amount_idx ON prize_pools(hackathon_id,normalized_usd_amount DESC);
CREATE INDEX IF NOT EXISTS eligibility_rules_w4_team_idx ON eligibility_rules(minimum_team_size,maximum_team_size,student_required);
CREATE INDEX IF NOT EXISTS hackathons_w4_active_quality_idx ON hackathons(is_active,quality_score DESC,start_at,id);
CREATE INDEX IF NOT EXISTS hackathons_w4_active_deadline_idx ON hackathons(is_active,registration_close_at,quality_score DESC,id);
SELECT rebuild_hackathon_search_documents();
