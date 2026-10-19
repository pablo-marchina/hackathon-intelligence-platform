-- W5 recommendation + alerts hardening
ALTER TABLE user_preferences
  ADD COLUMN IF NOT EXISTS version integer NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS snapshot_hash char(64);

CREATE TABLE IF NOT EXISTS user_preference_versions (
  id bigserial PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  version integer NOT NULL,
  snapshot_hash char(64) NOT NULL,
  snapshot jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, version)
);
CREATE INDEX IF NOT EXISTS user_preference_versions_user_time_idx
  ON user_preference_versions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS user_preference_versions_hash_idx
  ON user_preference_versions(user_id, snapshot_hash);

ALTER TABLE recommendation_scores
  ADD COLUMN IF NOT EXISTS input_hash char(64),
  ADD COLUMN IF NOT EXISTS profile_version integer,
  ADD COLUMN IF NOT EXISTS event_snapshot_hash char(64),
  ADD COLUMN IF NOT EXISTS components jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE UNIQUE INDEX IF NOT EXISTS recommendation_input_uniq
  ON recommendation_scores(user_id, hackathon_id, model_version, input_hash)
  WHERE input_hash IS NOT NULL;

ALTER TABLE saved_searches
  ADD COLUMN IF NOT EXISTS definition_hash char(64);
CREATE INDEX IF NOT EXISTS saved_searches_definition_hash_idx
  ON saved_searches(user_id, definition_hash);

CREATE TABLE IF NOT EXISTS alert_channel_preferences (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  channel alert_channel NOT NULL,
  alert_type text NOT NULL DEFAULT '*',
  enabled boolean NOT NULL DEFAULT true,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id, channel, alert_type)
);

ALTER TABLE alert_deliveries
  ADD COLUMN IF NOT EXISTS provider_response jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 4;

CREATE INDEX IF NOT EXISTS alert_delivery_retry_idx
  ON alert_deliveries(status, next_attempt_at)
  WHERE status IN ('PENDING','FAILED');
