-- OpenCode SQLite schema for sandbox testing
-- Run this to create a test OpenCode database

CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    title TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    directory TEXT,
    parent_id TEXT,
    project_id TEXT,
    workspace_id TEXT,
    slug TEXT,
    version TEXT,
    share_url TEXT,
    permission TEXT,
    summary_additions INTEGER DEFAULT 0,
    summary_deletions INTEGER DEFAULT 0,
    summary_files INTEGER DEFAULT 0,
    summary_diffs TEXT,
    revert TEXT
);

CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    time_created INTEGER,
    data TEXT
);

CREATE TABLE IF NOT EXISTS part (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    time_created INTEGER,
    data TEXT
);

-- Sample session data
INSERT INTO session VALUES (
    'fixture-session-001',
    'Sample OpenCode Session',
    1742120400000,  -- 2026-03-16T12:00:00Z in ms
    1742120460000,
    '/tmp/test-project',
    NULL,
    'proj-fixture',
    'ws-fixture',
    'fixture',
    '1',
    '',
    '',
    0, 0, 0, NULL, NULL
);

-- Sample message
INSERT INTO message VALUES (
    'msg-fixture-001',
    'fixture-session-001',
    1742120401000,
    '{"role":"user","time":{"created":1742120401000}}'
);

-- Sample text part
INSERT INTO part VALUES (
    'part-fixture-001',
    'msg-fixture-001',
    1742120401000,
    '{"type":"text","text":"What files are in this project?"}'
);
