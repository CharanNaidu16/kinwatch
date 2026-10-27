-- Kinwatch schema v1 (PLAN.md §4.5). Timestamps are epoch seconds (UTC); display in IST.

CREATE TABLE households (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    default_language TEXT NOT NULL DEFAULT 'hi',
    demo_mode INTEGER NOT NULL DEFAULT 0,
    checkin_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE members (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'whatsapp',
    address TEXT NOT NULL,             -- WhatsApp wa_id; never logged or committed
    aliases_json TEXT NOT NULL DEFAULT '[]',
    escalation_order INTEGER NOT NULL DEFAULT 0,
    is_admin INTEGER NOT NULL DEFAULT 0,
    last_inbound_at REAL,              -- for the WhatsApp 24-hour customer service window
    UNIQUE (household_id, channel, address)
);

CREATE TABLE devices (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,   -- sha256 of the device token
    last_seen_at REAL,
    last_frame_shape TEXT
);

CREATE TABLE rules (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    created_by INTEGER NOT NULL REFERENCES members(id),
    original_text TEXT NOT NULL,
    input_kind TEXT NOT NULL DEFAULT 'text',
    transcript TEXT,
    rule_json TEXT NOT NULL,           -- the full validated Rule
    zone_instance INTEGER,
    restatement TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL,
    armed_at REAL
);

CREATE TABLE rule_state (
    rule_id INTEGER PRIMARY KEY REFERENCES rules(id),
    state_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE conversations (
    member_id INTEGER PRIMARY KEY REFERENCES members(id),
    state TEXT NOT NULL DEFAULT 'idle',
    rule_id INTEGER REFERENCES rules(id),
    expires_at REAL,
    data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE events (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES rules(id),
    started_at REAL NOT NULL,
    met_at REAL NOT NULL,
    facts_json TEXT NOT NULL,
    verification_json TEXT,
    status TEXT NOT NULL,              -- sent | suppressed | unverified_sent | cancelled_by_checkin
    snapshot_path TEXT,
    clip_path TEXT
);

CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES events(id),
    text TEXT NOT NULL,
    language TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- open | claimed | expired
    claimed_by INTEGER REFERENCES members(id),
    claimed_at REAL,
    escalation_level INTEGER NOT NULL DEFAULT 0,
    next_escalation_at REAL
);
CREATE INDEX alerts_open ON alerts (status, next_escalation_at);

CREATE TABLE deliveries (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER REFERENCES alerts(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    kind TEXT NOT NULL,                -- confirm | alert | resend | escalation | claim_notice | info
    provider_msg_id TEXT,
    sent_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',  -- accepted | sent | delivered | read | failed
    error TEXT
);
CREATE INDEX deliveries_provider ON deliveries (provider_msg_id);

CREATE TABLE inbound_messages (
    provider_msg_id TEXT PRIMARY KEY,  -- dedupes webhook retries
    channel TEXT NOT NULL,
    member_id INTEGER REFERENCES members(id),
    kind TEXT NOT NULL,
    body TEXT,
    received_at REAL NOT NULL,
    processed_at REAL
);

CREATE TABLE timeline (
    id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    ts REAL NOT NULL,
    rule_id INTEGER REFERENCES rules(id),
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX timeline_ts ON timeline (household_id, ts);

CREATE TABLE api_calls (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    images INTEGER NOT NULL DEFAULT 0,
    video_frames INTEGER NOT NULL DEFAULT 0,
    audio_sec REAL NOT NULL DEFAULT 0,
    est_cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    ok INTEGER NOT NULL,
    error_code TEXT,
    mode TEXT NOT NULL                 -- live | mock
);
CREATE INDEX api_calls_ts ON api_calls (ts);
CREATE INDEX api_calls_model_ts ON api_calls (model, ts);
