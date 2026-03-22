-- Migration: LangGraph Checkpoint Schema
-- Used by langgraph-checkpoint-postgres AsyncPostgresSaver

-- ========================================
-- CHECKPOINTS: State snapshots for LangGraph
-- ========================================
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

-- Index for listing checkpoints per thread
CREATE INDEX IF NOT EXISTS idx_checkpoints_thread ON checkpoints(thread_id, checkpoint_ns);

-- ========================================
-- WRITES: Individual state writes
-- ========================================
CREATE TABLE IF NOT EXISTS writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    value JSONB,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

-- Index for listing writes per checkpoint
CREATE INDEX IF NOT EXISTS idx_writes_checkpoint ON writes(thread_id, checkpoint_ns, checkpoint_id);

-- Foreign key to ensure data consistency (optional, can be removed for performance)
-- ALTER TABLE writes ADD CONSTRAINT fk_writes_checkpoint
--     FOREIGN KEY (thread_id, checkpoint_ns, checkpoint_id)
--     REFERENCES checkpoints(thread_id, checkpoint_ns, checkpoint_id)
--     ON DELETE CASCADE;

-- ========================================
-- MIGRATION TRACKING: For LangGraph schema updates
-- ========================================
CREATE TABLE IF NOT EXISTS migration_versions (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Track this migration
INSERT INTO migration_versions (version) VALUES ('002_langgraph_checkpoints')
ON CONFLICT DO NOTHING;
