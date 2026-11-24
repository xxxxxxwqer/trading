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
COMPETITION_TRADING_DAYS: int = 30

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
        short_description="Passive S&P 500 index benchmark that buys SPY on Day 1 and holds until the final trading day.",
        playbook=(
            "Passive index benchmark control. Allocates virtual capital into SPY at the first trading cycle and holds "
            "passively through the 30-day competition so every active agent can be measured against the S&P 500."
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
        agent_id="claude_rmp",
        name="14. Claude Residual-Momentum Pullback",
        category="Coded Strategy",
        short_description="Deterministic coded strategy designed by Claude: 4-factor SPY/breadth/vol Regime Gate, 63-day beta-neutral residual-momentum Top 10 leaders, ATR/RSI(2) pullback entries, HeatCap drawdown-adaptive sizing, and R-based trailing/time/SMA50/regime exits.",
        playbook=(
            "100% deterministic, zero-LLM-at-runtime coded strategy with SHA-256 locked code and params.json. "
            "Trades individual US stocks only (no ETFs; SPY used solely for regime & beta). Daily 4-factor Regime Gate "
            "(C1..C4) sets max slots (5/3/1/0) plus -1.25% intraday SPY crash guard. Ranks stocks by 58-day intercept-free "
            "residual momentum skipping the last 5 days, enters Top 10 pullbacks (E1..E6 with 24h news keyword veto), sizes via "
            "drawdown-adaptive HeatCap (0.75% target risk, -2% daily halt), and manages exits via stop ratchet, 5-day time stop, "
            "SMA50 break, and Regime-OFF rules. No new entries on the final trading day."
        ),
    ),
    AgentSpec(
        agent_id="kiro_pair_reversion",
        name="15. Kiro Pair-Spread Reversion (PSR)",
        category="Coded Strategy",
        short_description="Deterministic coded pair-reversion strategy designed by Kiro: 12 fixed same-industry large-cap pairs, 10-day beta-neutral residual spread >= 2 sigma laggard long entries, 3-tier SPY regime filter, 24h both-leg news veto, and strict 10-level exit priority.",
        playbook=(
            "100% deterministic, zero-LLM-at-runtime coded strategy with SHA-256 locked code and params.json. "
            "Monitors 12 pre-fixed same-industry large-cap equity pairs (long-only individual stocks; SPY used only for CRASH/RISK_OFF/NORMAL regime). "
            "Uses D-1 completed daily bars for 60-day beta/correlation/sigma_e and 9-day residual sum S_9, combines with intraday residual z_live, "
            "enters laggard when 2.0 <= |lz| <= 4.0 with 30-min bar confirmation, edge >= 3x cost, no 24h news veto, drawdown-scaled risk (0.60%/0.40%), "
            "2.5% max open risk, +1R breakeven stop ratchet, and strict 1->10 exit priority."
        ),
    ),
    # --- Part B-A: Time-of-Day & Calendar Effect Agents (#16-#20) ---
    AgentSpec(
        agent_id="overnight_drift",
        name="16. Overnight Drift (Close→Open)",
        category="Coded Strategy",
        short_description="Buys SPY and QQQ at the 15:30 ET close window and unwinds at next morning's open to capture the overnight equity drift premium.",
        playbook="Enters SPY & QQQ at 15:30 ET (12:30 ET early close) with -3% stop and sells at <= 10:00 ET the next trading day.",
    ),
    AgentSpec(
        agent_id="eod_loser_overnight",
        name="17. EOD Loser Overnight Reversal",
        category="Coded Strategy",
        short_description="Buys up to 3 non-news large-cap stocks down >= 2.5% intraday at 15:30 ET and sells after 10:30 ET the next morning.",
        playbook="Scans 53 individual stocks at 15:30 ET for intraday drops <= -2.5% with no 24h veto news (max 1/sector, -4% stop / +4% TP), unwinding next morning >= 10:30 ET.",
    ),
    AgentSpec(
        agent_id="intraday_momentum",
        name="18. Intraday Momentum (Gao 2018)",
        category="Coded Strategy",
        short_description="If SPY's overnight + first 30-min return is positive (>= +0.15%), buys SPY/QQQ at 15:00 ET and flattens before market close.",
        playbook="Implements Gao et al. (2018) intraday momentum: enters at 15:00 ET (12:00 ET early close) only when morning return >= +0.15%, with -1.5% stop and mandatory EOD flatten.",
        flatten_eod=True,
    ),
    AgentSpec(
        agent_id="turn_of_month",
        name="19. Turn-of-Month Calendar",
        category="Coded Strategy",
        short_description="Holds SPY and QQQ only from the last trading day of each month through the 3rd trading day of the new month; 100% cash otherwise.",
        playbook="Calendar liquidity effect: enters SPY/QQQ on last trading day of month through day 3 of the following month (-4.5% stop), holding cash on all other days.",
    ),
    AgentSpec(
        agent_id="gap_down_fill",
        name="20. Intraday Gap-Down Fill",
        category="Coded Strategy",
        short_description="Buys non-news stocks that gap down -1.5% to -4.0% with a green first 30-min bar, targeting prior close and flattening by EOD.",
        playbook="Scans 53 stocks at 10:00-11:30 ET for -1.5% to -4.0% open gaps without veto news and green opening 30m bar; targets prior close, flattens EOD.",
        flatten_eod=True,
    ),
    # --- Part B-B: Asset Allocation & Risk Premia Agents (#21-#25) ---
    AgentSpec(
        agent_id="dual_momentum_macro",
        name="21. Dual Momentum Macro ETF",
        category="Coded Strategy",
        short_description="Weekly 60-day absolute + relative momentum rotation across SPY, QQQ, IWM, TLT, and GLD (top 2 positive ETFs, else cash).",
        playbook="Ranks SPY/QQQ/IWM/TLT/GLD weekly by D-1 60-day return; holds top 2 with positive absolute momentum (-5% stop), moving to cash if none are positive.",
    ),
    AgentSpec(
        agent_id="risk_parity_3",
        name="22. Risk Parity 3-Asset (8% Vol)",
        category="Coded Strategy",
        short_description="Weekly inverse-volatility allocation across SPY, TLT, and GLD scaled to an 8% annualized volatility target.",
        playbook="Computes 20-day realized volatility on D-1 daily bars for SPY/TLT/GLD, allocates inversely to vol scaled to 8% target vol (-4.5% stop), rebalanced weekly.",
    ),
    AgentSpec(
        agent_id="min_variance_etf",
        name="23. Minimum-Variance ETF Portfolio",
        category="Coded Strategy",
        short_description="Weekly 60-day covariance minimum-variance long-only optimization across all 18 ETFs (top 5 holdings).",
        playbook="Solves deterministic long-only minimum-variance weights over 60-day daily returns of all 18 ETFs (ridge-regularized), holding top 5 weekly (-4.5% stop).",
    ),
    AgentSpec(
        agent_id="low_vol_anomaly",
        name="24. Low-Volatility Stock Anomaly",
        category="Coded Strategy",
        short_description="Weekly equal-weight portfolio of the 5 lowest 60-day realized-volatility large-cap stocks (max 2 per sector).",
        playbook="Exploits the low-volatility anomaly across 53 stocks using D-1 60-day realized volatility; holds bottom 5 vol stocks (19% each, -5% stop, max 2/sector).",
    ),
    AgentSpec(
        agent_id="megacap5_hold",
        name="25. Mega-Cap Top-5 Buy & Hold (Control)",
        category="Control / Benchmark",
        short_description="Concentrated control benchmark: buys the top 5 largest-cap stocks (19% each, -5% stop) on Day 1 and holds.",
        playbook="Null benchmark for concentrated mega-cap exposure: allocates 19% each into the top 5 market-cap US stocks on Day 1 with -5% stop and holds.",
    ),
    # --- Part B-C: Cross-Sectional Factor Agents (#26-#30) ---
    AgentSpec(
        agent_id="xs_short_reversal",
        name="26. Cross-Sectional 5D Short Reversal",
        category="Coded Strategy",
        short_description="Daily rebalance into the 5 worst 5-day performers among 53 large-caps (max 1 per sector, no veto news).",
        playbook="Exploits short-term cross-sectional reversal: daily at 10:30-11:30 ET rotates into bottom 5 non-news 5-day losers across distinct GICS sectors (19% each, -5% stop).",
    ),
    AgentSpec(
        agent_id="high52_anchor",
        name="27. 70D High Anchoring (George-Hwang)",
        category="Coded Strategy",
        short_description="Weekly top 5 stocks closest to their dynamic 70-day high with positive 63-day relative strength vs SPY.",
        playbook="George & Hwang (2004) anchoring strategy: filters 53 stocks for 63d RS > 0 vs SPY, ranks by proximity to dynamic 70-day high, holds top 5 weekly (19% each, -5% stop).",
    ),
    AgentSpec(
        agent_id="value_momentum",
        name="28. Value + 63D Momentum Combo",
        category="Coded Strategy",
        short_description="Weekly intersection of cheaper half by P/E ratio and top 5 strongest 63-day price momentum stocks.",
        playbook="Filters 53 large-caps to bottom 50% by positive P/E ratio, selects top 5 by D-1 63-day positive momentum, rebalanced weekly (19% each, -5% stop).",
    ),
    AgentSpec(
        agent_id="volume_accumulation",
        name="29. 20D Up/Down Volume Accumulation",
        category="Coded Strategy",
        short_description="Weekly top 5 stocks above SMA20 with the highest 20-day up-day to down-day volume accumulation ratio.",
        playbook="Measures institutional accumulation via 20-day up-day/down-day volume ratio on stocks trading above D-1 SMA20; holds top 5 weekly (19% each, -5% stop).",
    ),
    AgentSpec(
        agent_id="regime_beta_switch",
        name="30. Regime-Adaptive Beta Switch",
        category="Coded Strategy",
        short_description="Holds top 5 highest 63-day beta stocks in RISK_ON (SPY > SMA20 & low vol), rotating to bottom 5 lowest-beta stocks in RISK_OFF.",
        playbook="Computes D-1 63-day beta vs SPY for 53 stocks; holds 5 highest-beta names when SPY > SMA20 & RV20 < 25%, switching to 5 lowest-beta defensive stocks otherwise.",
    ),
    # --- Part B-D: Market Timing & Sentiment Agents (#31-#33) ---
    AgentSpec(
        agent_id="breadth_timing",
        name="31. Market Breadth Equity Timing",
        category="Coded Strategy",
        short_description="Uses % of 53 stocks above SMA20 to step equity ETF exposure between ~95% (>=60%), ~50% (40-60%), and 0% Cash (<40%).",
        playbook="Daily breadth monitor: >=60% above SMA20 holds 5 broad ETFs (~95% equity); 40-60% holds 3 ETFs (~50%); <40% moves to 100% cash (-4.5% stop).",
    ),
    AgentSpec(
        agent_id="panic_dip_index",
        name="32. Panic-Dip Index Buyer",
        category="Coded Strategy",
        short_description="Buys SPY and QQQ in tranches when SPY drops >= 3% over 3 days, holding up to 5 trading days or +5% target.",
        playbook="Contrarian index panic buyer: enters tranche 1 in SPY/QQQ on 3-day SPY drop <= -3% (tranche 2 on extra -1.5% dip), exits after 5 trading days or +5% TP (-4.5% stop).",
    ),
    AgentSpec(
        agent_id="risk_appetite_ratio",
        name="33. Cross-Asset Risk Appetite Ratio",
        category="Coded Strategy",
        short_description="Scores XLY/XLP, SMH/SPY, and IWM/SPY vs 20d SMA to switch between growth ETFs, defensive ETFs (XLP/XLU/GLD/TLT), and Cash.",
        playbook="Daily 3-ratio risk-appetite composite: score 2-3 holds QQQ/SMH/XLY/XLK; score 1 rotates to XLP/XLU/GLD/TLT; score 0 holds 100% cash (-4.5% stop).",
    ),
    # --- Part B-E: Intraday Microstructure Agents (#34-#35) ---
    AgentSpec(
        agent_id="vwap_reversion",
        name="34. Intraday VWAP -2σ Reversion",
        category="Coded Strategy",
        short_description="On range-bound SPY days (|move| <= 0.75%), buys stocks below session VWAP - 2σ, targeting VWAP reversion and flattening EOD.",
        playbook="Computes cumulative intraday VWAP & std from 30-min bars; enters up to 3 non-news stocks below VWAP - 2σ (10:30-14:30 ET), exits on VWAP touch and flattens EOD.",
        flatten_eod=True,
    ),
    AgentSpec(
        agent_id="squeeze_breakout",
        name="35. Volatility Squeeze Breakout",
        category="Coded Strategy",
        short_description="Identifies stocks with D-1 daily BB width in the bottom 20% of their 60-day range and buys intraday 30m upper-band breakouts.",
        playbook="Daily D-1 Bollinger Band width compression filter (<=20th percentile of 60d); enters intraday upper BB breakouts with vol ratio >= 1.2x, ATR stop, +6% TP, 5-day max hold.",
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
