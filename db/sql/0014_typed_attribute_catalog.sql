-- Reuse source_observations + canonical_field_selections as the evidence and
-- selected-value store. This registry only describes curated, searchable keys.
-- A source-provided arbitrary metadata key is never promoted automatically.
CREATE TABLE attribute_definitions (
    key text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{0,62}$'),
    label text NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 120),
    category text NOT NULL DEFAULT 'other',
    data_type text NOT NULL CHECK (data_type IN ('text','number','boolean')),
    description text NOT NULL DEFAULT '',
    unit text,
    filterable boolean NOT NULL DEFAULT false,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX attribute_definitions_filterable_idx
    ON attribute_definitions(key) WHERE filterable AND active;

-- These are already projected by project_structured_metadata for authorized
-- schema.org/Event inputs; their source values retain existing provenance.
INSERT INTO attribute_definitions
    (key, label, category, data_type, description, unit, filterable)
VALUES
    ('language', 'Event language', 'event', 'text',
     'Language explicitly declared in the event page', NULL, true),
    ('capacity', 'Maximum attendees', 'participation', 'number',
     'Published maximum attendee capacity (not team size)', 'attendees', true),
    ('source_event_status', 'Official event status', 'event', 'text',
     'Raw structured event status; not inferred from deadlines', NULL, true);
