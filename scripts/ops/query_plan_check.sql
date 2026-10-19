-- Run after loading production-like data. Each query should be inspected with EXPLAIN (ANALYZE, BUFFERS).
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, slug, name
FROM hackathons
WHERE is_active = true
  AND status IN ('REGISTRATION_OPEN', 'UPCOMING')
ORDER BY registration_close_at ASC NULLS LAST, quality_score DESC NULLS LAST
LIMIT 50;

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, slug, name
FROM hackathons
WHERE is_active = true
  AND search_text @@ websearch_to_tsquery('simple', 'AI agents')
ORDER BY ts_rank_cd(search_text, websearch_to_tsquery('simple', 'AI agents')) DESC
LIMIT 50;
