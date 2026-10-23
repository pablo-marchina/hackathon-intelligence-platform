-- Curated textual evidence from explicit, already-authorized source payloads.
-- Unknown amount, age, team size and eligibility booleans stay NULL.
-- The registry does not grant source collection permissions.
INSERT INTO attribute_definitions
    (key, label, category, data_type, description, filterable)
VALUES
    ('participation_requirements', 'Published participation requirements',
     'participation', 'text',
     'Verbatim publisher eligibility statement. Does not imply student, residency, or team-size booleans.',
     true),
    ('prize_details_text', 'Published award information',
     'awards', 'text',
     'Publisher-stated award labels, not an inferred monetary prize pool.',
     true);
