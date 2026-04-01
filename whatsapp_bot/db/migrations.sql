-- Neuronaq WhatsApp Trading Bot — Supabase Schema
-- Run this in the Supabase SQL Editor to create all tables.

-- ============================================================
-- USERS
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    whatsapp_number VARCHAR(20) UNIQUE NOT NULL,       -- E.164 format
    name            VARCHAR(100),
    role            VARCHAR(10) NOT NULL DEFAULT 'trader'
                        CHECK (role IN ('admin', 'trader')),
    risk_tolerance  VARCHAR(15) DEFAULT 'moderate'
                        CHECK (risk_tolerance IN ('conservative', 'moderate', 'aggressive')),
    yield_target_pct    FLOAT DEFAULT 10.0,            -- monthly target %
    max_drawdown_pct    FLOAT DEFAULT -10.0,           -- monthly max loss %
    trading_style       VARCHAR(10) DEFAULT 'active'
                        CHECK (trading_style IN ('active', 'patient')),
    pairs_blacklist     JSONB DEFAULT '[]'::jsonb,     -- coins user excludes
    automation_mode     VARCHAR(10) DEFAULT 'paper'
                        CHECK (automation_mode IN ('paper', 'live')),
    notifications_config JSONB DEFAULT '{
        "daily": true,
        "weekly": true,
        "monthly": true,
        "macro_alerts": true,
        "trade_notifications": true,
        "summary_time": "08:00"
    }'::jsonb,
    onboarding_step         VARCHAR(30),               -- tracks interview progress
    onboarded_at            TIMESTAMPTZ,               -- null until complete
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- STRATEGIES (one active per user)
-- ============================================================
CREATE TABLE IF NOT EXISTS strategies (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    strategy_name   VARCHAR(50) NOT NULL,
    stoploss_pct    FLOAT NOT NULL DEFAULT -5.0,
    max_open_trades INTEGER NOT NULL DEFAULT 3,
    timeframe       VARCHAR(5) NOT NULL DEFAULT '5m',
    roi_config      JSONB DEFAULT '{"0": 0.05}'::jsonb,
    activated_at    TIMESTAMPTZ DEFAULT NOW(),
    activated_by    VARCHAR(10) DEFAULT 'bot'
                        CHECK (activated_by IN ('user', 'bot')),
    reason          TEXT,                              -- why chosen / adjusted
    is_active       BOOLEAN DEFAULT true
);

-- Only one active strategy per user
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_strategy
    ON strategies (user_id) WHERE is_active = true;

-- ============================================================
-- MACRO EVENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS macro_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scanned_at              TIMESTAMPTZ DEFAULT NOW(),
    headlines               JSONB DEFAULT '[]'::jsonb,
    fear_greed_score        INTEGER,
    significance_triggered  BOOLEAN DEFAULT false,
    claude_recommendation   TEXT,
    alert_sent              BOOLEAN DEFAULT false,
    user_response           VARCHAR(15)
                                CHECK (user_response IN ('agree', 'disagree', 'discussion', NULL)),
    final_action_taken      TEXT
);

-- ============================================================
-- CONVERSATIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        VARCHAR(10) NOT NULL CHECK (role IN ('user', 'assistant')),
    message     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_time
    ON conversations (user_id, created_at DESC);

-- ============================================================
-- TRADE LOGS (mirror of Freqtrade trades, per-user)
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    trade_id    INTEGER,
    pair        VARCHAR(20),
    action      VARCHAR(15),
    amount      DECIMAL,
    price       DECIMAL,
    profit      DECIMAL,
    strategy    VARCHAR(50),
    triggered_via VARCHAR(20) DEFAULT 'bot',
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_trade_logs_user_time
    ON trade_logs (user_id, created_at DESC);

-- ============================================================
-- Updated_at trigger
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
