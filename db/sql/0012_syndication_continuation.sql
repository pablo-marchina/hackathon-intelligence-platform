-- Iteration 21: atomic continuation cursor for bounded RSS / sitemap listing pages.
-- No network operations or source activation.
ALTER TABLE source_url_backlog
    ADD COLUMN listing_offset integer NOT NULL DEFAULT 0 CHECK (listing_offset >= 0),
    ADD COLUMN listing_hash text;
