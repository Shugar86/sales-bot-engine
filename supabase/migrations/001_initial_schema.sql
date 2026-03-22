-- Migration: Initial Schema for Sales Bot Engine
-- Enables: User memory, deduplication, vector embeddings, funnel tracking

-- Enable pgvector extension for semantic search
CREATE EXTENSION IF NOT EXISTS vector;

-- ========================================
-- USERS: Core user table per persona
-- ========================================
CREATE TABLE IF NOT EXISTS users (
    user_id          TEXT NOT NULL,
    persona_name     TEXT NOT NULL,
    username         TEXT DEFAULT '',
    display_name     TEXT DEFAULT '',
    first_seen       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_interactions INTEGER DEFAULT 0,
    has_dm           BOOLEAN DEFAULT FALSE,
    funnel_stage     TEXT DEFAULT 'unknown',
    extra            JSONB DEFAULT '{}',
    PRIMARY KEY (user_id, persona_name)
);

CREATE INDEX IF NOT EXISTS idx_users_persona ON users(persona_name);
CREATE INDEX IF NOT EXISTS idx_users_funnel ON users(persona_name, funnel_stage);

-- ========================================
-- USER NOTES: Separate table for notes
-- ========================================
CREATE TABLE IF NOT EXISTS user_notes (
    id       BIGSERIAL PRIMARY KEY,
    user_id  TEXT NOT NULL,
    persona_name TEXT NOT NULL,
    note     TEXT NOT NULL,
    ts       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    FOREIGN KEY (user_id, persona_name) REFERENCES users(user_id, persona_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_notes_user ON user_notes(user_id, persona_name, ts DESC);

-- ========================================
-- RECOMMENDATIONS: Track what was recommended
-- ========================================
CREATE TABLE IF NOT EXISTS recommendations (
    id             BIGSERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL,
    persona_name   TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    ts             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id, persona_name, recommendation),
    FOREIGN KEY (user_id, persona_name) REFERENCES users(user_id, persona_name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_recs_user ON recommendations(user_id, persona_name, ts DESC);

-- ========================================
-- GROUP MESSAGES: Structured group chat messages
-- ========================================
CREATE TABLE IF NOT EXISTS group_messages (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    persona_name TEXT NOT NULL,
    chat_id      TEXT NOT NULL,
    chat_title   TEXT DEFAULT '',
    text         TEXT NOT NULL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gm_chat ON group_messages(persona_name, chat_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_gm_user ON group_messages(user_id, persona_name, ts DESC);

-- ========================================
-- DM SUMMARIES: Text accumulation for DM history
-- ========================================
CREATE TABLE IF NOT EXISTS dm_summaries (
    user_id      TEXT NOT NULL,
    persona_name TEXT NOT NULL,
    summary      TEXT DEFAULT '',
    last_tool    TEXT,
    last_tool_args JSONB DEFAULT '{}',
    PRIMARY KEY (user_id, persona_name),
    FOREIGN KEY (user_id, persona_name) REFERENCES users(user_id, persona_name) ON DELETE CASCADE
);

-- ========================================
-- PROCESSED MESSAGES: Deduplication
-- ========================================
CREATE TABLE IF NOT EXISTS processed_messages (
    message_hash TEXT PRIMARY KEY,
    persona_name TEXT NOT NULL,
    chat_id      TEXT NOT NULL,
    message_id   BIGINT NOT NULL,
    text_preview TEXT,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pm_persona ON processed_messages(persona_name, processed_at DESC);
CREATE INDEX IF NOT EXISTS idx_pm_chat ON processed_messages(chat_id, processed_at DESC);

-- ========================================
-- BOT RESPONSES: Anti-repeat tracking
-- ========================================
CREATE TABLE IF NOT EXISTS bot_responses (
    id              BIGSERIAL PRIMARY KEY,
    persona_name    TEXT NOT NULL,
    chat_id         TEXT NOT NULL,
    response_hash   TEXT NOT NULL,
    response_preview TEXT,
    responded_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_br_chat ON bot_responses(persona_name, chat_id, responded_at DESC);

-- ========================================
-- VECTOR MEMORY: Message embeddings for semantic search
-- ========================================
CREATE TABLE IF NOT EXISTS message_embeddings (
    id           BIGSERIAL PRIMARY KEY,
    persona_name TEXT NOT NULL,
    user_id      TEXT,        -- NULL для групповых сообщений без атрибуции
    chat_id      TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user', 'bot')),
    text         TEXT NOT NULL,
    embedding    vector(1024),  -- deepvk/USER-bge-m3 produces 1024-dim vectors
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- IVF flat index for vector similarity search (filter by persona + user)
CREATE INDEX IF NOT EXISTS idx_emb_persona_user
    ON message_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100)
    WHERE user_id IS NOT NULL;

-- Index for group messages (without user filter)
CREATE INDEX IF NOT EXISTS idx_emb_persona_chat
    ON message_embeddings USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ========================================
-- FUNCTIONS
-- ========================================

-- Semantic search function with filtering
CREATE OR REPLACE FUNCTION match_messages(
    query_embedding vector(1024),
    p_persona_name  TEXT,
    p_user_id       TEXT,
    match_count     INT     DEFAULT 5,
    min_similarity  FLOAT   DEFAULT 0.6
)
RETURNS TABLE (
    text        TEXT,
    role        TEXT,
    similarity  FLOAT,
    ts          TIMESTAMPTZ
) LANGUAGE sql STABLE AS $$
    SELECT
        text,
        role,
        1 - (embedding <=> query_embedding) AS similarity,
        ts
    FROM message_embeddings
    WHERE persona_name = p_persona_name
      AND user_id = p_user_id
      AND 1 - (embedding <=> query_embedding) > min_similarity
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;

-- Semantic search for group chats (no user filter)
CREATE OR REPLACE FUNCTION match_group_messages(
    query_embedding vector(1024),
    p_persona_name  TEXT,
    p_chat_id       TEXT,
    match_count     INT     DEFAULT 5,
    min_similarity  FLOAT   DEFAULT 0.6
)
RETURNS TABLE (
    text        TEXT,
    role        TEXT,
    similarity  FLOAT,
    ts          TIMESTAMPTZ
) LANGUAGE sql STABLE AS $$
    SELECT
        text,
        role,
        1 - (embedding <=> query_embedding) AS similarity,
        ts
    FROM message_embeddings
    WHERE persona_name = p_persona_name
      AND chat_id = p_chat_id
      AND 1 - (embedding <=> query_embedding) > min_similarity
    ORDER BY embedding <=> query_embedding
    LIMIT match_count;
$$;

-- Cleanup old processed messages (run periodically)
CREATE OR REPLACE FUNCTION cleanup_old_processed_messages(max_age_hours INT DEFAULT 48)
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM processed_messages
    WHERE processed_at < NOW() - (max_age_hours || ' hours')::INTERVAL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Cleanup old bot responses (keep last 50 per chat)
CREATE OR REPLACE FUNCTION cleanup_old_bot_responses()
RETURNS INT AS $$
DECLARE
    deleted_count INT;
BEGIN
    DELETE FROM bot_responses br1
    WHERE id IN (
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY persona_name, chat_id ORDER BY responded_at DESC) as rn
            FROM bot_responses
        ) sub
        WHERE rn > 50
    );
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Update user interaction stats
CREATE OR REPLACE FUNCTION update_user_interaction(
    p_user_id TEXT,
    p_persona_name TEXT,
    p_username TEXT DEFAULT '',
    p_display_name TEXT DEFAULT ''
)
RETURNS VOID AS $$
BEGIN
    INSERT INTO users (
        user_id, persona_name, username, display_name,
        first_seen, last_seen, total_interactions
    )
    VALUES (
        p_user_id, p_persona_name, p_username, p_display_name,
        NOW(), NOW(), 1
    )
    ON CONFLICT (user_id, persona_name) DO UPDATE SET
        last_seen = NOW(),
        total_interactions = users.total_interactions + 1,
        username = EXCLUDED.username,
        display_name = EXCLUDED.display_name;
END;
$$ LANGUAGE plpgsql;
