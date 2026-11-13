# AI Trading Arena — 10-Day US Stock Paper Trading Competition

> **PAPER TRADING — NOT REAL MONEY**
> Every agent trades **100% virtual money ($10,000.00 starting cash each)**. This system connects **only** to Alpaca's free Paper Trading market-data endpoint (`https://paper-api.alpaca.markets`) and **never** places real or live orders.

---

## What Is This Project?

**AI Trading Arena** is an automated, zero-server 10-day virtual stock trading competition where **10 trading agents** compete head-to-head on liquid US large-cap stocks and major ETFs:

- **4 Coded Algorithmic Strategies** (Trend Following, Mean Reversion, Intraday Opening Range Breakout, Sector ETF Rotation)
- **4 Gemini Flash AI Traders** (Technical Analyst, News & Catalyst Trader, Macro & Sector Strategist, Cautious Risk-First Trader)
- **2 Scientific Controls** (Buy-and-Hold `SPY` S&P 500 Benchmark, and a seeded `Random Trader` to measure pure luck)

### How It Runs (100% Free — Your PC Can Be Off)
1. **GitHub Actions** wakes up every **30 minutes** during US regular market hours (Monday–Friday).
2. It asks **Alpaca's Market Clock & Calendar** whether the US stock market is open right now (this automatically handles weekends, US Thanksgiving Day closure, and Black Friday's 1:00 PM ET early close).
3. If the market is open:
   - Fetches point-in-time prices, technical indicators, and market news strictly at or before decision time (`decision_timestamp`).
   - All 10 agents make a decision (`BUY`, `SELL`, or `HOLD`) with a plain-English explanation.
   - Passes every order through **one shared Professional Risk Manager**.
   - Simulates fills at the **next available price after the decision** (`fill_timestamp > decision_timestamp` — zero look-ahead) with **5 bps slippage** plus **bid-ask spread crossing**.
   - Updates each agent's separate JSON portfolio ledger in `state/agents/` and writes a plain-English daily journal.
   - Rebuilds the static dashboard in `docs/` and commits it back to GitHub so **GitHub Pages** updates automatically.
4. After the **10th US trading day** completes, the system automatically closes all open positions at the final price, locks the competition as `COMPLETED`, and publishes the **Final 10-Day Report** (including an honest statistical breakdown of skill vs. luck over 10 trading days).

---

## The 10 Competing Agents ($10,000 Virtual Cash Each)

| # | Agent ID | Name | Type | Strategy Summary |
|---|----------|------|------|------------------|
| 1 | `coded_trend` | **Trend Follower (MA / Breakout)** | Coded Strategy | Buys stocks breaking 20-bar highs with `SMA(10) > SMA(30)` and volume confirmation; trails an ATR stop-loss. |
| 2 | `coded_mean_rev` | **Mean Reversion (RSI / Bollinger)** | Coded Strategy | Buys oversold dips (`RSI(14) < 32` near lower Bollinger Band) in structural uptrends; exits on reversion to the middle band. |
| 3 | `coded_orb` | **Intraday ORB (Flat by Close)** | Coded Strategy | Trades breakouts above the first 30-minute Opening Range high; **automatically liquidates all positions before market close**. |
| 4 | `coded_sector` | **Sector ETF Rotation** | Coded Strategy | Ranks all 11 SPDR Sector ETFs (`XLK`, `XLF`, `XLV`, `XLE`, etc.) by relative strength vs. `SPY` and holds the top leaders. |
| 5 | `llm_technical` | **Technical Analyst (Gemini Flash)** | LLM Trader | Reads multi-bar price action, RSI/Bollinger confluence, volume surges, and 20-bar support/resistance with >= 2:1 reward-to-risk. |
| 6 | `llm_catalyst` | **News & Catalyst Trader (Gemini Flash)** | LLM Trader | Reacts to fresh company news headlines, earnings catalysts, and momentum shifts confirmed by volume. |
| 7 | `llm_macro` | **Macro & Sector Strategist (Gemini Flash)** | LLM Trader | Diagnoses the macro regime from `SPY`, `QQQ`, `IWM`, `TLT` (bonds), and `GLD` (gold) to rotate into favored sectors. |
| 8 | `llm_cautious` | **Cautious Risk-First Trader (Gemini Flash)** | LLM Trader | Keeps most capital in cash; only enters A+ low-volatility setups with tight stops and >= 2.5:1 reward-to-risk. |
| 9 | `ctrl_spy` | **Buy-and-Hold SPY (Benchmark)** | Control | Invests passively in the `SPY` S&P 500 ETF on Day 1 and holds until Day 10 so every agent is measured against the market. |
| 10 | `ctrl_random` | **Random Trader (Luck Control)** | Control | Randomly buys and sells universe stocks with valid stops under the exact same risk manager to reveal what luck alone looks like. |

---

## Shared Risk Manager & Execution Rules

Every single agent is governed by the **same shared Risk Manager** (`src/arena/engine/risk_manager.py`):

