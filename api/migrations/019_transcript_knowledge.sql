-- Migration 019: Transcript Knowledge Base
-- Full-text searchable store of all YouTube transcripts
-- Supports Slack: "search knowledge <query>" command

CREATE TABLE IF NOT EXISTS transcript_knowledge (
    id              SERIAL PRIMARY KEY,
    filename        TEXT NOT NULL,
    source          TEXT NOT NULL,        -- 'flip_with_rick' | 'dan_martell'
    title           TEXT NOT NULL,        -- cleaned title from filename
    content         TEXT NOT NULL,        -- full transcript text
    content_tsv     TSVECTOR,             -- full-text search index
    char_count      INTEGER,
    inserted_at     TIMESTAMP DEFAULT NOW()
);

-- Unique on filename so re-runs are safe
CREATE UNIQUE INDEX IF NOT EXISTS idx_tk_filename ON transcript_knowledge(filename);

-- Full-text search index (English stemming)
CREATE INDEX IF NOT EXISTS idx_tk_tsv ON transcript_knowledge USING GIN(content_tsv);

-- Index on source for filtering
CREATE INDEX IF NOT EXISTS idx_tk_source ON transcript_knowledge(source);

-- Auto-update tsvector on insert/update
CREATE OR REPLACE FUNCTION tk_tsv_update() RETURNS TRIGGER AS $$
BEGIN
    NEW.content_tsv := to_tsvector('english', NEW.title || ' ' || NEW.content);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS tk_tsv_trigger ON transcript_knowledge;
CREATE TRIGGER tk_tsv_trigger
    BEFORE INSERT OR UPDATE ON transcript_knowledge
    FOR EACH ROW EXECUTE FUNCTION tk_tsv_update();
