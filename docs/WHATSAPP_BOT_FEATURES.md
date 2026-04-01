# Neuronaq WhatsApp Trading Bot — Complete Feature Guide

> Your personal AI-powered crypto trading assistant, right inside WhatsApp.

---

## Table of Contents

1. [Trading & Portfolio Management](#1-trading--portfolio-management)
2. [Market Research & Analysis](#2-market-research--analysis)
3. [Strategy Management](#3-strategy-management)
4. [AI-Powered Intelligence](#4-ai-powered-intelligence)
5. [Learning & Education](#5-learning--education)
6. [Alerts & Notifications](#6-alerts--notifications)
7. [User Memory & Personalization](#7-user-memory--personalization)
8. [Admin & Multi-User](#8-admin--multi-user)
9. [Trade Compliance & Audit Trail](#9-trade-compliance--audit-trail)
10. [Web Dashboard](#10-web-dashboard)
11. [Sample Conversations](#11-sample-conversations)
12. [Architecture & Tech Stack](#12-architecture--tech-stack)

---

## 1. Trading & Portfolio Management

### Live Bot Status
- See if the bot is running, paused, or stopped
- View the current active strategy and its settings
- Check how many trades are open vs. max allowed

### Open Trades Dashboard
- View all currently open positions in a clean list
- See per-trade: pair, entry price, current price, unrealized P&L (% and $)
- Color-coded indicators (profit/loss)

### Trade History
- View past trades (today, this week, this month, all-time)
- Filter by pair, strategy, or result (wins/losses)
- See total P&L, win rate, average profit per trade

### Manual Trading
- **Force Buy**: Open a position on any whitelisted pair
  - "Buy BTC" → bot opens a BTC/USDT trade
  - Confirmation required before execution
- **Force Sell**: Close any open trade
  - "Sell trade #3" or "Close my ETH position"
  - Shows expected profit/loss before confirming
- **Close All**: Emergency close all open trades

### Portfolio Summary
- Total invested, total profit, current balance
- Breakdown by pair (how much allocated to BTC vs ETH vs SOL vs ADA)
- Daily, weekly, monthly P&L summaries
- Portfolio pie chart sent as image

### Profit & Loss Reports
- "How did I do today?" → daily P&L summary
- "This week's performance" → weekly report with chart
- "Best and worst trades this month" → highlights
- Cumulative P&L chart over time (image)

---

## 2. Market Research & Analysis

### Live Price Checks
- "What's the price of BTC?" → instant price with 24h change
- "Price of ETH SOL ADA" → multi-pair price table
- Shows: current price, 24h high/low, 24h volume, % change

### Technical Analysis
- "Analyze BTC" → full technical breakdown:
  - RSI value + interpretation (overbought/oversold/neutral)
  - EMA position (price above/below 50 & 200 EMA)
  - MACD signal (bullish/bearish crossover)
  - Bollinger Band position (upper/middle/lower)
  - Volume analysis (above/below average)
  - Overall trend verdict: Bullish / Bearish / Sideways

### Chart Generation
- **Candlestick Charts**: 5m, 15m, 1h, 4h, 1d timeframes
- **Indicator Overlays**: Bollinger Bands, EMA lines, RSI subplot, MACD subplot
- **P&L Charts**: Your profit/loss over time
- **Comparison Charts**: Multiple pairs side by side
- All charts rendered as images and sent directly in WhatsApp

### Trend Analysis
- "What's trending?" → scan all pairs for momentum
- "Is BTC bullish?" → trend direction with confidence level
- Support and resistance level identification
- Volume spike detection
- Divergence alerts (price vs RSI divergence)

### Market Sentiment
- Aggregate indicator scores across all pairs
- "Market overview" → overall crypto market mood
- Pair-by-pair signal strength ranking
- Best entry opportunities right now

### Pair Comparison
- "Compare BTC vs ETH" → side-by-side metrics
- Performance comparison over different timeframes
- Volatility comparison
- Which pair has stronger signals right now

### Watchlist *(Planned — v1.1)*
- Set custom watchlists: "Watch DOGE and XRP"
- Get periodic updates on watched pairs
- Alert when a watched pair hits a price level

---

## 3. Strategy Management

### View All Strategies
- List all 5 available strategies with:
  - Name, risk level, timeframe
  - Entry/exit logic explained simply
  - Expected win rate and monthly return range
  - Current status (active/inactive)

### Strategy Details
- "Tell me about ConservativeRSI" → deep dive:
  - What indicators it uses
  - Exact entry and exit conditions
  - Risk parameters (stoploss, take profit)
  - What market conditions it works best in
  - Historical backtest performance

### Switch Strategy
- "Switch to EMACrossover" → change the active strategy
  - Shows what will change (risk, timeframe, signals)
  - Warning about open trades
  - Confirmation required
  - Bot reloads with new strategy

### Tweak Strategy Parameters
- "Set stoploss to -2%" → update stoploss value
- "Change timeframe to 15m" → update candle timeframe
- "Set max trades to 4" → update max concurrent trades
- "Update ROI to 3% after 30 minutes" → modify take profit schedule
- All changes require confirmation + show before/after comparison

### Strategy Recommendations
- Based on your risk profile: "Which strategy should I use?"
- Based on market conditions: "Best strategy for this market?"
- GPT-4o analyzes current market + your preferences → personalized recommendation

### Strategy Performance Comparison *(Backtesting — Planned v1.2)*
- "Compare strategies" → side-by-side performance table
- Backtest results for each strategy on recent data *(v1.2)*
- Which strategy would have performed best last week/month

### Custom Strategy Parameters *(Planned — v1.2)*
- Save personal parameter presets: "Save this as my aggressive setup"
- Quick switch between presets: "Load my conservative preset"

---

## 4. AI-Powered Intelligence

### Natural Language Understanding
- No need to memorize commands — just talk naturally
- "How's my bot?" = "Show status" = "What's happening?"
- The AI understands context and intent from conversation flow

### Smart Recommendations
- **Trade Recommendations**: "Should I buy BTC now?"
  - AI analyzes: current indicators, recent trend, your risk tolerance, strategy signals
  - Gives clear recommendation with reasoning
  - Never guaranteed — always includes risk disclaimer

- **Timing Suggestions**: "When should I enter ETH?"
  - AI identifies optimal entry zones based on indicators
  - "ETH RSI is at 42 and dropping. Wait for RSI < 30 for a better entry"

- **Risk Warnings**: Proactive alerts when risk is elevated
  - "Your stoploss is very tight at -0.4%. In current volatility, consider widening to -2%"
  - "You have 6 open trades — your exposure is high right now"

### Context-Aware Responses
- The bot remembers what you were talking about
- "What about SOL?" → knows you were discussing trends, applies same analysis to SOL
- "Do the same for ETH" → repeats the last action for a different pair

### Multi-Turn Conversations
- Complex requests handled over multiple messages
- "I want to change my strategy" → bot walks you through options step by step
- Follow-up questions handled naturally

### Explainable AI
- Every recommendation comes with reasoning
- "Why did the bot buy BTC?" → explains which indicator signals triggered
- "Why did that trade lose?" → post-trade analysis

---

## 5. Learning & Education

> **Planned for v1.2** — not included in MVP. The AI can answer basic questions about trading concepts, but the structured learning module below is deferred.

### Concept Explanations
- "What is RSI?" → simple explanation with analogy
- "How do Bollinger Bands work?" → visual explanation + how your strategy uses them
- "What's a stoploss?" → beginner-friendly explanation with examples

### Strategy Education
- "Explain my current strategy" → step-by-step walkthrough
- "Why does this strategy use 5-minute candles?" → reasoning behind design choices
- "What's the difference between scalping and swing trading?" → comparison

### Trading Fundamentals
- Market orders vs limit orders
- Long vs short positions
- Risk management principles
- Position sizing explained
- Reading candlestick patterns

### Personalized Learning Path
- Bot tracks what you've learned and what you haven't
- Suggests next topics based on your questions
- "What should I learn next?" → tailored recommendation
- Quizzes: "Test me on RSI" → interactive Q&A

### Real-Time Learning
- When a trade happens: "The bot just bought ETH because RSI dropped below 30. This means..."
- Connects theory to your actual trades
- "Show me what triggered my last buy" → indicator values at entry time

### Glossary
- "Define: slippage" → instant definition
- "Define: liquidity" → with context from your trading pairs
- Builds personal glossary based on terms you've asked about

---

## 6. Alerts & Notifications

### Trade Notifications (Push)
- Instant WhatsApp message when a trade opens
  - Pair, direction, entry price, strategy signal reason
- Instant message when a trade closes
  - Exit price, profit/loss, duration, reason (stoploss/ROI/signal)

### Scheduled Reports
- **Daily Summary**: Automatic report at your preferred time — P&L, trades, win rate, best/worst trade, portfolio snapshot
- **Weekly Summary**: Every Monday morning — 7-day performance, cumulative P&L, strategy review
- **Monthly Summary**: 1st of each month — full month performance review, strategy recommendation

### Macro Intelligence Alerts
- **Data Sources**: CryptoPanic (crypto news + sentiment), NewsAPI (macro headlines), Fear & Greed Index (alternative.me)
- **Scan Frequency**: Every 6 hours via automated background job
- **Alert Triggers**: Fear & Greed < 25 (extreme fear) or > 80 (extreme greed), or high headline significance
- **Proactive Alerts**: When a macro event crosses thresholds, bot messages you with:
  - Current Fear & Greed score
  - Top headlines driving the move
  - AI analysis of impact on your active strategy
  - Proposed strategy adjustments (e.g., tighten stoploss, reduce max trades)
- **Conversational Alignment**: Reply with Agree / Disagree / Let's discuss — bot adjusts strategy or opens a conversation

### Price Alerts *(Planned — v1.1)*
- "Alert me when BTC hits $70,000" → triggered notification
- "Alert when ETH drops 5% in an hour" → percentage-based alerts
- "Alert when RSI goes below 30 on SOL" → indicator-based alerts

### Strategy Alerts
- Notification when strategy signals change
- "Bull signal on BTC just activated"
- "3 consecutive losses — consider pausing"

### Risk Alerts (Proactive)
- Warning when drawdown exceeds threshold
- Alert when too many trades are open
- Notification on unusual market volatility
- "Your daily loss limit has been reached"

### Bot Health Alerts
- Bot stopped unexpectedly → instant notification
- Exchange connection issues → warning
- API rate limit warnings

### Custom Alert Schedules *(Planned — v1.1)*
- Choose when to receive summaries (morning/evening/both)
- Mute notifications during sleeping hours
- Urgent-only mode (only losses > X%)

---

## 7. User Memory & Personalization

### The Bot Never Forgets
- Memory system:
  - **Short-term (MVP)**: Last 20 messages in active context
  - **Mid-term** *(Planned — v1.1)*: Older conversations summarized and stored
  - **Long-term** *(Planned — v1.1)*: Key facts extracted and permanently saved

### What Gets Remembered
- Your name and preferred greeting
- Risk tolerance (conservative/moderate/aggressive)
- Favorite trading pairs
- Preferred strategy and parameter tweaks
- Learning progress (what concepts you've mastered)
- Past questions and interests
- Trading goals and targets
- Communication preferences (brief vs detailed responses)

### Personalized Experience
- New users get a guided onboarding: "Hi! I'm your trading assistant. Let's set up your profile..."
- Bot adapts tone: beginners get simpler explanations, advanced users get technical detail
- Recommendations personalized to your risk profile
- "Remember that I prefer short answers" → bot adjusts permanently

### Context Continuity
- Come back after days/weeks → bot picks up where you left off
- "What was my P&L last time we talked?" → bot recalls
- References past conversations naturally

### Memory Management
- "What do you know about me?" → shows stored preferences
- "Forget my risk tolerance" → delete specific memory
- "Update my risk to aggressive" → modify memory
- Full privacy control over stored data

---

## 8. Admin & Multi-User

### User Roles
- **Admin**: Full control — strategy switches, bot start/stop, user management
- **Trader**: Can trade, view status, research, but can't change bot-level settings
- **Viewer** *(Planned — v1.1)*: Read-only — can view status and learn, but can't execute trades

### User Management (Admin Only)
- "Add user +1234567890 as trader" → whitelist new users
- "Remove user +1234567890" → revoke access
- "List all users" → see who has access
- "Promote user to admin" → change roles

### Multi-User Isolation
- Each user has their own:
  - Conversation history and memory
  - Preferences and risk profile
  - Trade logs (which trades they triggered)
  - Alert settings
  - Learning progress

### Shared Bot, Personal Experience
- All users share the same Freqtrade bot instance
- But each sees personalized insights based on their profile
- Admin actions (strategy change) affect everyone — users are notified
- "Admin changed strategy to ConservativeRSI. Your risk exposure has decreased."

### Usage Analytics (Admin) *(Planned — v1.2)*
- See how many messages each user sends
- Most active users
- Most common questions/commands
- Bot response time metrics

---

## 9. Trade Compliance & Audit Trail

> **Status:** Phase 7 (Post-MVP) — designed and specified, implementation pending after MVP launch.

Insurance-grade proof that every trade was executed per the agreed strategy. This system enables third-party verification and underwriting of trading risk.

### Why This Matters
- Proves every trade matched the agreed strategy rules (compliance)
- Detects if the strategy was modified between agreement and execution (integrity)
- Evidence can't be forged after the fact (immutability)
- Trades can be independently reproduced and verified (reproducibility)

### Three-Layer Architecture

#### Layer 1 — Signal Snapshot (Proof of Compliance)
Every trade captures at the moment of entry:
- **Indicator values**: RSI, EMA(50), EMA(200), Bollinger Bands upper/lower, volume, volume MA
- **Conditions checked**: Each entry condition with its boolean result and actual values
- **Signal source**: `strategy` (automated) / `force_entry` (manual) / `macro_adjustment`
- **Strategy reference**: Name + file hash + config hash

#### Layer 2 — Strategy Integrity (Tamper Detection)
- SHA-256 hash of the strategy `.py` file computed on every load/change
- Stored in `strategy_versions` table with timestamp and who changed it
- Every trade audit log references the strategy hash at execution time
- If strategy is modified between user agreement and trade — hash mismatch — flagged
- Strategy change events logged with: old hash, new hash, changed_by, reason, user_agreement

#### Layer 3 — Independent Verification (Reproducibility)
- OHLCV candle data window stored at entry time
- Given the same candle data + same strategy file, replay generates the same signal
- Third-party audit API: `GET /audit/trade/{id}` returns full compliance report
- All audit records are append-only (Supabase RLS prevents UPDATE/DELETE)

### Manual Trade Policy
Force entries via WhatsApp ("Buy BTC") are:
- **Allowed** — users can always override the bot
- **Clearly flagged** as `signal_source: "force_entry"` in the audit trail
- **Classified separately** — insurers can exclude them or price them at a different tier

### Insurance Tier Model

| Tier | Coverage | Criteria |
|------|----------|----------|
| Full | All trades covered | All strategy-compliant, no overrides, strategy unchanged since agreement |
| Partial | Strategy trades only | Manual overrides excluded from coverage |
| None | No coverage | Strategy modified without re-agreement, or unverified entries |

### Compliance Data Captured Per Trade
- Trade ID, pair, timestamp
- Signal source (strategy / force_entry / macro_adjustment)
- All indicator values at entry moment
- Each entry condition: condition text, boolean result, actual values
- Strategy name, file hash (SHA-256), config hash
- OHLCV candle window used for signal generation
- HMAC-SHA256 signature of the full snapshot

### Audit Database Tables
- **`trade_audit_logs`** — Per-trade compliance snapshot with indicator values, conditions, and cryptographic signature
- **`strategy_versions`** — History of every strategy file change with SHA-256 hashes and who changed it
- **`compliance_events`** — Append-only event log: strategy agreed, trade opened, trade closed, strategy changed, force override

---

## 10. Web Dashboard

The Neuronaq Web Dashboard is a companion web app that provides a full visual interface alongside the WhatsApp bot. Authenticated via phone OTP (same number as WhatsApp).

### Authentication
- Phone number + SMS OTP verification (Supabase Auth)
- Session persistence (stay logged in across visits)
- All users can access — data is scoped per user

### Dashboard (Home Page)
- **Bot Status**: Running/stopped indicator, paper/live mode, start/stop controls
- **Today's P&L**: Profit/loss with sparkline chart
- **Balance**: Total USDT balance with per-coin breakdown
- **Open Trades**: Top 5 active positions with live P&L (10-second polling)
- **Recent Trades**: Last 10 closed trades with profit/loss
- **Market Sentiment**: Fear & Greed score + top crypto headlines
- **Quick Actions**: Switch strategy, force entry, view market

### Trades Page
- **Open Positions**: Live table showing pair, entry price, current price, unrealized P&L, duration, force-exit button
- **Trade History**: Filterable by date range, pair, and strategy. Sortable columns.
- **Trade Stats**: Win rate, average profit per trade, best/worst trade, total trade count

### Strategies Page
- **Strategy Gallery**: Browse all 5 built-in strategies with risk badges, descriptions, and "Activate" button
- **Active Strategy Detail**: Full parameters, ROI table, performance metrics from trade history
- **Strategy Builder**: Create custom strategies via form:
  - Entry conditions: pick indicator (RSI, EMA, BB, Volume MA, Close) + operator (< > crosses above/below) + value
  - Exit conditions: same pattern
  - Stoploss, trailing stop, ROI schedule (editable table)
  - Max open trades, timeframe, pairs whitelist
  - Saves as JSON config to database
- **Strategy Comparison**: Side-by-side view of two strategies

### Market Page
- **Price Cards**: Live price per pair (BTC, ETH, SOL, ADA /USDT) with 24h change, high/low, volume
- **Candlestick Chart**: Interactive chart with timeframe selector (5m, 1h, 4h, 1d)
- **Indicator Panel**: RSI, EMA(50/200), Bollinger Bands values for selected pair
- **Macro Feed**: Latest headlines from CryptoPanic + NewsAPI
- **Fear & Greed Gauge**: Visual semicircular gauge (0-100) with colored zones

### Settings Page
- **Profile**: Name, risk tolerance, yield target, max drawdown, trading style, pairs blacklist, paper/live mode toggle
- **Strategy Params**: Stoploss, max open trades, timeframe, ROI table
- **API Keys**: KuCoin API key management (AES-256-GCM encrypted storage, masked display, test connection)
- **Notifications**: Toggle daily/weekly/monthly summaries, macro alerts, trade notifications

### Logs Page
- **Conversation Log**: WhatsApp chat history displayed as message bubbles (paginated)
- **Macro Scan Log**: Table of macro scans with timestamps, Fear & Greed scores, AI recommendations, user responses
- **Error Log**: Bot errors and Freqtrade log output

---

## 11. Sample Conversations

### First-Time User Onboarding
```
User: Hi

Bot: Welcome to Neuronaq Trading Bot! I'm your AI-powered crypto
     trading assistant.

     Let's get you set up. What's your name?

User: I'm Ahmed

Bot: Nice to meet you, Ahmed! Quick question — how would you
     describe your risk tolerance?

     1. Conservative — I prefer safety over big gains
     2. Moderate — balanced risk and reward
     3. Aggressive — I'm okay with higher risk for higher returns

User: 2

Bot: Got it — moderate risk profile saved!

     Here's what I can help you with:
     - Trading: Check status, P&L, open/close trades
     - Research: Live prices, charts, trend analysis
     - Strategy: View and switch trading strategies
     - Learn: Ask me anything about crypto trading

     Your bot is currently running MeanReversionScalper
     with 2 open trades. Want to see the status?
```

### Market Research Flow
```
User: What's happening in the market?

Bot: Here's your market overview:

     BTC/USDT  $67,450  +2.1%  RSI: 58 (neutral)
     ETH/USDT  $3,520   +1.8%  RSI: 52 (neutral)
     SOL/USDT  $142     +4.2%  RSI: 63 (warming up)
     ADA/USDT  $0.48    -0.5%  RSI: 41 (cool)

     SOL is showing the most momentum today.
     Want a detailed analysis on any pair?

User: Yeah analyze SOL

Bot: [Sends candlestick chart with Bollinger Bands + RSI]

     SOL/USDT 5m Technical Analysis:

     Trend: Bullish (short-term)
     RSI: 63 — approaching overbought but not there yet
     EMA: Price above 50 EMA and 200 EMA (bullish)
     Bollinger: Price near upper band (strong move)
     Volume: 40% above average (confirmed move)
     MACD: Bullish crossover 2 hours ago

     My take: Strong momentum but approaching resistance.
     Entry zone: Wait for a pullback to $138-140 range
     for better risk/reward.

     Want me to set a price alert?

User: Yes alert me at $138

Bot: Done! I'll notify you when SOL/USDT hits $138.
```

### Strategy Management Flow
```
User: Show me all strategies

Bot: Here are your available strategies:

     1. MeanReversionScalper (ACTIVE)
        Risk: Medium | TF: 5m
        Scalps oversold bounces with BB + RSI

     2. ConservativeRSI
        Risk: Low | TF: 5m
        Safe RSI-based entries above 200 EMA

     3. EMACrossover
        Risk: Low-Medium | TF: 15m
        Trend-following with EMA golden cross

     4. BollingerBreakout
        Risk: Medium | TF: 5m
        Volatility breakout with Bollinger Bands

     5. ActiveTrader
        Risk: Medium-High | TF: 1m
        Fast signals for testing (dry-run recommended)

     Want details on any strategy or want to switch?

User: I want something less risky

Bot: Based on your moderate risk profile, I'd recommend
     ConservativeRSI:

     - Lower risk with -5% stoploss (vs current -0.4%)
     - Fewer but higher-quality trades
     - Only enters when RSI < 30 AND price > 200 EMA
     - Expected: 50-60% win rate, 5-15% monthly return

     Currently you have 2 open trades. Switching will
     keep them open but no new entries until the new
     strategy signals.

     Switch to ConservativeRSI? (Yes/No)

User: Yes

Bot: Strategy switched to ConservativeRSI!
     New settings active on next candle.

     Changes:
     - Stoploss: -0.4% → -5%
     - Take Profit: 0.5% → up to 15%
     - Entry: BB+RSI → RSI<30 + EMA filter
     - Timeframe: 5m (unchanged)

     I'll remember that you prefer lower-risk strategies.
```

### Learning Flow
```
User: Why did my last trade lose?

Bot: Let me check... Your last trade was:

     ETH/USDT — Closed 45 min ago
     Entry: $3,515 | Exit: $3,501
     Loss: -0.4% (-$0.20) — hit stoploss

     What happened:
     - Bot entered because RSI dropped to 28 (oversold signal)
     - Price bounced briefly but then continued dropping
     - Stoploss at -0.4% triggered after 12 minutes

     This is normal for scalping — tight stops mean small
     losses but they happen more often. The strategy profits
     from winning more trades than it loses.

     Want me to explain how stoploss works in more detail?

User: Yeah what exactly is a stoploss?

Bot: A stoploss is your safety net — it automatically sells
     when a trade goes against you by a set amount.

     Think of it like a fire alarm:
     - You set it at a threshold (e.g., -5%)
     - If the price drops 5% from your entry → auto-sell
     - You take a small, controlled loss instead of a big one

     Your current strategy has a -0.4% stoploss, which is
     very tight. That means:
     - Max you can lose per trade: ~$0.04 on a $10 trade
     - Trades get stopped out quickly
     - You need a high win rate to be profitable

     The ConservativeRSI strategy uses -5%, giving trades
     more room to breathe. Would you like to compare?
```

### Parameter Update Flow
```
User: Update stoploss to -2%

Bot: Updating MeanReversionScalper stoploss:

     Current: -0.4%
     New:     -2.0%

     What this means:
     - Each trade can now lose up to 2% before closing
     - More room for price to bounce back
     - But losses will be larger when they happen
     - On $1 trades, max loss goes from $0.004 to $0.02

     This is a 5x wider stop. Are you sure? (Yes/No)

User: Yes

Bot: Stoploss updated to -2.0%
     Change takes effect on the next candle.

     Tip: With a wider stoploss, consider also increasing
     your take-profit target for better risk/reward.
     Current ROI is +0.5%. Want to increase it?
```

---

## 12. Architecture & Tech Stack

### System Architecture

```
WhatsApp Users
      │
      ▼
Meta Cloud API (Free)
      │
      ▼
FastAPI Server (Python)
├── Webhook Handler
├── Message Router
├── GPT-4o AI Engine (NLU + Function Calling)
├── Freqtrade API Client
├── Market Analysis (CCXT + TA-Lib)
├── Chart Generator (matplotlib)
├── Strategy Manager
└── User Memory Manager
      │
      ▼
Supabase (PostgreSQL)
├── users (profiles, preferences)
├── conversations (chat history)
├── macro_events (scan results, alerts)
├── trade_logs (per-user analytics)
└── strategies (active strategy configs)
      │
      ▼
Freqtrade Bot (REST API)
├── KuCoin Exchange
├── 5 Trading Strategies
├── Trade Database (SQLite)
└── FreqUI Dashboard
```

### Tech Stack

| Component | Technology |
|-----------|-----------|
| WhatsApp API | Meta Cloud API (free tier) |
| Bot Server | FastAPI (Python, async) |
| AI Brain | OpenAI GPT-4o with function calling |
| Database | Supabase (PostgreSQL, free tier) |
| Charts | matplotlib → PNG → WhatsApp Media API |
| Market Data | CCXT (KuCoin) + TA-Lib |
| Macro Data | CryptoPanic API + NewsAPI + Fear & Greed API |
| Encryption | AES-256-GCM (cryptography lib) |
| Web Dashboard | Next.js + React + Tailwind CSS (7 pages) |
| Deployment | Render.com (co-deployed with Freqtrade) |

### Environment Variables

```
WHATSAPP_TOKEN          — Meta Cloud API access token
WHATSAPP_PHONE_NUMBER_ID — Business phone number ID
WHATSAPP_VERIFY_TOKEN   — Webhook verification token
OPENAI_API_KEY          — GPT-4o API key
SUPABASE_URL            — Supabase project URL
SUPABASE_KEY            — Supabase service role key
FREQTRADE_API_URL       — Freqtrade REST API endpoint
FREQTRADE_API_USERNAME  — API auth username
FREQTRADE_API_PASSWORD  — API auth password
CRYPTOPANIC_API_KEY     — CryptoPanic news API key
NEWSAPI_KEY             — NewsAPI for macro headlines
ENCRYPTION_KEY          — AES-256 key for KuCoin API key encryption
NEXT_PUBLIC_SUPABASE_URL — Supabase project URL (dashboard)
NEXT_PUBLIC_SUPABASE_ANON_KEY — Supabase anon key (dashboard)
```

---

## Feature Status

### Already Built (MVP)
| Feature | Status |
|---------|--------|
| Paper Trading Mode | Implemented — default for new users |
| Scheduled Reports | Implemented — daily, weekly, monthly via APScheduler |
| Macro Intelligence | CryptoPanic + NewsAPI + Fear & Greed, 6-hour scans |

### In Development (Phase 5)
| Feature | Status |
|---------|--------|
| Web Dashboard | Planned — 7 pages: login, dashboard, trades, strategies, market, settings, logs |
| Strategy Builder | Planned — form-based builder, saves JSON config to database |

### Roadmap (Future)
| Feature | Description |
|---------|-------------|
| Voice Messages | Process voice notes → speech-to-text → execute commands |
| WhatsApp Payments | Accept payments for premium features via WhatsApp Pay |
| Social Trading | Share your P&L card with friends |
| Copy Trading | Follow another user's strategy settings |
| Multi-Exchange | Support Binance, Coinbase, Bybit alongside KuCoin |
| AI Strategy Generation | Users describe a strategy in plain English, AI generates code |
| Referral System | Invite friends, earn benefits |
| Multi-Language | Arabic, Urdu, Hindi, Spanish support |

---

*Built by Neuronaq — AI-powered crypto trading for everyone.*
