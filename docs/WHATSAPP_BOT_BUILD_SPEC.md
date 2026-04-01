# Neuronaq WhatsApp Trading Bot — Complete Build Specification

**Version:** 1.0
**Date:** March 2026
**Company:** Neuronaq
**Status:** In Development — Phase 1 Complete

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Product Overview](#2-product-overview)
3. [Architecture](#3-architecture)
4. [Tech Stack](#4-tech-stack)
5. [Codebase Structure](#5-codebase-structure)
6. [Database Schema](#6-database-schema)
7. [Feature Specification](#7-feature-specification)
8. [Freqtrade Integration](#8-freqtrade-integration)
9. [AI Engine (GPT-4o)](#9-ai-engine-gpt-4o)
10. [Trade Compliance & Audit Trail](#10-trade-compliance--audit-trail)
11. [Error Handling & Resilience](#11-error-handling--resilience)
12. [Security](#12-security)
13. [Build Sequence & Progress](#13-build-sequence--progress)
14. [Environment Variables](#14-environment-variables)
15. [Deployment](#15-deployment)
16. [API Contracts](#16-api-contracts)
17. [Sample Conversations](#17-sample-conversations)
18. [Roadmap](#18-roadmap)
19. [MVP Success Criteria](#19-mvp-success-criteria)

---

## 1. Executive Summary

The Neuronaq WhatsApp Trading Bot is an AI-powered crypto trading assistant operating natively inside WhatsApp. It combines:

- **Technical execution** via Freqtrade connected to KuCoin exchange
- **Natural language understanding** and strategy reasoning via GPT-4o (OpenAI)
- **Macro intelligence** via scheduled news scanning and proactive alerts
- **Insurance-grade audit trail** for trade compliance verification (post-MVP)

Users onboard via a conversational interview, agree on a strategy, and the bot runs fully autonomously — sending daily reports, proactive macro alerts, and allowing on-demand queries. Every trade from the audit phase onward carries cryptographic proof of strategy compliance.

### MVP Goal

Ship a working, trustworthy trading bot that a real user can onboard onto, agree a strategy with, activate in paper or live mode, and receive intelligent daily updates and macro alerts — entirely through WhatsApp plus one settings webpage.

---

## 2. Product Overview

### 2.1 Automation Modes

| Mode | Description | Status |
|------|-------------|--------|
| Full Automation | Bot executes trades per agreed strategy. User receives reports and macro alerts. | MVP |
| Paper Trading | Identical to full automation but simulated. No real money. Default for new users. | MVP |
| Semi Automation | Bot messages user before each trade. User confirms via WhatsApp. | v1.1 |
| No Automation | Bot sends signals only. User executes manually on exchange. | v1.1 |

### 2.2 Core User Journey

1. User scans WhatsApp QR code or clicks link → starts conversation
2. Bot runs 6-question strategy onboarding interview
3. AI recommends a strategy with explanation → user confirms or negotiates
4. User selects Paper or Live mode
5. Bot activates strategy via Freqtrade API
6. Bot sends daily summaries, portfolio updates on demand, and proactive macro alerts
7. User can query bot anytime about portfolio, market conditions, or macro events
8. If macro event triggers threshold → bot proactively messages with proposed strategy adjustment

---

## 3. Architecture

```
WhatsApp Users (Multi-user)
        │
        ▼
Meta Cloud API (Free Tier)
        │
        ▼
┌───────────────────────────────────────────────────┐
│           FastAPI Server (Python, Async)           │
│                                                   │
│  ┌─────────────┐  ┌───────────────────────────┐  │
│  │  Webhook     │  │    Message Router          │  │
│  │  Handler     │──│  (onboarding vs active)    │  │
│  └─────────────┘  └──────────┬────────────────┘  │
│                              │                    │
│  ┌───────────────────────────┼────────────────┐  │
│  │                           ▼                │  │
│  │  ┌──────────────┐  ┌──────────────┐       │  │
│  │  │ GPT-4o AI    │  │ Freqtrade    │       │  │
│  │  │ Engine       │  │ API Client   │       │  │
│  │  │ (NLU + Tool  │  │ (JWT Auth)   │       │  │
│  │  │  Calling)    │  └──────┬───────┘       │  │
│  │  └──────┬───────┘         │               │  │
│  │         │                 │               │  │
│  │  ┌──────▼───────┐  ┌─────▼────────┐      │  │
│  │  │ Macro Intel  │  │ Strategy     │      │  │
│  │  │ (CryptoPanic │  │ Manager      │      │  │
│  │  │  NewsAPI     │  │ (CRUD +      │      │  │
│  │  │  Fear&Greed) │  │  Freqtrade)  │      │  │
│  │  └──────────────┘  └──────────────┘      │  │
│  │                                           │  │
│  │  ┌──────────────┐  ┌──────────────┐      │  │
│  │  │ Market       │  │ Chart        │      │  │
│  │  │ Analysis     │  │ Generator    │      │  │
│  │  │ (CCXT)       │  │ (matplotlib) │      │  │
│  │  └──────────────┘  └──────────────┘      │  │
│  │                                           │  │
│  │  ┌──────────────┐  ┌──────────────┐      │  │
│  │  │ User Memory  │  │ Encryption   │      │  │
│  │  │ (Context)    │  │ (AES-256)    │      │  │
│  │  └──────────────┘  └──────────────┘      │  │
│  │              Core Services                │  │
│  └───────────────────────────────────────────┘  │
│                      │                           │
│  ┌───────────────────▼───────────────────────┐  │
│  │         APScheduler (Background Jobs)      │  │
│  │  • Daily summary    (20:00 UTC daily)      │  │
│  │  • Weekly summary   (08:00 UTC Monday)     │  │
│  │  • Monthly summary  (08:00 UTC 1st)        │  │
│  │  • Macro scan       (every 6 hours)        │  │
│  └───────────────────────────────────────────┘  │
└───────────────────────────────────────────────────┘
        │                         │
        ▼                         ▼
┌───────────────┐       ┌─────────────────┐
│   Supabase    │       │  Freqtrade Bot  │
│  (PostgreSQL) │       │  REST API :8080 │
│               │       │                 │
│  • users      │       │  • KuCoin       │
│  • strategies │       │  • 5 Strategies │
│  • convos     │       │  • SQLite DB    │
│  • macro      │       │  • FreqUI       │
│  • trade_logs │       └─────────────────┘
│  • audit*     │
└───────────────┘

* Audit tables added in Phase 7 (post-MVP)
```

---

## 4. Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| WhatsApp Interface | Meta Cloud API (free tier) | Message ingestion, delivery, media sending |
| Bot Server | FastAPI (Python, async) | Webhook handler, message router, scheduled jobs |
| AI Brain | OpenAI GPT-4o (OpenAI SDK) | NLU, strategy reasoning, macro analysis, recommendations |
| Database | Supabase (PostgreSQL, free tier) | Users, conversations, memory, strategy configs, trade logs |
| Chart Generation | matplotlib → PNG → WhatsApp Media API | P&L charts, candlestick charts, portfolio snapshots |
| Market Data | CCXT (KuCoin) + TA-Lib | Live prices, technical indicators |
| Macro Data | CryptoPanic API + NewsAPI + Fear & Greed API | News headlines, sentiment scores |
| Trading Engine | Freqtrade REST API | Strategy execution, KuCoin integration, dry_run mode |
| Settings Frontend | Next.js + Tailwind CSS | Multi-page trading dashboard with auth (7 pages) |
| Encryption | AES-256-GCM (cryptography lib) | KuCoin API key storage at rest |
| Deployment | Render.com | Bot server + Freqtrade co-deployed as separate services |

---

## 5. Codebase Structure

### 5.1 WhatsApp Bot Server (Built — Phase 1 Complete)

```
whatsapp_bot/
├── __init__.py
├── app.py                        # FastAPI entry, webhook routes, scheduler lifecycle
├── config.py                     # All env vars via pydantic Settings
├── requirements.txt              # Python dependencies
├── Dockerfile                    # Render deployment container
│
├── core/
│   ├── __init__.py
│   ├── whatsapp.py               # Meta Cloud API client
│   │                             # - send_text(), send_image(), send_image_bytes()
│   │                             # - send_interactive_buttons(), send_interactive_list()
│   │                             # - extract_message() — parse webhook payloads
│   │                             # - 3x retry with exponential backoff on all sends
│   ├── message_router.py         # Route messages based on user state
│   │                             # - New user → onboarding handler
│   │                             # - Active user → GPT-4o AI engine with tool execution
│   │                             # - Tool executor bridges GPT-4o → Freqtrade/Market/Strategy
│   └── auth.py                   # get_or_create_user(), is_admin(), is_onboarded()
│
├── services/
│   ├── __init__.py
│   ├── freqtrade_client.py       # Freqtrade REST API wrapper
│   │                             # - JWT auth with auto-refresh on 401
│   │                             # - get_status(), get_profit(), get_balance(), get_trades()
│   │                             # - force_entry(), force_exit()
│   │                             # - start(), stop(), reload_config(), ping()
│   ├── claude_engine.py          # OpenAI GPT-4o with 9 tool definitions
│   │                             # - chat() — full conversation with tool use loop
│   │                             # - recommend_strategy() — onboarding recommendation
│   │                             # - analyze_macro() — macro event analysis
│   │                             # - Tools: get_bot_status, get_profit_summary, get_balance,
│   │                             #   get_trade_history, force_entry, force_exit,
│   │                             #   switch_strategy, update_strategy_params,
│   │                             #   get_market_price, analyze_trend
│   ├── market_analysis.py        # Live prices + technical indicators via CCXT
│   │                             # - get_price() — current price + 24h stats
│   │                             # - analyze() — RSI, EMA(50/200), BB, volume analysis
│   │                             # - Pure Python indicator calculations (no TA-Lib dependency)
│   ├── strategy_manager.py       # Strategy CRUD + Freqtrade config push
│   │                             # - switch_strategy() — save to DB + reload Freqtrade
│   │                             # - update_params() — modify stoploss/trades/timeframe
│   │                             # - Default params per strategy stored in code
│   ├── chart_generator.py        # Dark-themed chart rendering
│   │                             # - generate_candlestick_chart() — OHLCV with BB/EMA overlay
│   │                             # - generate_pnl_chart() — daily + cumulative P&L
│   │                             # - Returns PNG bytes for WhatsApp media upload
│   ├── macro_intelligence.py     # External macro data fetching
│   │                             # - fetch_crypto_headlines() — CryptoPanic API
│   │                             # - fetch_fear_greed() — Alternative.me API
│   │                             # - fetch_macro_headlines() — NewsAPI
│   │                             # - scan() — combined scan with significance threshold
│   ├── user_memory.py            # Per-user context manager
│   │                             # - Loads strategy, recent messages for AI context
│   │                             # - Saves both sides of conversation turns
│   └── encryption.py             # AES-256-GCM encryption/decryption
│                                 # - encrypt() / decrypt() for KuCoin API keys
│
├── handlers/
│   ├── __init__.py
│   ├── onboarding.py             # 6-question strategy interview state machine
│   │                             # Steps: ask_name → ask_yield_target → ask_max_drawdown
│   │                             #   → ask_trading_style → ask_pairs_blacklist
│   │                             #   → ask_automation_mode → recommend_strategy
│   │                             #   → confirm_strategy → activate
│   │                             # Uses WhatsApp interactive buttons for choices
│   │                             # GPT-4o generates personalized strategy recommendation
│   ├── trading.py                # get_status_summary(), get_portfolio_summary()
│   ├── reporting.py              # Reporting helpers (main logic in jobs/)
│   ├── macro.py                  # Macro query + alert response handling
│   ├── strategy.py               # Strategy management (routed via AI tools)
│   └── general.py                # Help message, greeting responses
│
├── jobs/
│   ├── __init__.py
│   ├── scheduler.py              # APScheduler with 4 registered jobs
│   ├── daily_summary.py          # Sends P&L summary to all subscribed users
│   ├── weekly_summary.py         # Monday morning: 7-day performance report
│   ├── monthly_summary.py        # 1st of month: cumulative performance review
│   └── macro_scan.py             # Every 6 hours: headlines + F&G → GPT-4o analysis
│                                 # → proactive WhatsApp alerts if significant
│
├── db/
│   ├── __init__.py
│   ├── supabase_client.py        # Full CRUD for all tables
│   │                             # - Users: get_by_phone, create, update, get_with_notifications
│   │                             # - Strategies: get_active, create (deactivates old), update
│   │                             # - Conversations: save_message, get_recent (last 20)
│   │                             # - Macro: save_event, get_latest, update
│   │                             # - Trade logs: save, get by user
│   ├── models.py                 # Pydantic models: User, Strategy, ConversationMessage,
│   │                             #   MacroEvent, TradeLog, OnboardingAnswers
│   └── migrations.sql            # Complete Supabase SQL schema
│                                 # - 5 tables with indexes and constraints
│                                 # - updated_at trigger on users table
│                                 # - Unique constraint: one active strategy per user
│
└── utils/
    ├── __init__.py
    ├── formatters.py             # WhatsApp-friendly message formatting
    │                             # - format_trade_notification() — open/close events
    │                             # - format_open_trades() — summary list
    │                             # - format_daily_summary() — daily P&L
    │                             # - format_portfolio() — holdings breakdown
    │                             # - format_strategy_info() — strategy details
    │                             # - format_macro_alert() — macro event alert
    ├── retry.py                  # @async_retry decorator — exponential backoff
    └── error_handler.py          # Error classification + WhatsApp notification
                                  # - classify_error() — maps exceptions to categories
                                  # - handle_error() — log + send alert to user
                                  # - Categories: freqtrade_unreachable, kucoin_api_error,
                                  #   ai_timeout, webhook_delivery_failed, unknown
```

### 5.2 Web Dashboard (Phase 5 — In Development)

```
settings_web/
├── package.json                          # Next.js 16, React 19, Tailwind v4, recharts, lightweight-charts, lucide-react
├── tsconfig.json
├── src/
│   ├── app/
│   │   ├── layout.tsx                    # Sidebar + TopBar shell, AuthProvider wrapper
│   │   ├── page.tsx                      # Dashboard (landing page after login)
│   │   ├── login/page.tsx                # Phone OTP login (Supabase Auth)
│   │   ├── trades/page.tsx               # Open positions + trade history
│   │   ├── strategies/page.tsx           # Strategy gallery + builder
│   │   ├── market/page.tsx               # Live prices, charts, indicators, macro
│   │   ├── settings/page.tsx             # Profile, strategy params, API keys, notifications
│   │   ├── logs/page.tsx                 # Conversations, macro scans, errors
│   │   └── api/
│   │       ├── freqtrade/[...path]/route.ts  # Catch-all Freqtrade proxy (server-side JWT)
│   │       ├── market/prices/route.ts    # KuCoin prices for 4 pairs
│   │       ├── market/analysis/route.ts  # OHLCV + indicator calculation
│   │       └── macro/route.ts            # CryptoPanic + Fear & Greed combined
│   ├── components/
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx               # Nav: Dashboard, Trades, Strategies, Market, Settings, Logs
│   │   │   ├── TopBar.tsx                # User name, bot mode badge, sign out
│   │   │   └── MobileNav.tsx             # Bottom tab bar for mobile
│   │   ├── dashboard/
│   │   │   ├── BotStatusCard.tsx         # Running/stopped, paper/live, start/stop
│   │   │   ├── PnLCard.tsx              # Today's P&L with sparkline
│   │   │   ├── BalanceCard.tsx          # Total USDT balance, per-coin
│   │   │   ├── OpenTradesCard.tsx       # Top 5 open positions
│   │   │   ├── RecentTradesTable.tsx    # Last 10 closed trades
│   │   │   ├── MarketSentiment.tsx      # Fear & Greed + headlines
│   │   │   └── QuickActions.tsx         # Action buttons
│   │   ├── trades/
│   │   │   ├── OpenPositions.tsx         # Live table with force-exit
│   │   │   ├── TradeHistory.tsx          # Filterable history table
│   │   │   └── TradeStats.tsx            # Win rate, avg profit, totals
│   │   ├── strategies/
│   │   │   ├── StrategyCard.tsx          # Strategy summary card
│   │   │   ├── StrategyDetail.tsx        # Active strategy expanded view
│   │   │   ├── StrategyBuilder.tsx       # Form-based custom strategy builder
│   │   │   ├── ConditionRow.tsx          # Entry/exit condition row
│   │   │   └── StrategyCompare.tsx       # Side-by-side comparison
│   │   ├── market/
│   │   │   ├── PriceCard.tsx             # Per-pair price card
│   │   │   ├── PriceChart.tsx            # Candlestick chart (lightweight-charts)
│   │   │   ├── IndicatorPanel.tsx        # RSI, EMA, BB values
│   │   │   ├── MacroFeed.tsx             # News headlines
│   │   │   └── FearGreedGauge.tsx        # Semicircular gauge (0-100)
│   │   ├── settings/
│   │   │   ├── ProfileSection.tsx        # Name, risk, yield, drawdown, pairs, mode
│   │   │   ├── StrategySection.tsx       # Strategy params editor
│   │   │   ├── ApiKeysSection.tsx        # KuCoin key management
│   │   │   └── NotificationsSection.tsx  # Notification toggles
│   │   ├── logs/
│   │   │   ├── ConversationLog.tsx       # WhatsApp chat history (bubble UI)
│   │   │   ├── MacroScanLog.tsx          # Macro scan results table
│   │   │   └── ErrorLog.tsx              # Bot error log
│   │   └── shared/
│   │       ├── Card.tsx                  # Reusable card wrapper
│   │       ├── Badge.tsx                 # Status badges
│   │       ├── Toggle.tsx                # Toggle switch
│   │       └── DataTable.tsx             # Sortable data table
│   └── lib/
│       ├── supabase.ts                   # Supabase client + query functions
│       ├── api.ts                        # Freqtrade/market/macro fetch functions
│       ├── types.ts                      # Centralized TypeScript types
│       ├── strategy-data.ts              # 5 strategy defaults as constants
│       └── hooks/
│           ├── useAuth.tsx               # Supabase Auth context + phone OTP
│           ├── useFreqtrade.ts           # Polling hook for bot status/trades
│           └── useMarket.ts              # Polling hook for prices
```

### 5.3 Existing Freqtrade Files (Reference)

```
freqtrade.io/
├── config/
│   ├── config.json               # Main config (local, has real API keys — DO NOT COMMIT)
│   └── config.render.json        # Render deploy config (env var references)
├── strategies/
│   ├── ConservativeRSI.py        # Low risk, RSI < 30 + 200 EMA, -5% SL, 5m
│   ├── EMACrossover.py           # Low-medium, EMA golden cross, -7% SL, 15m
│   ├── BollingerBreakout.py      # Medium, BB + Stochastic, -8% SL, 5m
│   ├── ActiveTrader.py           # Medium-high, 1m fast signals, -3% SL
│   └── MeanReversionScalper.py   # Medium, BB + RSI scalping, -0.4% SL, 5m (ACTIVE)
├── render_start.py               # Render deployment startup (config generation)
├── Dockerfile                    # Docker build with TA-Lib compilation
├── requirements.txt              # Core Freqtrade dependencies
└── user_data/
    └── tradesv3.sqlite           # Trade database (SQLite)
```

---

## 6. Database Schema

### 6.1 MVP Tables (Supabase)

#### `users`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | Auto-generated |
| whatsapp_number | VARCHAR(20) UNIQUE | E.164 format (+1234567890) |
| name | VARCHAR(100) | From onboarding interview |
| role | VARCHAR(10) | `admin` or `trader` |
| risk_tolerance | VARCHAR(15) | `conservative` / `moderate` / `aggressive` |
| yield_target_pct | FLOAT | Monthly return target (e.g. 10.0) |
| max_drawdown_pct | FLOAT | Max monthly loss (e.g. -10.0) |
| trading_style | VARCHAR(10) | `active` or `patient` |
| pairs_blacklist | JSONB | Excluded pairs (e.g. `["ADA/USDT"]`) |
| automation_mode | VARCHAR(10) | `paper` or `live` |
| notifications_config | JSONB | `{daily, weekly, monthly, macro_alerts, trade_notifications, summary_time}` |
| kucoin_api_key_enc | TEXT | AES-256-GCM encrypted API key |
| kucoin_api_secret_enc | TEXT | AES-256-GCM encrypted API secret |
| kucoin_api_password_enc | TEXT | AES-256-GCM encrypted API password |
| onboarding_step | VARCHAR(30) | Current step in interview (null when complete) |
| onboarded_at | TIMESTAMPTZ | Null until onboarding complete |
| created_at | TIMESTAMPTZ | Auto |
| updated_at | TIMESTAMPTZ | Auto-updated via trigger |

#### `strategies`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| strategy_name | VARCHAR(50) | e.g. `ConservativeRSI` |
| stoploss_pct | FLOAT | e.g. -5.0 |
| max_open_trades | INTEGER | |
| timeframe | VARCHAR(5) | e.g. `5m`, `15m`, `1h` |
| roi_config | JSONB | Take profit schedule (e.g. `{"0": 0.05, "30": 0.03}`) |
| activated_at | TIMESTAMPTZ | |
| activated_by | VARCHAR(10) | `user` or `bot` |
| reason | TEXT | Why this strategy was chosen or adjusted |
| is_active | BOOLEAN | Unique constraint: only one active per user |

#### `conversations`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| role | VARCHAR(10) | `user` or `assistant` |
| message | TEXT | Message content |
| metadata | JSONB | Optional context (e.g. `{type: "macro_alert"}`) |
| created_at | TIMESTAMPTZ | Indexed DESC for recent message queries |

#### `macro_events`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| scanned_at | TIMESTAMPTZ | |
| headlines | JSONB | Array of headline strings |
| fear_greed_score | INTEGER | 0-100 |
| significance_triggered | BOOLEAN | Did this scan cross the alert threshold? |
| claude_recommendation | TEXT | Full text of AI's analysis (column name kept for compatibility) |
| alert_sent | BOOLEAN | Was an alert sent to users? |
| user_response | VARCHAR(15) | `agree` / `disagree` / `discussion` / null |
| final_action_taken | TEXT | What was changed, if anything |

#### `trade_logs`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| trade_id | INTEGER | Freqtrade trade ID |
| pair | VARCHAR(20) | |
| action | VARCHAR(15) | `buy`, `sell`, `force_buy`, `force_sell` |
| amount | DECIMAL | |
| price | DECIMAL | |
| profit | DECIMAL | |
| strategy | VARCHAR(50) | |
| triggered_via | VARCHAR(20) | `bot`, `whatsapp`, `settings_page` |
| created_at | TIMESTAMPTZ | |

### 6.2 Audit Tables (Phase 7 — Post-MVP)

#### `trade_audit_logs`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| trade_id | INTEGER | Freqtrade trade ID |
| user_id | UUID FK → users | |
| signal_source | VARCHAR(20) | `strategy` / `force_entry` / `macro_adjustment` |
| strategy_name | VARCHAR(50) | |
| strategy_file_hash | VARCHAR(64) | SHA-256 of strategy .py file at execution time |
| indicator_snapshot | JSONB | All indicator values at entry (RSI, EMA, BB, volume) |
| conditions_checked | JSONB | Array of `{condition, result, values}` |
| all_conditions_met | BOOLEAN | Were ALL strategy entry conditions satisfied? |
| candle_data | JSONB | OHLCV window used for signal generation |
| ohlcv_hash | VARCHAR(64) | SHA-256 of candle data for integrity |
| signature | VARCHAR(128) | HMAC-SHA256 of the full snapshot |
| created_at | TIMESTAMPTZ | Immutable |

#### `strategy_versions`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| user_id | UUID FK → users | |
| strategy_name | VARCHAR(50) | |
| file_hash | VARCHAR(64) | SHA-256 of strategy .py file |
| config_hash | VARCHAR(64) | SHA-256 of strategy parameters |
| parameters | JSONB | Full snapshot of all params |
| changed_by | VARCHAR(20) | `user` / `bot` / `admin` / `macro_alert` |
| change_reason | TEXT | |
| agreed_by_user | BOOLEAN | Did user explicitly confirm this version? |
| agreed_at | TIMESTAMPTZ | |
| created_at | TIMESTAMPTZ | |

#### `compliance_events`
| Column | Type | Description |
|--------|------|-------------|
| id | UUID PK | |
| event_type | VARCHAR(30) | `strategy_agreed` / `trade_opened` / `trade_closed` / `strategy_changed` / `force_override` |
| user_id | UUID FK → users | |
| trade_id | INTEGER | |
| details | JSONB | Event-specific data |
| signature | VARCHAR(128) | HMAC-SHA256 |
| created_at | TIMESTAMPTZ | Append-only (RLS policy prevents UPDATE/DELETE) |

---

## 7. Feature Specification

### 7.1 Strategy Onboarding (6-Question Interview)

| # | Question | Purpose | Input Type |
|---|----------|---------|------------|
| 1 | What's your name? | Personalization | Free text |
| 2 | Monthly yield target? | Return expectation | Buttons: ~5% / ~10% / 20%+ |
| 3 | Max monthly drawdown? | Risk floor | Buttons: -5% / -10% / -20% |
| 4 | Trading style? | Strategy fit | Buttons: Active / Patient |
| 5 | Coins to exclude? | Pair filtering | Free text or "None" |
| 6 | Paper or live? | Mode selection | Buttons: Paper (Recommended) / Live |

After all 6 answers, GPT-4o receives the inputs and generates a strategy recommendation including: strategy name, parameters, expected returns, rationale, and confirmation prompt (Activate / Adjust / Explain more).

### 7.2 Full Automation Mode

| Feature | Description |
|---------|-------------|
| Trade execution | Freqtrade executes buys/sells per active strategy signals |
| Trade notifications | Instant WhatsApp message on every open and close with pair, price, P&L |
| Daily summary | Automated at user's preferred time: P&L, trades, win rate, balance |
| Weekly summary | Every Monday: 7-day performance, best/worst trade, portfolio snapshot |
| Monthly summary | End of month: cumulative performance, strategy review recommendation |
| On-demand portfolio | User asks any time: "How am I doing?" → instant response |

### 7.3 Macro Intelligence Layer

| Component | Detail |
|-----------|--------|
| CryptoPanic API | Crypto-specific news with sentiment scores (free tier) |
| Fear & Greed Index | Daily market sentiment 0-100 (free, single endpoint) |
| NewsAPI | Macro news: Fed decisions, geopolitical events, inflation |
| Scan frequency | Every 6 hours via APScheduler |
| Alert threshold | Fear & Greed < 25 or > 80, or high headline sentiment score |
| AI analysis | Strategy config + headlines + F&G → should strategy be adjusted? |
| User response | Agree / Disagree / Let's discuss → conversational alignment |

### 7.4 Manual Trade Execution

| Action | Behaviour |
|--------|-----------|
| Force Buy | "Buy BTC" → bot confirms pair, price, allocation → user confirms → Freqtrade force-buy |
| Force Sell | "Sell my ETH" or "Close trade #3" → bot shows expected P&L → user confirms → force-sell |
| Close All | "Close all trades" → emergency close, requires explicit confirmation |

All manual trades are flagged as `signal_source: "force_entry"` in audit trail.

### 7.5 Web Dashboard

Multi-page trading dashboard — the web companion to the WhatsApp bot. Authenticated via Supabase phone OTP.

| Page | Features |
|------|----------|
| **Login** | Phone number entry → SMS OTP verification → session creation |
| **Dashboard** | Bot status (running/stopped, paper/live), today's P&L with sparkline, total balance, top 5 open trades, last 10 closed trades, Fear & Greed score + headlines, quick action buttons (switch strategy, force entry, view market) |
| **Trades** | Open positions table (live P&L, force-exit button, 10s polling), trade history (filterable by date/pair/strategy), trade stats bar (win rate, avg profit, best/worst, total count) |
| **Strategies** | Gallery of 5 built-in strategies with risk badges and descriptions, active strategy detail with performance data, strategy builder (form-based: entry/exit conditions with indicator/operator/value rows, stoploss, trailing stop, ROI table, pairs), strategy comparison (side-by-side) |
| **Market** | Live price cards per pair (price, 24h change, high/low, volume), candlestick chart with timeframe selector (lightweight-charts), indicator panel (RSI, EMA, BB values), macro news feed, Fear & Greed gauge |
| **Settings** | Profile (name, risk, yield, drawdown, style, pairs, mode), strategy params (stoploss, max trades, timeframe, ROI), KuCoin API keys (encrypted, test connection), notification toggles |
| **Logs** | WhatsApp conversation history (chat bubble UI), macro scan log (scan results, AI recommendations, user responses), error log |

Architecture: Next.js API routes proxy to Freqtrade (server-side JWT auth). Supabase direct from client for reads. Polling for real-time data (10s trades, 30s prices). Strategy builder saves JSON configs to database.

---

## 8. Freqtrade Integration

### 8.1 Current Setup

- **Exchange:** KuCoin (4 pairs: BTC/USDT, ETH/USDT, SOL/USDT, ADA/USDT)
- **Active Strategy:** MeanReversionScalper (5m, BB + RSI, -0.4% SL)
- **API Server:** Port 8080, JWT auth, OpenAPI enabled
- **Database:** SQLite (tradesv3.sqlite)
- **Deployment:** Render.com (Python runtime)
- **Mode:** Live trading (dry_run: false)

### 8.2 REST API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/token/login` | POST | JWT authentication |
| `/api/v1/ping` | GET | Health check |
| `/api/v1/status` | GET | All open trades with live P&L |
| `/api/v1/profit` | GET | Overall profit summary |
| `/api/v1/balance` | GET | Portfolio balance |
| `/api/v1/trades` | GET | Trade history |
| `/api/v1/show_config` | GET | Current bot configuration |
| `/api/v1/forcebuy` | POST | Manually open a trade |
| `/api/v1/forcesell` | POST | Manually close a trade |
| `/api/v1/reload_config` | POST | Apply updated strategy config |
| `/api/v1/start` | POST | Start bot |
| `/api/v1/stop` | POST | Stop bot |
| `/api/v1/config` | PATCH | Update strategy parameters |

### 8.3 Trade Data Available

Each trade record includes:
- `trade_id`, `pair`, `strategy`, `enter_tag` (signal source)
- `open_date`, `close_date`, `open_rate`, `close_rate`
- `stake_amount`, `close_profit`, `close_profit_abs`
- `exit_reason` (ROI, STOPLOSS, SELL_SIGNAL, FORCE_SELL)
- `stop_loss_abs`, `initial_stop_loss`, `min_rate`, `max_rate`
- `orders` (all entry/exit orders)
- `custom_data` (extensible metadata via `trade.set_custom_data()`)

### 8.4 Available Strategies

| Strategy | Risk | Timeframe | Entry Logic | Stoploss |
|----------|------|-----------|-------------|----------|
| ConservativeRSI | Low | 5m | RSI < 30 + Price > 200 EMA | -5% |
| EMACrossover | Low-Med | 15m | EMA(12) crosses above EMA(26) + MACD positive | -7% |
| BollingerBreakout | Medium | 5m | Price touches lower BB + RSI < 35 + Stochastic < 20 | -8% |
| ActiveTrader | Med-High | 1m | Multiple OR conditions (RSI/BB/EMA/MACD) | -3% |
| MeanReversionScalper | Medium | 5m | Close < BB lower + RSI < 30 + Volume > MA | -0.4% |

---

## 9. AI Engine (GPT-4o)

### 9.1 Model

`gpt-4o` via OpenAI Python SDK.

### 9.2 System Prompt Structure

Every GPT-4o call includes:
1. Bot persona and personality guidelines
2. User profile (name, risk tolerance, yield target, drawdown, style, mode)
3. Active strategy details (name, params, activation reason)
4. Available strategies list with descriptions
5. Available trading pairs
6. Rules (confirmation required for trades, concise responses)

### 9.3 Tool Definitions (9 Tools)

| Tool | Purpose | Requires Confirmation |
|------|---------|----------------------|
| `get_bot_status` | Fetch open trades | No |
| `get_profit_summary` | Fetch P&L summary | No |
| `get_balance` | Fetch portfolio balance | No |
| `get_trade_history` | Fetch recent trades | No |
| `force_entry` | Open a trade | Yes |
| `force_exit` | Close a trade | Yes |
| `switch_strategy` | Change active strategy | Yes |
| `update_strategy_params` | Modify stoploss/trades/timeframe | Yes |
| `get_market_price` | Current price + 24h stats | No |
| `analyze_trend` | Technical analysis (RSI, EMA, BB) | No |

### 9.4 Conversation Flow

1. User sends message via WhatsApp
2. Message router loads user profile + active strategy + last 20 messages
3. GPT-4o receives full context as system prompt + message history
4. If GPT-4o calls a tool → execute against Freqtrade/CCXT → return result
5. GPT-4o may call multiple tools in sequence (tool use loop)
6. Final text response sent to user via WhatsApp
7. Both user message and assistant response saved to `conversations` table

---

## 10. Trade Compliance & Audit Trail

**Phase:** Post-MVP (Phase 7, Days 17-20)
**Purpose:** Insurance-grade proof that every trade was executed per the agreed strategy.

### 10.1 Why This Matters

For an insurance company to underwrite trading risk, they need to verify:
- The trade matched the agreed strategy rules (compliance)
- The strategy wasn't modified between agreement and execution (integrity)
- The evidence can't be forged after the fact (immutability)
- The trade can be independently reproduced (verification)

### 10.2 Three-Layer Architecture

#### Layer 1 — Signal Snapshot (Proof of Compliance)

Every trade captures at the moment of entry:
- **Indicator values**: RSI, EMA(50), EMA(200), BB upper/lower, volume, volume MA
- **Conditions checked**: Each entry condition with its boolean result and actual values
- **Signal source**: `strategy` (automated) / `force_entry` (manual) / `macro_adjustment`
- **Strategy reference**: Name + file hash + config hash

Implementation: Modify each strategy's `confirm_trade_entry()` callback to log indicator values via Freqtrade's `trade.set_custom_data()`.

#### Layer 2 — Strategy Integrity (Tamper Detection)

- SHA-256 hash of the strategy `.py` file computed on every load/change
- Stored in `strategy_versions` table with timestamp and who changed it
- Every trade audit log references the strategy hash at execution time
- If strategy is modified between user agreement and trade → **hash mismatch → flagged**
- Strategy change events logged with: old hash, new hash, changed_by, reason, user_agreement

#### Layer 3 — Independent Verification (Reproducibility)

- Store the OHLCV candle data window that was active at entry time
- Given the same candle data + same strategy file → replay should generate the same signal
- Third-party audit API: `GET /audit/trade/{id}` returns full compliance report
- All audit records are append-only (Supabase RLS prevents UPDATE/DELETE)

### 10.3 Manual Trade Policy

Force entries via WhatsApp ("Buy BTC") are:
- **Allowed** — users can always override the bot
- **Clearly flagged** as `signal_source: "force_entry"` in the audit trail
- **Classified separately** — insurers can exclude them or price them at a different tier

### 10.4 Insurance Tier Model

| Tier | Coverage | Criteria |
|------|----------|----------|
| Full | All trades | All strategy-compliant, no overrides, strategy unchanged since agreement |
| Partial | Strategy trades only | Manual overrides excluded from coverage |
| None | No coverage | Strategy modified without re-agreement, or unverified entries |

### 10.5 Compliance Snapshot Example

```json
{
  "trade_id": 42,
  "pair": "BTC/USDT",
  "timestamp": "2026-03-22T14:30:00Z",
  "signal_source": "strategy",
  "indicators": {
    "rsi": 28.4,
    "ema_50": 67200.50,
    "ema_200": 65100.00,
    "bb_lower": 66800.00,
    "bb_upper": 68500.00,
    "volume": 1250.5,
    "volume_ma": 980.2
  },
  "strategy_config": {
    "name": "MeanReversionScalper",
    "version_hash": "sha256:a1b2c3d4...",
    "stoploss": -0.004,
    "roi": {"0": 0.005}
  },
  "compliance": {
    "all_conditions_met": true,
    "conditions_checked": [
      {"condition": "close < bb_lower", "result": true, "values": {"close": 66750, "bb_lower": 66800}},
      {"condition": "rsi < 30", "result": true, "values": {"rsi": 28.4}},
      {"condition": "volume > volume_ma", "result": true, "values": {"volume": 1250.5, "volume_ma": 980.2}}
    ]
  },
  "signature": "hmac_sha256:e5f6g7h8..."
}
```

---

## 11. Error Handling & Resilience

Every failure state produces a WhatsApp notification. No silent failures.

| Failure | User Message |
|---------|-------------|
| Freqtrade unreachable | "Your bot is currently unreachable. Trading is paused. We're investigating." |
| KuCoin API error | "Exchange connection issue. No trades are executing. Check your API key." |
| AI API timeout | "I'm having trouble processing your request. Please try again in a moment." |
| Webhook delivery failed | Retry 3x with exponential backoff before logging as failed |
| CryptoPanic/NewsAPI fail | Skip scan cycle silently. Alert only after 3 consecutive failures. |

Implementation: Centralized `error_handler.py` classifies exceptions and dispatches WhatsApp alerts.

---

## 12. Security

| Concern | Solution |
|---------|----------|
| KuCoin API keys | AES-256-GCM encrypted at rest in Supabase. Never in logs or messages. |
| Webhook verification | Meta signature validation on every incoming request |
| API authentication | JWT auth on Freqtrade API with auto-refresh on 401 |
| User authentication | Phone number from Meta webhook (verified by Meta) |
| Role-based access | Admin role for strategy changes. Trader role for trading. |
| Rate limiting | Max 30 messages/minute per user (planned) |
| Sensitive actions | Force buy/sell require explicit Yes/No confirmation |
| Credential storage | All secrets in environment variables, never in code |
| Withdrawal protection | Documentation instructs: KuCoin keys must have trade-only permissions |
| Dashboard authentication | Supabase Auth with phone OTP. Session-based. Same phone as WhatsApp. |
| Audit immutability | Supabase RLS: append-only on compliance_events (Phase 7) |

---

## 13. Build Sequence & Progress

### Phase 1 — Foundation Hardening (Days 1-3) ✅ COMPLETE

All 35 files created:
- FastAPI server with webhook routes
- Meta WhatsApp Cloud API client (text, images, interactive buttons)
- Supabase client with full CRUD
- OpenAI GPT-4o engine with 9 tool definitions
- Freqtrade REST API client with JWT auth
- AES-256-GCM encryption for KuCoin keys
- Error handler with WhatsApp notification dispatch
- Exponential backoff retry decorator
- WhatsApp message formatters
- Onboarding state machine (6-question interview)
- Strategy manager with Freqtrade reload
- Market analysis with pure-Python indicators
- Chart generator (candlestick + P&L)
- Macro intelligence (CryptoPanic + NewsAPI + Fear & Greed)
- APScheduler with 4 background jobs
- Pydantic models and database migrations

### Phase 2 — Strategy Onboarding (Days 4-6) — NEXT

- Test onboarding flow end-to-end with real WhatsApp messages
- Refine GPT-4o strategy recommendation prompt
- Wire up Freqtrade reload after strategy activation
- Handle edge cases (re-onboarding, mid-onboarding restart)

### Phase 3 — Reporting Layer (Days 7-9) — PENDING

- Test daily/weekly/monthly summary jobs
- Build chart rendering pipeline (generate → upload → send)
- Test force-buy/sell with confirmation flow
- Validate P&L accuracy against Freqtrade data

### Phase 4 — Macro Intelligence (Days 10-12) — PENDING

- Obtain CryptoPanic + NewsAPI keys
- Test macro scan pipeline end-to-end
- Tune significance thresholds
- Test conversational strategy alignment flow

### Phase 5 — Web Dashboard (Days 13-20) — PENDING

#### Phase 5.0 — Auth + Layout Shell (Days 13-14)
- Supabase Auth with phone OTP (useAuth context)
- Login page (phone → OTP → session)
- Sidebar navigation (6 pages + bot status indicator)
- TopBar (user name, mode badge, sign out)
- Mobile bottom nav
- Reusable shared components (Card, Badge, Toggle)

#### Phase 5.1 — API Proxy Layer (Day 14)
- Catch-all Freqtrade proxy route (server-side JWT)
- Market prices route (KuCoin REST)
- Market analysis route (OHLCV + indicators)
- Macro data route (CryptoPanic + Fear & Greed)
- Expand api.ts with ~15 typed fetch functions

#### Phase 5.2 — Dashboard Page (Days 15-16)
- Bot status card (running/stopped, start/stop, paper/live)
- P&L card with sparkline (recharts)
- Balance card (USDT total + per-coin)
- Open trades card (top 5)
- Recent trades table (last 10)
- Market sentiment (Fear & Greed + headlines)
- Quick action buttons
- useFreqtrade polling hook (10s)

#### Phase 5.3 — Trades Page (Day 16)
- Open positions table (live P&L, force-exit)
- Trade history table (filterable: date, pair, strategy)
- Trade stats bar (win rate, avg profit, best/worst)
- Reusable DataTable component

#### Phase 5.4 — Settings Page (Day 17)
- Migrate existing 4 components into new layout
- Remove old login logic (handled by AuthProvider)

#### Phase 5.5 — Strategies Page (Days 17-18)
- Strategy gallery (5 built-in, risk badges, activate button)
- Active strategy detail + performance stats
- Strategy builder (form-based: conditions, stoploss, ROI, pairs)
- Strategy comparison (side-by-side)

#### Phase 5.6 — Market Page (Days 18-19)
- Price cards per pair (price, 24h change, volume)
- Candlestick chart (lightweight-charts, timeframe selector)
- Indicator panel (RSI, EMA, BB)
- Macro news feed
- Fear & Greed gauge (CSS)
- useMarket polling hook (30s)

#### Phase 5.7 — Logs Page (Day 19)
- Conversation log (chat bubble UI)
- Macro scan log (expandable rows)
- Error log (Freqtrade logs or placeholder)

**Totals:** ~46 new files, ~8 modified files

### Phase 6 — QA & Launch (Days 21-23) — PENDING

- Full E2E test across all flows
- Load test, security review
- Create WhatsApp entry point (QR code / link)

### Phase 7 — Trade Compliance & Audit Trail (Days 24-28) — PENDING

- Build audit service with HMAC signing
- Create audit database tables
- Modify strategies for indicator snapshots
- Build strategy hashing and version tracking
- Build audit query endpoint
- Test compliance verification

---

## 14. Environment Variables

```
# Meta WhatsApp Cloud API
WHATSAPP_TOKEN              # API access token
WHATSAPP_PHONE_NUMBER_ID    # Business phone number ID
WHATSAPP_VERIFY_TOKEN       # Custom webhook verification token

# OpenAI
OPENAI_API_KEY              # GPT-4o API key

# Supabase
SUPABASE_URL                # Project URL (https://xxx.supabase.co)
SUPABASE_KEY                # Service role key

# Freqtrade
FREQTRADE_API_URL           # REST API endpoint (http://localhost:8080 or Render URL)
FREQTRADE_API_USERNAME      # API auth username
FREQTRADE_API_PASSWORD      # API auth password

# Macro Data Sources
CRYPTOPANIC_API_KEY         # CryptoPanic news API
NEWSAPI_KEY                 # NewsAPI for macro headlines

# Security
ENCRYPTION_KEY              # 64-char hex string for AES-256 key derivation

# Web Dashboard (Next.js)
NEXT_PUBLIC_SUPABASE_URL    # Supabase project URL (public, used in browser)
NEXT_PUBLIC_SUPABASE_ANON_KEY # Supabase anon key (public, used in browser)
FREQTRADE_API_URL           # Freqtrade URL (server-side only, used by API proxy)
FREQTRADE_API_USERNAME      # Freqtrade auth (server-side only)
FREQTRADE_API_PASSWORD      # Freqtrade auth (server-side only)

# Deployment
PORT                        # Server port (default: 8000)
DEBUG                       # Enable debug logging (true/false)
```

---

## 15. Deployment

### WhatsApp Bot Server

Deploys as a **separate Render service** alongside Freqtrade:

```yaml
# render.yaml addition
- type: web
  name: neuronaq-whatsapp-bot
  runtime: python
  buildCommand: pip install -r whatsapp_bot/requirements.txt
  startCommand: uvicorn whatsapp_bot.app:app --host 0.0.0.0 --port $PORT
  envVars:
    - key: WHATSAPP_TOKEN
      sync: false
    - key: OPENAI_API_KEY
      sync: false
    - key: SUPABASE_URL
      sync: false
    - key: SUPABASE_KEY
      sync: false
    # ... (all env vars)
```

### Freqtrade Bot

Continues running as existing Render service on port 8080. No changes needed — the WhatsApp bot communicates via REST API.

### Web Dashboard

Deploys to Render or Vercel as a Next.js app with Node.js runtime (not static — requires API routes for Freqtrade proxy and market data). Environment variables for Supabase (public) and Freqtrade (server-side only) must be configured.

---

## 16. API Contracts

### Meta WhatsApp Cloud API

```
POST /v17.0/{phone_number_id}/messages    # Send message
POST /v17.0/{phone_number_id}/media       # Upload media
GET  /webhook                              # Verification
POST /webhook                              # Incoming messages
```

### OpenAI API (OpenAI SDK)

```python
client.chat.completions.create(
    model="gpt-4o",
    max_tokens=1024,
    messages=[{"role": "system", "content": system_prompt}, *messages],
    tools=TOOLS,          # 9 tool definitions
)
```

### Freqtrade REST API

See Section 8.2 for full endpoint list.

### Supabase

Direct PostgreSQL access via `supabase-py` client library. No custom API layer needed.

---

## 17. Sample Conversations

### New User Onboarding

```
User: Hi

Bot:  Welcome to Neuronaq Trading Bot! 🚀

      I'm your AI-powered crypto trading assistant.
      Let's get you set up in under 2 minutes.

      First — what's your name?

User: Ahmed

Bot:  Nice to meet you, Ahmed! 👋

      What's your monthly yield target?
      This helps me pick the right strategy for you.

      [~5% (Safe)] [~10% (Moderate)] [20%+ (Aggressive)]

User: [taps ~10% (Moderate)]

Bot:  Got it! Now, what's the maximum monthly loss
      you're comfortable with?

      This is your safety floor — the bot won't exceed this.

      [-5% (Cautious)] [-10% (Moderate)] [-20% (Risky)]

User: [taps -10% (Moderate)]

Bot:  What's your preferred trading style?

      [Active] [Patient]

User: [taps Active]

Bot:  Any coins you don't want me to trade?

      Available pairs: BTC, ETH, SOL, ADA

      Reply with coin names to exclude, or say None to trade all.

User: None

Bot:  Last question! Which mode do you want to start with?

      Paper Trading — simulated trades, no real money
      Live Trading — real trades on KuCoin

      💡 We recommend starting with Paper to test your strategy.

      [Paper (Recommended)] [Live Trading]

User: [taps Paper (Recommended)]

Bot:  Analyzing your profile... 🧠

Bot:  Based on your goals, I recommend MeanReversionScalper:

      A medium-risk scalping strategy that catches oversold bounces
      using Bollinger Bands and RSI on 5-minute candles.

      Parameters:
      • Stoploss: -0.4%
      • Max trades: 4
      • Timeframe: 5m
      • Take profit: +0.5%

      Expected: 50-65% win rate, tight TP/SL for quick feedback.
      This matches your active style and 10% monthly target.

      Ready to activate? Reply Activate, Adjust, or Explain more

User: Activate

Bot:  ✅ Setup Complete!

      Strategy: MeanReversionScalper
      Mode: 📝 Paper Trading
      Stoploss: -0.4%
      Max Trades: 4

      Your bot is now active! I'll send you trade notifications
      and a daily summary.

      You can ask me anything — try:
      • "How's my bot doing?"
      • "Show me BTC trend"
      • "What's happening in the market?"
      • "Switch to a different strategy"
```

### Macro Alert

```
Bot:  ⚠️ Macro Alert — Strategy Review Needed

      Fear & Greed Index: 21/100

      Top Headlines:
      • Major escalation in Middle East tensions
      • Bitcoin drops 8% in 4 hours
      • Fed signals potential emergency rate cut
      • Crypto liquidations exceed $500M

      Analysis:
      Your current strategy (MeanReversionScalper) was designed
      for ranging markets. Current extreme fear conditions suggest:
      → Reduce max open trades: 4 → 1
      → Tighten stoploss: -0.4% → -0.2%
      → Pause new entries until Fear & Greed > 35

      This protects capital during elevated uncertainty.

      Reply: Agree / Disagree / Let's discuss

User: Agree

Bot:  ✅ Strategy adjusted:
      • Max trades: 4 → 1
      • Stoploss: -0.4% → -0.2%
      Changes active on next candle.

      I'll notify you when Fear & Greed recovers above 35.
```

---

## 18. Roadmap

### MVP (Current Build)
- Strategy onboarding interview
- Full/paper automation with trade notifications
- Daily/weekly/monthly reports
- Macro intelligence (CryptoPanic, NewsAPI, Fear & Greed)
- Manual force buy/sell
- Web Dashboard (7 pages: login, dashboard, trades, strategies, market, settings, logs)
- Strategy builder (form-based, JSON config)
- Per-user KuCoin keys (encrypted)
- Dashboard auth (Supabase phone OTP)

### v1.1 (Post-MVP)
- Trade compliance & audit trail (Phase 7)
- Semi-automation mode (confirm each trade)
- No-automation mode (signals only)
- Pair watchlists with price alerts
- Advanced memory tiers (mid-term summarization, long-term fact extraction)

### v1.2
- Learning & education module (concept explanations, quizzes, glossary)
- Custom strategy parameter presets
- Strategy backtesting via WhatsApp
- Usage analytics for admins

### v2.0
- Multi-exchange support (Binance, Bybit, Coinbase)
- Voice message processing (speech-to-text)
- Social/copy trading
- Multi-language (Arabic, Urdu, Hindi, Spanish)
- WhatsApp Payments integration

---

## 19. MVP Success Criteria

The MVP is shippable when all of the following are true:

- [ ] New user completes full onboarding via WhatsApp in under 5 minutes
- [ ] Bot correctly activates paper trading mode and sends trade notifications
- [ ] Bot generates accurate daily, weekly, and monthly summaries
- [ ] Macro event above threshold triggers proactive alert within one scan cycle (6 hours)
- [ ] User can adjust strategy via WhatsApp and changes reflect in Freqtrade within 60 seconds
- [ ] Web dashboard loads with live bot status and P&L data after phone OTP login
- [ ] User can browse and switch strategies from the web dashboard
- [ ] Strategy builder saves custom JSON configs to database
- [ ] All 7 dashboard pages render correctly (login, dashboard, trades, strategies, market, settings, logs)
- [ ] Settings page correctly displays and updates all user configuration
- [ ] KuCoin API keys stored encrypted and never appear in any log or message
- [ ] All error states (Freqtrade down, KuCoin error, AI timeout) produce a WhatsApp notification
- [ ] Live trading mode executes real trades on KuCoin correctly after paper validation

---

*Neuronaq — Tailored. Rapid. Fortified.*
*Confidential — March 2026*
