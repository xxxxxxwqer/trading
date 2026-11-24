"""Historical 30-minute market replay provider with strict zero look-ahead.

Supports both:
1. Real historical 30-minute bars/quotes/news from Alpaca Paper/Data API when credentials are present.
2. Deterministic, realistic 5-trading-day 30-minute synthetic replay (plus 5 warmup days)
   across the 64-ticker liquid US universe when running offline or before API keys are configured.
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterator, List, Optional, Tuple

from arena.config import (
    DEFAULT_SPREAD_BPS,
    INDEX_AND_MACRO_ETFS,
    LIQUID_LARGE_CAP_STOCKS,
    SECTOR_ETFS,
    UNIVERSE,
)
from arena.data.alpaca_client import (
    AlpacaDataClient,
    compute_indicators_from_bars,
    compute_is_near_market_close,
    compute_regime_summary,
    et_wall_to_utc,
    format_iso_utc,
    get_session_hours_et,
    is_us_trading_day,
    parse_iso_utc,
    to_et_datetime,
)
from arena.models import Bar, MarketSnapshot, NewsHeadline, Quote, TickerIndicators


# Realistic baseline prices for the 64-ticker liquid universe
BASELINE_PRICES: Dict[str, float] = {
    # Index & Macro ETFs
    "SPY": 585.0, "QQQ": 505.0, "IWM": 226.0, "DIA": 435.0,
    "TLT": 92.5, "GLD": 252.0, "SMH": 258.0,
    # 11 SPDR Sector ETFs
    "XLK": 232.0, "XLF": 48.5, "XLV": 148.0, "XLE": 91.0,
    "XLY": 208.0, "XLP": 81.5, "XLI": 138.0, "XLU": 79.0,
    "XLB": 94.0, "XLRE": 43.0, "XLC": 94.5,
    # Mega-cap Tech & AI
    "AAPL": 228.0, "MSFT": 428.0, "NVDA": 142.0, "GOOGL": 182.0,
    "AMZN": 210.0, "META": 595.0, "TSLA": 320.0, "AVGO": 178.0,
    "AMD": 148.0, "ORCL": 186.0, "CRM": 325.0, "ADBE": 515.0,
    "NFLX": 825.0, "INTC": 25.0, "QCOM": 168.0, "MU": 106.0,
    "PLTR": 58.0, "PANW": 388.0,
    # Financials
    "JPM": 242.0, "BAC": 45.5, "WFC": 72.0, "GS": 585.0,
    "MS": 131.0, "V": 308.0, "MA": 522.0, "BRK.B": 458.0,
    "AXP": 296.0, "BLK": 1025.0,
    # Healthcare & Pharma
    "LLY": 845.0, "UNH": 592.0, "JNJ": 156.0, "ABBV": 176.0,
    "MRK": 102.0, "TMO": 545.0, "ISRG": 532.0, "PFE": 26.8,
    # Consumer & Retail
    "WMT": 85.0, "COST": 935.0, "HD": 408.0, "PG": 168.0,
    "KO": 64.5, "PEP": 162.0, "MCD": 298.0, "NKE": 78.5,
    "DIS": 112.0,
    # Industrials, Energy & Defense
    "XOM": 119.0, "CVX": 158.0, "CAT": 395.0, "GE": 184.0,
    "HON": 226.0, "LMT": 545.0, "BA": 152.0, "UNP": 244.0,
}

# Realistic spread in bps by liquidity tier
TIGHT_SPREAD_TICKERS = {"SPY", "QQQ", "IWM", "DIA", "TLT", "GLD", "AAPL", "MSFT", "NVDA", "AMZN", "META", "TSLA"}


def _ticker_seed(ticker: str, salt: int = 2026) -> int:
    digest = hashlib.sha256(f"{ticker}:{salt}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def get_recent_us_trading_days(num_days: int, end_date: Optional[date] = None) -> List[date]:
    """Return the most recent `num_days` valid US equity trading dates in ascending order."""
    cur = end_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    days: List[date] = []
    while len(days) < num_days:
        if is_us_trading_day(cur):
            days.append(cur)
        cur -= timedelta(days=1)
    days.reverse()
    return days


def get_session_30min_timestamps_utc(trading_day: date) -> List[str]:
    """Return the 30-minute decision bar timestamps (09:30 ET .. 15:30 ET, or 12:30 ET on early close)."""
    open_str, close_str = get_session_hours_et(trading_day)
    close_h, close_m = (int(x) for x in close_str.split(":"))
    close_total_min = close_h * 60 + close_m

    timestamps: List[str] = []
    cur_min = 9 * 60 + 30  # 09:30 ET
    while cur_min + 30 <= close_total_min:
        # Decision timestamp at the completion of each 30-minute bar: 10:00, 10:30, ..., 16:00 ET
        # Using bar start timestamps 09:30 .. 15:30 ET (13 bars per regular session)
        h = cur_min // 60
        m = cur_min % 60
        dt_utc = et_wall_to_utc(trading_day.year, trading_day.month, trading_day.day, h, m)
        timestamps.append(format_iso_utc(dt_utc))
        cur_min += 30
    return timestamps


class HistoricalReplayProvider:
    """Provides 30-minute historical market snapshots and next-bar fill prices with zero look-ahead."""

    def __init__(
        self,
        universe: Optional[List[str]] = None,
        client: Optional[AlpacaDataClient] = None,
        offline: bool = False,
        seed: int = 42,
        reference_end_date: Optional[date] = None,
    ) -> None:
        self.universe: List[str] = list(universe) if universe is not None else list(UNIVERSE)
        self.client = client or AlpacaDataClient()
        self.offline = offline
        self.seed = seed
        self.reference_end_date = reference_end_date or date(2026, 11, 11)

    def _build_synthetic_dataset(
        self,
        num_days: int = 5,
        warmup_days: int = 5,
    ) -> Tuple[List[date], Dict[str, List[Bar]], Dict[str, List[Quote]], List[NewsHeadline]]:
        """Generate a deterministic, realistic 30-minute dataset with warmup history and rich setups."""
        total_days = warmup_days + num_days
        all_days = get_recent_us_trading_days(total_days, end_date=self.reference_end_date)
        replay_days = all_days[-num_days:]

        # Build all 30-min timestamps across warmup + replay days, plus 1 post-close fill bar on the final day
        all_timestamps: List[Tuple[date, int, str]] = []
        for d in all_days:
            day_ts = get_session_30min_timestamps_utc(d)
            for idx, ts in enumerate(day_ts):
                all_timestamps.append((d, idx, ts))

        # Add one extra post-session bar 5 minutes after the final replay bar so the last cycle has a valid next fill bar
        last_day = replay_days[-1]
        last_ts_dt = parse_iso_utc(all_timestamps[-1][2])
        extra_fill_ts = format_iso_utc(last_ts_dt + timedelta(minutes=5))
        all_timestamps.append((last_day, 13, extra_fill_ts))

        # Assign deterministic role archetypes so all strategies (Trend, MeanRev, ORB, Sector, Catalyst) see realistic setups
        trend_leaders = {"NVDA", "AVGO", "META", "PLTR", "CAT", "LLY", "GE", "AXP", "XLK", "XLI", "XLF", "SMH", "ORCL", "NFLX"}
        mean_rev_dippers = {"MSFT", "AAPL", "AMZN", "COST", "V", "JPM", "CRM", "ISRG", "ADBE", "PANW", "UNH", "MA"}
        orb_breakout_tickers = {"TSLA", "AMD", "NVDA", "PLTR", "MU", "GS", "META", "AVGO", "QQQ", "QCOM"}
        defensive_tickers = {"TLT", "GLD", "XLP", "XLU", "XLV", "PG", "KO", "PEP", "JNJ", "WMT"}

        bars_by_ticker: Dict[str, List[Bar]] = {sym: [] for sym in self.universe}
        quotes_by_ticker: Dict[str, List[Quote]] = {sym: [] for sym in self.universe}

        # First generate market factor path (SPY drift + macro waves)
        market_rng = random.Random(self.seed + 101)
        market_returns: List[float] = []
        for step_idx, (d, bar_in_day, _) in enumerate(all_timestamps):
            # Mild upward macro drift with a Day-3 morning pullback and Day-4/5 recovery rally
            day_num = all_days.index(d) - warmup_days  # 0..num_days-1 for replay days
            base_drift = 0.00035
            if day_num == 2 and bar_in_day in (1, 2, 3, 4):
                base_drift = -0.0018  # Mid-week dip for mean-reversion & macro regime testing
            elif day_num in (0, 1, 3, 4) and bar_in_day in (1, 2, 3, 4, 5):
                base_drift = 0.00075
            noise = market_rng.gauss(0.0, 0.0014)
            market_returns.append(base_drift + noise)

        for sym in self.universe:
            rng = random.Random(_ticker_seed(sym, self.seed))
            price = BASELINE_PRICES.get(sym, 120.0)
            base_vol = 420_000.0 if sym in TIGHT_SPREAD_TICKERS else 185_000.0
            spread_bps = 2.0 if sym in TIGHT_SPREAD_TICKERS else DEFAULT_SPREAD_BPS

            for step_idx, (d, bar_in_day, ts) in enumerate(all_timestamps):
                day_offset = all_days.index(d) - warmup_days  # < 0 in warmup, 0..num_days-1 in replay
                mkt_ret = market_returns[step_idx]

                # Base beta to market
                if sym == "SPY":
                    beta = 1.0
                    idiosyncratic = rng.gauss(0.00005, 0.0004)
                elif sym == "QQQ" or sym == "SMH":
                    beta = 1.18
                    idiosyncratic = rng.gauss(0.00012, 0.0007)
                elif sym in ("TLT", "GLD"):
                    beta = -0.25 if sym == "TLT" else 0.15
                    idiosyncratic = rng.gauss(0.0001, 0.0009)
                elif sym in defensive_tickers:
                    beta = 0.55
                    idiosyncratic = rng.gauss(0.00008, 0.0008)
                else:
                    beta = 1.05
                    idiosyncratic = rng.gauss(0.0001, 0.0016)

                vol_mult = 1.0 + abs(rng.gauss(0.0, 0.25))

                # Pattern 1: Trend Leaders exhibit steady positive drift & clean 20-bar breakouts with volume surges
                if sym in trend_leaders:
                    idiosyncratic += 0.00065
                    if day_offset >= 0 and bar_in_day in (2, 3, 7, 8):
                        idiosyncratic += 0.0038
                        vol_mult = 1.85

                # Pattern 2: Mean Reversion Dippers have an uptrend during warmup, then sharp oversold dips on replay days followed by bounce
                if sym in mean_rev_dippers:
                    if day_offset < 0:
                        idiosyncratic += 0.0005
                    else:
                        # Create distinct oversold dip windows staggered by ticker seed
                        dip_day = (_ticker_seed(sym, 7) % max(1, num_days - 1))
                        if day_offset == dip_day and 1 <= bar_in_day <= 6:
                            idiosyncratic -= 0.0068
                            mkt_ret = min(0.0, mkt_ret)
                            vol_mult = 1.55
                        elif day_offset == dip_day and bar_in_day >= 7:
                            idiosyncratic += 0.0052
                            vol_mult = 1.40
                        elif day_offset == dip_day + 1 and bar_in_day <= 6:
                            idiosyncratic += 0.0045

                # Pattern 3: Intraday ORB stocks form a tight first 30-min bar (bar_in_day == 0) then break out on bars 1..4
                if sym in orb_breakout_tickers and day_offset >= 0:
                    if bar_in_day == 0:
                        idiosyncratic = rng.gauss(0.0002, 0.0005)
                        mkt_ret *= 0.4
                    elif bar_in_day in (1, 2, 3):
                        idiosyncratic += 0.0046
                        vol_mult = 1.95

                ret = beta * mkt_ret + idiosyncratic
                open_price = round(price, 4)
                close_price = round(max(2.0, open_price * (1.0 + ret)), 4)

                # Construct realistic high/low wicks
                intra_range_pct = max(0.0018, abs(ret) * 0.65 + 0.0015)
                if bar_in_day == 0 and sym in orb_breakout_tickers:
                    intra_range_pct = 0.0022  # Tight opening range on first bar
                wick_up = open_price * intra_range_pct * (0.45 + 0.3 * rng.random())
                wick_dn = open_price * intra_range_pct * (0.45 + 0.3 * rng.random())
                high_price = round(max(open_price, close_price) + wick_up, 4)
                low_price = round(max(1.0, min(open_price, close_price) - wick_dn), 4)
                vwap_price = round((open_price + high_price + low_price + close_price) / 4.0, 4)
                volume = round(base_vol * vol_mult * (1.25 if bar_in_day in (0, 12) else 1.0))

                bars_by_ticker[sym].append(
                    Bar(
                        ticker=sym,
                        timestamp=ts,
                        open=open_price,
                        high=high_price,
                        low=low_price,
                        close=close_price,
                        volume=float(volume),
                        vwap=vwap_price,
                    )
                )

                half_spread = close_price * (spread_bps / 20_000.0)
                quotes_by_ticker[sym].append(
                    Quote(
                        ticker=sym,
                        timestamp=ts,
                        bid_price=round(close_price - half_spread, 4),
                        ask_price=round(close_price + half_spread, 4),
                        mid_price=close_price,
                        spread_bps=spread_bps,
                    )
                )
                price = close_price

        # Build realistic timestamped news headlines across the replay days
        news_Catalog: List[Tuple[int, int, List[str], str, str, str]] = [
            (
                0, 1, ["NVDA", "AVGO", "SMH", "XLK"],
                "Hyperscaler AI CapEx Survey Points to Accelerated Next-Gen GPU & Custom Silicon Orders",
                "Channel checks across major cloud providers show Q4 data-center accelerator demand running 18% ahead of consensus.",
                "MarketWire Pro",
            ),
            (
                0, 4, ["SPY", "QQQ", "TLT"],
                "US Productivity Growth Tops Estimates While Unit Labor Costs Moderate",
                "Nonfarm productivity rose at a 2.8% annualized pace, supporting soft-landing equity multiples as Treasury yields stabilize.",
                "MacroDesk",
            ),
            (
                1, 1, ["META", "GOOGL", "AMZN", "XLC"],
                "Digital Ad Pricing & AI Recommendation Conversion Rates Expand in Early Holiday Quarter",
                "Checks indicate strong e-commerce ad ROAS and rising enterprise cloud backlog across mega-cap platforms.",
                "TechPulse",
            ),
            (
                1, 5, ["CAT", "GE", "XLI", "UNP"],
                "Grid Modernization & Aerospace Aftermarket Orders Lift Industrial Backlog to Record High",
                "Large-cap industrial manufacturers report sustained book-to-bill ratios above 1.15x on power infrastructure demand.",
                "Industrial Alpha",
            ),
            (
                2, 1, ["SPY", "TLT", "GLD", "XLV"],
                "Mid-Week Profit Taking Hits High-Beta Tech as Traders Digest Treasury Auction Supply",
                "Short-term dip buyers watch oversold RSI levels on quality software and consumer compounders ahead of afternoon bond auction.",
                "MacroDesk",
            ),
            (
                2, 6, ["MSFT", "CRM", "ADBE", "ORCL"],
                "Enterprise Software CFO Survey Confirms Resilient Renewal Rates After Morning Pullback",
                "Institutional desks note oversold intraday readings in mega-cap software leaders despite raised full-year cash flow guidance.",
                "StreetSignals",
            ),
            (
                3, 1, ["PLTR", "PANW", "AMD", "NVDA"],
                "Defense & Commercial AI Workflow Deployments Accelerate on New Federal Contract Awards",
                "Multi-year enterprise AI and cybersecurity contract awards drive high-volume morning breakouts across software and chip leaders.",
                "TechPulse",
            ),
            (
                3, 5, ["JPM", "GS", "AXP", "XLF"],
                "Capital Markets Advisory & Card Spend Trends Beat Expectations Heading Into Year-End",
                "Large-cap financials outperform as M&A pipeline recovery and prime consumer credit metrics remain benign.",
                "FinancialTimes Feed",
            ),
            (
                4, 1, ["LLY", "ISRG", "ABBV", "XLV"],
                "Healthcare Innovation Leaders Gain on Strong Procedure Volume & Metabolic Pipeline Data",
                "Robotic surgery utilization and biopharma prescription trends attract institutional inflows.",
                "BioMed Wire",
            ),
            (
                4, 4, ["AAPL", "COST", "WMT", "AMZN", "XLY"],
                "Consumer Discretionary & Club Retail Traffic Surges Into Peak Holiday Shopping Window",
                "High-frequency transaction data shows resilient household spending and strong omnichannel fulfillment margins.",
                "RetailWatch",
            ),
        ]

        headlines: List[NewsHeadline] = []
        for day_idx, bar_idx, syms, title, summary, source in news_Catalog:
            if day_idx < len(replay_days):
                target_day = replay_days[day_idx]
                day_ts_list = get_session_30min_timestamps_utc(target_day)
                chosen_ts = day_ts_list[min(bar_idx, len(day_ts_list) - 1)]
                headlines.append(
                    NewsHeadline(
                        headline=title,
                        summary=summary,
                        source=source,
                        created_at=chosen_ts,
                        symbols=syms,
                        url="",
                    )
                )

        headlines.sort(key=lambda n: n.created_at, reverse=True)
        return replay_days, bars_by_ticker, quotes_by_ticker, headlines

    def iter_replay_cycles(
        self,
        num_days: int = 5,
    ) -> Iterator[Tuple[MarketSnapshot, Dict[str, Bar], Dict[str, Quote], str]]:
        """Yield (snapshot, next_bars, next_quotes, fill_timestamp) across `num_days` US trading days.

        Guarantees strict zero look-ahead:
        - Every bar, quote, indicator, and news headline inside `snapshot` has timestamp <= snapshot.decision_timestamp.
        - `fill_timestamp > snapshot.decision_timestamp` and `next_bars`/`next_quotes` come from the next bar after decision_timestamp.
        """
        # Try live Alpaca historical bars if credentials exist and offline is False
        if not self.offline and self.client.has_credentials():
            live_cycles = list(self._iter_live_alpaca_replay_cycles(num_days=num_days))
            if live_cycles:
                yield from live_cycles
                return

        replay_days, bars_by_ticker, quotes_by_ticker, all_headlines = self._build_synthetic_dataset(
            num_days=num_days,
            warmup_days=5,
        )

        # Map each timestamp to its index in the synthetic series
        sample_ticker = self.universe[0]
        ts_list = [b.timestamp for b in bars_by_ticker[sample_ticker]]
        ts_to_idx = {ts: i for i, ts in enumerate(ts_list)}

        replay_days_set = {d.isoformat(): idx + 1 for idx, d in enumerate(replay_days)}

        # Precompute 85 completed synthetic daily bars prior to replay_days[0] so strategies needing >=70
        # completed daily bars (e.g. Claude RMP) work deterministically during offline dry runs.
        pre_daily_by_ticker: Dict[str, List[Bar]] = {}
        first_all_day = to_et_datetime(bars_by_ticker[sample_ticker][0].timestamp).date()
        pre_days = get_recent_us_trading_days(80, end_date=first_all_day - timedelta(days=1))
        for sym in self.universe:
            first_bar = bars_by_ticker[sym][0]
            anchor_close = float(first_bar.open)
            rng_d = random.Random(_ticker_seed(sym, self.seed + 777))
            # Build backward-anchored realistic daily path ending cleanly at anchor_close
            rets = []
            drift = 0.0014 if sym in {"NVDA", "AVGO", "META", "PLTR", "CAT", "LLY", "GE", "AXP", "ORCL", "NFLX", "JPM", "MSFT", "AAPL"} else 0.0006
            for _ in pre_days:
                rets.append(drift + rng_d.gauss(0.0, 0.011))
            closes_pre = [anchor_close]
            for r in reversed(rets[:-1]):
                closes_pre.append(max(5.0, closes_pre[-1] / math.exp(r)))
            closes_pre.reverse()
            dlist: List[Bar] = []
            for d_obj, c_val in zip(pre_days, closes_pre):
                o_val = round(c_val * (1.0 + rng_d.gauss(0.0, 0.004)), 4)
                h_val = round(max(o_val, c_val) * (1.0 + abs(rng_d.gauss(0.0, 0.006))), 4)
                l_val = round(min(o_val, c_val) * (1.0 - abs(rng_d.gauss(0.0, 0.006))), 4)
                dlist.append(
                    Bar(
                        ticker=sym,
                        timestamp=f"{d_obj.isoformat()}T21:00:00Z",
                        open=o_val,
                        high=h_val,
                        low=l_val,
                        close=round(c_val, 4),
                        volume=2_500_000.0,
                    )
                )
            pre_daily_by_ticker[sym] = dlist

        # Aggregate completed daily bars from the warmup + replay 30-min bars by ET date
        completed_30m_daily: Dict[str, Dict[str, Bar]] = {sym: {} for sym in self.universe}
        for sym in self.universe:
            by_d: Dict[str, List[Bar]] = {}
            for b in bars_by_ticker[sym]:
                bd = to_et_datetime(b.timestamp).strftime("%Y-%m-%d")
                by_d.setdefault(bd, []).append(b)
            for bd, bl in by_d.items():
                completed_30m_daily[sym][bd] = Bar(
                    ticker=sym,
                    timestamp=f"{bd}T21:00:00Z",
                    open=bl[0].open,
                    high=max(x.high for x in bl),
                    low=min(x.low for x in bl),
                    close=bl[-1].close,
                    volume=sum(x.volume for x in bl),
                )

        for d in replay_days:
            d_str = d.isoformat()
            day_num = replay_days_set[d_str]
            session_open_et, session_close_et = get_session_hours_et(d)
            day_timestamps = get_session_30min_timestamps_utc(d)

            # Completed daily bars strictly BEFORE d_str (zero look-ahead)
            daily_bars_before_day: Dict[str, List[Bar]] = {}
            for sym in self.universe:
                extra = [
                    b for bd, b in sorted(completed_30m_daily[sym].items())
                    if bd < d_str
                ]
                daily_bars_before_day[sym] = pre_daily_by_ticker[sym] + extra

            for ts in day_timestamps:
                idx = ts_to_idx[ts]
                next_idx = idx + 1
                fill_timestamp = ts_list[next_idx]

                # Slice bars strictly up to idx (timestamp <= ts)
                sliced_bars: Dict[str, List[Bar]] = {}
                sliced_quotes: Dict[str, Quote] = {}
                next_bars: Dict[str, Bar] = {}
                next_quotes: Dict[str, Quote] = {}

                for sym in self.universe:
                    sym_bars = bars_by_ticker[sym][: idx + 1]
                    sliced_bars[sym] = sym_bars
                    sliced_quotes[sym] = quotes_by_ticker[sym][idx]
                    next_bars[sym] = bars_by_ticker[sym][next_idx]
                    next_quotes[sym] = quotes_by_ticker[sym][next_idx]

                spy_bars = sliced_bars.get("SPY", [])
                indicators: Dict[str, TickerIndicators] = {}
                for sym in self.universe:
                    indicators[sym] = compute_indicators_from_bars(
                        ticker=sym,
                        bars=sliced_bars[sym],
                        spy_bars=spy_bars,
                        trading_date_et=d_str,
                    )

                visible_news = [n for n in all_headlines if n.created_at <= ts]
                regime_summary = compute_regime_summary(indicators)
                # Final bar of regular session (15:30 ET start -> 30 minutes before 16:00 ET close)
                is_near_close = (ts == day_timestamps[-1]) or compute_is_near_market_close(
                    ts, session_close_et=session_close_et
                )

                snapshot = MarketSnapshot(
                    decision_timestamp=ts,
                    trading_date_et=d_str,
                    trading_day_number=day_num,
                    is_near_market_close=is_near_close,
                    session_open_et=session_open_et,
                    session_close_et=session_close_et,
                    bars_by_ticker=sliced_bars,
                    quotes_by_ticker=sliced_quotes,
                    indicators=indicators,
                    news=visible_news,
                    regime_summary=regime_summary,
                    daily_bars_by_ticker=daily_bars_before_day,
                )

                yield snapshot, next_bars, next_quotes, fill_timestamp

    def _iter_live_alpaca_replay_cycles(
        self,
        num_days: int = 5,
    ) -> Iterator[Tuple[MarketSnapshot, Dict[str, Bar], Dict[str, Quote], str]]:
        """Fetch real 30-minute historical bars from Alpaca if credentials and data are available."""
        try:
            end_dt = datetime.now(timezone.utc) - timedelta(minutes=20)
            end_iso = format_iso_utc(end_dt)
            bars_by_ticker = self.client.fetch_bars_up_to(
                self.universe,
                decision_timestamp=end_iso,
                lookback_days=max(16, num_days * 3 + 7),
                timeframe="30Min",
            )
            spy_bars = bars_by_ticker.get("SPY", [])
            if len(spy_bars) < 30:
                return

            all_news = self.client.fetch_news_up_to(self.universe, decision_timestamp=end_iso, limit=50)

            # Group SPY bars by ET date
            dates_seen: List[str] = []
            for b in spy_bars:
                d_str = to_et_datetime(b.timestamp).strftime("%Y-%m-%d")
                if not dates_seen or dates_seen[-1] != d_str:
                    if d_str not in dates_seen:
                        dates_seen.append(d_str)

            replay_dates = dates_seen[-num_days:]
            replay_date_to_num = {d_str: i + 1 for i, d_str in enumerate(replay_dates)}

            # Iterate through SPY bar timestamps belonging to replay_dates
            for i, spy_bar in enumerate(spy_bars):
                ts = spy_bar.timestamp
                d_str = to_et_datetime(ts).strftime("%Y-%m-%d")
                if d_str not in replay_date_to_num:
                    continue

                day_num = replay_date_to_num[d_str]
                d_obj = date.fromisoformat(d_str)
                session_open_et, session_close_et = get_session_hours_et(d_obj)

                # Determine fill timestamp strictly > ts
                if i + 1 < len(spy_bars):
                    fill_timestamp = spy_bars[i + 1].timestamp
                else:
                    fill_timestamp = format_iso_utc(parse_iso_utc(ts) + timedelta(minutes=5))

                sliced_bars: Dict[str, List[Bar]] = {}
                sliced_quotes: Dict[str, Quote] = {}
                next_bars: Dict[str, Bar] = {}
                next_quotes: Dict[str, Quote] = {}

                for sym in self.universe:
                    sym_all = bars_by_ticker.get(sym, [])
                    up_to = [b for b in sym_all if b.timestamp <= ts]
                    after = [b for b in sym_all if b.timestamp > ts]
                    if not up_to:
                        continue
                    sliced_bars[sym] = up_to
                    last_b = up_to[-1]
                    half_sp = last_b.close * (DEFAULT_SPREAD_BPS / 20_000.0)
                    sliced_quotes[sym] = Quote(
                        ticker=sym,
                        timestamp=ts,
                        bid_price=round(last_b.close - half_sp, 4),
                        ask_price=round(last_b.close + half_sp, 4),
                        mid_price=round(last_b.close, 4),
                        spread_bps=DEFAULT_SPREAD_BPS,
                    )
                    nxt_b = after[0] if after else Bar(
                        ticker=sym,
                        timestamp=fill_timestamp,
                        open=last_b.close,
                        high=last_b.close,
                        low=last_b.close,
                        close=last_b.close,
                        volume=last_b.volume,
                        vwap=last_b.vwap,
                    )
                    next_bars[sym] = nxt_b
                    nxt_half = nxt_b.open * (DEFAULT_SPREAD_BPS / 20_000.0)
                    next_quotes[sym] = Quote(
                        ticker=sym,
                        timestamp=fill_timestamp,
                        bid_price=round(nxt_b.open - nxt_half, 4),
                        ask_price=round(nxt_b.open + nxt_half, 4),
                        mid_price=round(nxt_b.open, 4),
                        spread_bps=DEFAULT_SPREAD_BPS,
                    )

                spy_slice = sliced_bars.get("SPY", [])
                indicators: Dict[str, TickerIndicators] = {}
                for sym, blist in sliced_bars.items():
                    indicators[sym] = compute_indicators_from_bars(
                        ticker=sym,
                        bars=blist,
                        spy_bars=spy_slice,
                        trading_date_et=d_str,
                    )

                visible_news = [n for n in all_news if n.created_at <= ts]
                regime_summary = compute_regime_summary(indicators)

                # Check if this is the last bar of the day
                is_last_bar_of_day = (
                    i + 1 == len(spy_bars)
                    or to_et_datetime(spy_bars[i + 1].timestamp).strftime("%Y-%m-%d") != d_str
                )
                is_near_close = is_last_bar_of_day or compute_is_near_market_close(
                    ts, session_close_et=session_close_et
                )

                snapshot = MarketSnapshot(
                    decision_timestamp=ts,
                    trading_date_et=d_str,
                    trading_day_number=day_num,
                    is_near_market_close=is_near_close,
                    session_open_et=session_open_et,
                    session_close_et=session_close_et,
                    bars_by_ticker=sliced_bars,
                    quotes_by_ticker=sliced_quotes,
                    indicators=indicators,
                    news=visible_news,
                    regime_summary=regime_summary,
                )
                yield snapshot, next_bars, next_quotes, fill_timestamp
        except Exception:
            return

    def get_replay_cycles(
        self,
        num_days: int = 5,
    ) -> List[Tuple[MarketSnapshot, Dict[str, Bar], Dict[str, Quote], str]]:
        """Return all replay cycles as a list."""
        return list(self.iter_replay_cycles(num_days=num_days))
