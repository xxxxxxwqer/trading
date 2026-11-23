"""Configuration, liquid US stock/ETF universe, risk parameters, and agent metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = PROJECT_ROOT / "state"
AGENTS_STATE_DIR = STATE_DIR / "agents"
DOCS_DIR = PROJECT_ROOT / "docs"
DRY_RUN_DOCS_DIR = PROJECT_ROOT / "docs_dry_run"

# Hard safety rule: ONLY Alpaca paper endpoint is allowed.
ALLOWED_ALPACA_TRADING_BASE_URLS = {
    "https://paper-api.alpaca.markets",
    "https://paper-api.alpaca.markets/",
    "https://paper-api.alpaca.markets/v2",
}
ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_DATA_BASE_URL = "https://data.alpaca.markets"

# Competition parameters
INITIAL_CASH: float = 10_000.0
COMPETITION_TRADING_DAYS: int = 10

# Shared Professional Risk Manager Rules
MAX_RISK_PER_TRADE_PCT: float = 0.01       # At most 1% of agent equity at risk per trade
MAX_POSITION_EQUITY_PCT: float = 0.20      # At most 20% of agent equity in a single position
MAX_OPEN_POSITIONS: int = 5                # At most 5 open positions at any time
DAILY_LOSS_LIMIT_PCT: float = -0.03        # -3% daily loss halts new entries for the rest of that US trading day
MAX_TOTAL_DRAWDOWN_PCT: float = -0.10      # -10% drawdown from peak equity permanently stops agent ("BLOWN_UP")
MIN_STOP_DISTANCE_PCT: float = 0.002       # Minimum 0.2% stop distance to prevent division-by-near-zero sizing

# Transaction Costs
COMMISSION_DOLLARS: float = 0.0            # $0 commission
SLIPPAGE_BPS: float = 5.0                  # 5 basis points (0.05%) on every buy and sell
SLIPPAGE_RATE: float = SLIPPAGE_BPS / 10_000.0
DEFAULT_SPREAD_BPS: float = 4.0            # Fallback 4 bps full bid-ask spread when live quote spread is unavailable

# Liquid US Large-Cap Stocks & Major ETFs Universe (64 tickers)
SECTOR_ETFS: List[str] = [
    "XLK",  # Technology
    "XLF",  # Financials
    "XLV",  # Health Care
    "XLE",  # Energy
    "XLY",  # Consumer Discretionary
    "XLP",  # Consumer Staples
    "XLI",  # Industrials
    "XLU",  # Utilities
    "XLB",  # Materials
    "XLRE", # Real Estate
    "XLC",  # Communication Services
]

INDEX_AND_MACRO_ETFS: List[str] = [
    "SPY",  # S&P 500 Benchmark
    "QQQ",  # Nasdaq 100
    "IWM",  # Russell 2000
    "DIA",  # Dow Jones Industrial Average
    "TLT",  # 20+ Year Treasury Bond
    "GLD",  # Gold Trust
    "SMH",  # Semiconductor ETF
]

LIQUID_LARGE_CAP_STOCKS: List[str] = [
    # Mega-cap Tech & AI
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD", "ORCL",
    "CRM", "ADBE", "NFLX", "INTC", "QCOM", "MU", "PLTR", "PANW",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "BRK.B", "AXP", "BLK",
    # Healthcare & Pharma
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ISRG", "PFE",
    # Consumer & Retail
    "WMT", "COST", "HD", "PG", "KO", "PEP", "MCD", "NKE", "DIS",
    # Industrials, Energy & Defense
    "XOM", "CVX", "CAT", "GE", "HON", "LMT", "BA", "UNP",
]

UNIVERSE: List[str] = sorted(list(set(INDEX_AND_MACRO_ETFS + SECTOR_ETFS + LIQUID_LARGE_CAP_STOCKS)))
UNIVERSE_SET = frozenset(UNIVERSE)


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    name: str
    category: str  # "Coded Strategy", "LLM Trader (Gemini Flash)", "Control / Benchmark"
    short_description: str
    playbook: str
    flatten_eod: bool = False
    bypass_position_cap: bool = False  # Only True for passive SPY benchmark if needed


AGENT_SPECS: List[AgentSpec] = [
    AgentSpec(
        agent_id="coded_trend",
        name="1. Trend Follower (MA / Breakout)",
        category="Coded Strategy",
        short_description="Buys liquid stocks breaking above 20-bar highs with 10-SMA > 30-SMA momentum confirmation.",
        playbook=(
            "Systematic trend-following strategy. Scans the universe for stocks where the fast SMA(10) is above "
            "the slow SMA(30), price breaks above the 20-bar high with above-average volume, and sets a trailing/initial "
            "stop-loss 2x ATR below entry with a 3x ATR profit target."
        ),
    ),
    AgentSpec(
        agent_id="coded_mean_rev",
        name="2. Mean Reversion (RSI / Bollinger)",
        category="Coded Strategy",
        short_description="Buys oversold dips when RSI(14) < 32 and price touches or pierces the lower Bollinger Band.",
        playbook=(
            "Systematic mean-reversion strategy. Looks for quality large-cap stocks experiencing temporary intraday "
            "oversold conditions (RSI(14) < 32 and price near/below the 20-period lower Bollinger Band) while still in "
            "an overarching structural uptrend. Exits when price reverts to the 20-period middle band or RSI > 60."
        ),
    ),
    AgentSpec(
        agent_id="coded_orb",
        name="3. Intraday ORB (Flat by Close)",
        category="Coded Strategy",
        short_description="Trades breakouts above the first 30-minute opening range and liquidates all positions before the closing bell.",
        playbook=(
            "Intraday Opening Range Breakout (ORB). Establishes the high and low of the first 30-minute bar of the US "
            "session (9:30–10:00 AM ET). Buys when price breaks above the opening range high on strong volume with a "
            "stop at the opening range midpoint. Automatically flattens all positions in the final cycle before market close."
        ),
        flatten_eod=True,
    ),
    AgentSpec(
        agent_id="coded_sector",
        name="4. Sector ETF Rotation",
        category="Coded Strategy",
        short_description="Rotates into the top SPDR Sector ETFs exhibiting the strongest relative strength vs SPY.",
        playbook=(
            "Relative-strength sector rotation across the 11 SPDR Sector ETFs (XLK, XLF, XLV, XLE, XLY, XLP, XLI, XLU, "
            "XLB, XLRE, XLC). Computes risk-adjusted return over the lookback window relative to SPY, holds the top "
            "leaders with positive momentum, and rotates out of laggards."
        ),
    ),
    AgentSpec(
        agent_id="llm_technical",
        name="5. Technical Analyst (Gemini Flash)",
        category="LLM Trader (Gemini Flash)",
        short_description="AI chartist analyzing price action, volume anomalies, RSI/Bollinger confluence, and support/resistance.",
        playbook=(
            "You are a disciplined Chartered Market Technician (CMT). Focus strictly on price action, trend structure, "
            "support/resistance levels, volume surges, RSI, and Bollinger Bands. Only enter when reward-to-risk to the "
            "nearest resistance vs support stop is at least 2:1. Never chase extended vertical moves."
        ),
    ),
    AgentSpec(
        agent_id="llm_catalyst",
        name="6. News & Catalyst Trader (Gemini Flash)",
        category="LLM Trader (Gemini Flash)",
        short_description="AI event-driven trader reacting to company headlines, earnings catalysts, and momentum shifts.",
        playbook=(
            "You are a sharp event-driven and news catalyst trader. Prioritize stocks with fresh positive headlines, "
            "earnings beats, product launches, or analyst upgrades confirmed by positive price/volume reaction. "
            "If headlines are bearish or absent, stay patient or trim weakening positions."
        ),
    ),
    AgentSpec(
        agent_id="llm_macro",
        name="7. Macro & Sector Strategist (Gemini Flash)",
        category="LLM Trader (Gemini Flash)",
        short_description="AI top-down strategist reading SPY/QQQ/TLT/GLD regime signals to allocate into favored sectors and leaders.",
        playbook=(
            "You are a top-down global macro and sector strategist. First diagnose the market regime using SPY (equities), "
            "QQQ (growth), IWM (small caps), TLT (rates/bonds), and GLD (gold). In risk-on regimes, buy top sector ETFs "
            "and mega-cap leaders; in risk-off or rising-yield regimes, rotate into defensive sectors (XLV, XLP, XLU, GLD) or cash."
        ),
    ),
    AgentSpec(
        agent_id="llm_cautious",
        name="8. Cautious Risk-First Trader (Gemini Flash)",
        category="LLM Trader (Gemini Flash)",
        short_description="AI capital-preservation specialist that stays mostly in cash and only takes A+ asymmetric setups.",
        playbook=(
            "You are an ultra-disciplined, risk-averse portfolio manager inspired by Paul Tudor Jones and Seth Klarman. "
            "Capital preservation is your #1 rule. Keep most of your portfolio in cash. Only enter a trade when technical "
            "trend, low volatility, and favorable sentiment align with a tight stop-loss and at least 2.5:1 reward-to-risk."
        ),
    ),
    AgentSpec(
        agent_id="ctrl_spy",
        name="9. Buy-and-Hold SPY (Benchmark)",
        category="Control / Benchmark",
        short_description="Passive S&P 500 index benchmark that buys SPY on Day 1 and holds until the end of Day 10.",
        playbook=(
            "Passive index benchmark control. Allocates virtual capital into SPY at the first trading cycle and holds "
            "passively through the 10-day competition so every active agent can be measured against the S&P 500."
        ),
        bypass_position_cap=True,
    ),
    AgentSpec(
        agent_id="ctrl_random",
        name="10. Random Trader (Luck Control)",
        category="Control / Benchmark",
        short_description="Randomly buys and sells liquid universe stocks within the shared risk rules to show what luck alone produces.",
        playbook=(
            "Null-hypothesis scientific control. Uses a seeded pseudo-random generator each cycle to randomly hold, buy "
            "a random universe stock with a random 1.5%–3.5% stop-loss and 2x take-profit, or exit an existing position—all "
            "subject to the exact same shared risk manager as the AI and coded agents."
        ),
    ),
    AgentSpec(
        agent_id="style_berkshire",
        name="11. Berkshire Style (13F Pattern)",
        category="Filing-Style Learned",
        short_description="Learns Berkshire Hathaway's 5-year 13F buying style (sectors, size, valuation, 52w-high distance, 3m trend, dividend, concentration) and applies those rules to today's universe without copying filings.",
        playbook=(
            "Never buys a stock because it appeared in a 13F filing, and never infers timing from quarterly 13F dates. "
            "Measures Berkshire Hathaway's 5-year 13F style strictly from filings public before launch, converts the pattern "
            "into explicit rules (preferred sectors, large/mega-cap size, reasonable P/E, 52-week high pullback window, "
            "steady 3-month trend, cash dividend, concentrated low-turnover holds), and applies those rules to our liquid universe "
            "under the shared risk manager."
        ),
    ),
    AgentSpec(
        agent_id="style_congress",
        name="12. Congress Style (House PTR Pattern)",
        category="Filing-Style Learned",
        short_description="Learns US House members' 2-year stock-buying style (sectors, size, 30-day pre-trade move, typical buy-to-sell hold time) from cached PTR PDFs and applies those rules to today's universe without copying trades.",
        playbook=(
            "Never buys a stock because a member of Congress traded it. Parses 2 years of US House PTR PDFs (shares only, "
            "all members, filings public before launch) once into cache to measure sector mix, company size, 30-day pre-trade "
            "price momentum, and typical purchase-to-sale hold time, then screens our liquid universe with those explicit rules "
            "under the shared risk manager."
        ),
    ),
    AgentSpec(
        agent_id="llm_own_strategy",
        name="13. Own Strategy (Autonomous Gemini Flash)",
        category="Autonomous LLM (Gemini Flash)",
        short_description="Self-directed Gemini Flash trader given NO persona and NO suggested strategy — authors its own plain-language rulebook blindly before launch and may revise it once daily after market close.",
        playbook=(
            "Pre-declared autonomous strategy: given ONLY starting cash ($10,000), hard risk/universe rules "
            "(US stocks >$5, >1M avg daily volume, 1% risk/trade, 20% max position, max 5 positions, -3% daily / -10% total limits), "
            "available data fields, 10 trading days, and the goal of highest net return. Authors its own rulebook blindly without "
            "seeing dry-run prices, sees only its own rulebook/positions/trades (never other agents or leaderboard), may tighten "
            "(never loosen) limits, and is explicitly permitted to review results and revise its rulebook once per day AFTER market close."
        ),
    ),
]

AGENT_SPECS_BY_ID: Dict[str, AgentSpec] = {spec.agent_id: spec for spec in AGENT_SPECS}


def assert_paper_trading_only(base_url: str | None = None) -> str:
    """Enforce the hard rule that only Alpaca's paper-trading endpoint may ever be used."""
    url = (base_url or os.environ.get("ALPACA_BASE_URL") or ALPACA_PAPER_BASE_URL).strip().rstrip("/")
    if "paper-api.alpaca.markets" not in url or "api.alpaca.markets" == url.replace("https://", ""):
        raise ValueError(
            f"HARD RULE VIOLATION: Non-paper Alpaca endpoint '{url}' is strictly forbidden. "
            f"Only '{ALPACA_PAPER_BASE_URL}' is allowed."
        )
    return url