- **Long or Cash Only**: No short selling, no margin/leverage, fractional shares supported.
- **Stop-Loss Required**: Every `BUY` order must include a valid `stop_loss` below entry price. Orders without a stop-loss are rejected and logged.
- **1% Max Risk Per Trade**: Position size is calculated so that if the stop-loss is hit, the agent loses at most **1% of its current equity** (`$100` on a `$10,000` account).
- **20% Max Position Size**: At most **20% of equity** can be allocated to any single ticker (except the passive `ctrl_spy` benchmark control).
- **Max 5 Open Positions**: An agent can hold at most **5 positions** at any time.
- **-3% Daily Loss Circuit Breaker**: If an agent's equity drops **-3.0%** from the start of the US trading day, it is marked `DAILY_HALTED` and cannot open new positions for the rest of that day.
- **-10% Total Drawdown Circuit Breaker ("Blown Up")**: If an agent's equity falls **-10.0%** from its peak equity, all remaining positions are liquidated and the agent is permanently stopped (`BLOWN_UP`).
- **Realistic Transaction Costs**: **$0 commission**, **5 basis points (0.05%) slippage** on every buy and sell, plus **half bid-ask spread crossing** (buys fill above the ask; sells fill below the bid).
- **Zero Look-Ahead**: Decisions only see market data with `timestamp <= decision_timestamp`. Fills execute strictly at the next available price with `fill_timestamp > decision_timestamp`.

---

## Beginner Step-by-Step Setup Checklist (What You Click)

Follow these 5 steps once to launch your live automated competition:

### Step 1: Get Your Free Alpaca Paper Trading Keys (2 minutes)
1. Go to [https://app.alpaca.markets/signup](https://app.alpaca.markets/signup) and create a free account (no credit card or deposit required).
2. In the top-left corner of the Alpaca dashboard, make sure the yellow/paper toggle says **Paper Trading** (not Live).
3. On the right side under **API Keys**, click **View API Keys** -> click **Generate New Key**.
4. Copy both values right away (the Secret Key is only shown once):
   - **API Key ID** (starts with `PK...`) -> this is your `ALPACA_API_KEY`
   - **Secret Key** -> this is your `ALPACA_SECRET_KEY`

### Step 2: Get Your Free Google Gemini API Key (1 minute)
1. Go to **Google AI Studio**: [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey) and sign in with your Google account.
2. Click the blue **Create API key** button.
3. Copy the key (starts with `AIza...`) -> this is your `GEMINI_API_KEY`.

### Step 3: Save Keys Locally in `.env` (For Local Verification)
1. In the project folder (`ai-trading-arena`), copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Open `.env` in Notepad or your editor and paste your 3 keys:
   ```ini
   ALPACA_API_KEY=PKxxxxxxxxxxxxxxxxxx
   ALPACA_SECRET_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ALPACA_BASE_URL=https://paper-api.alpaca.markets
   GEMINI_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
   ```
   *(Never share or commit `.env` — `.gitignore` automatically blocks it from Git.)*

### Step 4: Add Your 3 Secrets to GitHub Actions
1. Open your GitHub repository in your browser.
2. Click the **Settings** tab at the top of your repository.
3. In the left sidebar, click **Secrets and variables** -> **Actions**.
4. Click the green **New repository secret** button and add these 3 secrets one by one:
   - Name: `ALPACA_API_KEY` -> Value: *(paste your Alpaca Paper Key ID)* -> Click **Add secret**
   - Name: `ALPACA_SECRET_KEY` -> Value: *(paste your Alpaca Paper Secret Key)* -> Click **Add secret**
   - Name: `GEMINI_API_KEY` -> Value: *(paste your Google AI Studio Gemini Key)* -> Click **Add secret**

### Step 5: Turn On GitHub Pages for Your Live Dashboard
1. In your GitHub repository, click **Settings** -> in the left sidebar, click **Pages**.
2. Under **Build and deployment**:
   - **Source**: Select **Deploy from a branch**
   - **Branch**: Select **`main`**, and change the folder dropdown right next to it from `/(root)` to **`/docs`**
   - Click **Save**
3. Wait ~60 seconds and refresh that page. You will see a banner at the top saying:
   `Your site is live at https://<your-github-username>.github.io/<repo-name>/`

### Step 6: Test the Workflow with One Click
1. Click the **Actions** tab at the top of your GitHub repository.
2. On the left, click **AI Trading Arena — 30-Min Paper Trading Cycle**.
3. On the right, click the **Run workflow** dropdown button -> click the green **Run workflow** button.
4. Watch it turn green in ~30 seconds! From now on, GitHub Actions will run automatically every 30 minutes during US market hours even when your computer is turned off.

---

## CLI Commands (Local Usage)

Run all commands from the project root with `PYTHONPATH=src`:

```bash
# 1. Verify your .env keys, Alpaca Paper clock/calendar, Gemini Flash API, and strategy hashes
PYTHONPATH=src python -m arena.cli verify-setup

# 2. Run the 5-trading-day historical dry-run replay (builds docs_dry_run/ and docs/)
PYTHONPATH=src python -m arena.cli dry-run

# 3. Run the 5-day dry run and immediately reset live state/ & docs/ back to $10,000 Day 0
PYTHONPATH=src python -m arena.cli dry-run --reset-after

# 4. Reset all 10 agents cleanly to $10,000.00 cash, 0 trades, Day 0/10
PYTHONPATH=src python -m arena.cli reset

# 5. Run a single live cycle (checks Alpaca market clock first)
PYTHONPATH=src python -m arena.cli run-cycle

# 6. Run the unit test suite (ledger, simulator, risk manager)
PYTHONPATH=src python -m unittest discover -s tests -v
```
