"""Rule-based coded trading strategies and scientific benchmark/control agents."""

from __future__ import annotations

import hashlib
import random
from typing import List, Optional, Tuple

from ..config import MAX_OPEN_POSITIONS, SECTOR_ETFS, UNIVERSE, AgentSpec
from ..models import AgentLedgerState, MarketSnapshot, OrderSignal, TickerIndicators
from .base import BaseAgent


class TrendFollowingAgent(BaseAgent):
    """1. Trend Follower (MA / Breakout) - `coded_trend`."""

    DEFAULT_AGENT_ID = "coded_trend"

    def __init__(self, spec: Optional[AgentSpec] = None) -> None:
        super().__init__(spec)

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        # 1. Check existing positions for trend breakdown exits
        for ticker in list(state.positions.keys()):
            ind = snapshot.indicators.get(ticker)
            if ind is None:
                continue
            below_sma20 = ind.last_price < ind.sma_20
            ma_bearish_cross = ind.sma_10 < ind.sma_30
            if below_sma20 or ma_bearish_cross:
                reasons: List[str] = []
                if below_sma20:
                    reasons.append(f"last price (${ind.last_price:.2f}) fell below 20-SMA (${ind.sma_20:.2f})")
                if ma_bearish_cross:
                    reasons.append(f"fast 10-SMA (${ind.sma_10:.2f}) crossed below slow 30-SMA (${ind.sma_30:.2f})")
                return OrderSignal(
                    agent_id=self.agent_id,
                    action="SELL",
                    ticker=ticker,
                    stop_loss=None,
                    take_profit=None,
                    confidence=0.82,
                    reasoning=(
                        f"Exiting {ticker} on trend breakdown: {' and '.join(reasons)}."
                    ),
                    decision_timestamp=snapshot.decision_timestamp,
                )

        # 2. Scan for new breakout entries if under the 5-position cap
        if len(state.positions) >= MAX_OPEN_POSITIONS:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    f"Holding {len(state.positions)} active trend positions at the maximum position limit "
                    f"({MAX_OPEN_POSITIONS}); all held positions remain above their 20-SMA with 10-SMA >= 30-SMA."
                ),
                confidence=0.70,
            )

        candidates: List[Tuple[float, str, TickerIndicators]] = []
        for ticker, ind in snapshot.indicators.items():
            if ticker in state.positions or ind.last_price <= 0 or ind.high_20 <= 0:
                continue
            if (
                ind.sma_10 > ind.sma_30
                and ind.last_price >= ind.high_20 * 0.995
                and 52.0 <= ind.rsi_14 <= 75.0
                and ind.volume_ratio >= 1.05
            ):
                ma_spread_pct = ((ind.sma_10 - ind.sma_30) / max(ind.sma_30, 1e-6)) * 100.0
                breakout_pct = ((ind.last_price / ind.high_20) - 1.0) * 100.0
                score = (
                    ma_spread_pct * 2.0
                    + breakout_pct * 3.0
                    + (ind.volume_ratio - 1.0) * 10.0
                    + ind.relative_strength_vs_spy * 0.5
                    + ind.change_pct_5d * 0.3
                )
                candidates.append((score, ticker, ind))

        if not candidates:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    "No unheld stock currently satisfies all trend-breakout criteria "
                    "(10-SMA > 30-SMA, price within 0.5% of 20-bar high, RSI 52-75, volume ratio >= 1.05x)."
                ),
                confidence=0.60,
            )

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_score, best_ticker, ind = candidates[0]
        last_price = ind.last_price
        atr_14 = max(ind.atr_14, last_price * 0.005)

        stop_loss = round(last_price - max(2.0 * atr_14, last_price * 0.015), 2)
        if stop_loss <= 0 or stop_loss >= last_price:
            stop_loss = round(last_price * 0.98, 2)
        take_profit = round(last_price + max(3.5 * atr_14, last_price * 0.03), 2)

        confidence = min(0.92, max(0.65, round(0.68 + min(best_score, 12.0) * 0.02, 2)))
        return OrderSignal(
            agent_id=self.agent_id,
            action="BUY",
            ticker=best_ticker,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            reasoning=(
                f"Buying {best_ticker} on momentum breakout: price ${last_price:.2f} is at/near 20-bar high "
                f"(${ind.high_20:.2f}) with 10-SMA (${ind.sma_10:.2f}) > 30-SMA (${ind.sma_30:.2f}), "
                f"RSI(14) at {ind.rsi_14:.1f}, and volume ratio {ind.volume_ratio:.2f}x. "
                f"Stop set at ${stop_loss:.2f} and target at ${take_profit:.2f}."
            ),
            decision_timestamp=snapshot.decision_timestamp,
        )


class MeanReversionAgent(BaseAgent):
    """2. Mean Reversion (RSI / Bollinger) - `coded_mean_rev`."""

    DEFAULT_AGENT_ID = "coded_mean_rev"

    def __init__(self, spec: Optional[AgentSpec] = None) -> None:
        super().__init__(spec)

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        # 1. Check existing positions to lock in mean-reversion bounce
        for ticker in list(state.positions.keys()):
            ind = snapshot.indicators.get(ticker)
            if ind is None:
                continue
            rsi_reverted = ind.rsi_14 >= 58.0
            bb_mid_reverted = ind.last_price >= ind.bb_mid_20
            if rsi_reverted or bb_mid_reverted:
                triggers: List[str] = []
                if rsi_reverted:
                    triggers.append(f"RSI(14) rebounded to {ind.rsi_14:.1f} (>= 58)")
                if bb_mid_reverted:
                    triggers.append(
                        f"price (${ind.last_price:.2f}) reached the 20-period Bollinger midline (${ind.bb_mid_20:.2f})"
                    )
                return OrderSignal(
                    agent_id=self.agent_id,
                    action="SELL",
                    ticker=ticker,
                    stop_loss=None,
                    take_profit=None,
                    confidence=0.84,
                    reasoning=(
                        f"Taking profit on {ticker} mean-reversion bounce: {' and '.join(triggers)}."
                    ),
                    decision_timestamp=snapshot.decision_timestamp,
                )

        # 2. Scan universe for oversold dips if under position cap
        if len(state.positions) >= MAX_OPEN_POSITIONS:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    f"Holding {len(state.positions)} positions at max capacity ({MAX_OPEN_POSITIONS}); "
                    "waiting for held positions to revert to their 20-period Bollinger midline or RSI >= 58."
                ),
                confidence=0.68,
            )

        primary_candidates: List[Tuple[float, str, TickerIndicators]] = []
        secondary_candidates: List[Tuple[float, str, TickerIndicators]] = []

        for ticker, ind in snapshot.indicators.items():
            if ticker in state.positions or ind.last_price <= 0 or ind.sma_30 <= 0:
                continue
            # Require intact long-term structure
            if ind.last_price <= ind.sma_30 * 0.94:
                continue

            if ind.rsi_14 <= 34.0:
                primary_candidates.append((ind.rsi_14, ticker, ind))
            elif ind.rsi_14 < 38.0 and ind.bb_lower_20 > 0 and ind.last_price <= ind.bb_lower_20 * 1.005:
                secondary_candidates.append((ind.rsi_14, ticker, ind))

        chosen_list = primary_candidates if primary_candidates else secondary_candidates
        if not chosen_list:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    "No universe stock currently exhibits an oversold dip (RSI <= 34 or RSI < 38 near lower "
                    "Bollinger Band) while preserving long-term structure (price > 94% of 30-SMA)."
                ),
                confidence=0.60,
            )

        # Pick lowest RSI (most oversold); break ties deterministically by ticker
        chosen_list.sort(key=lambda item: (item[0], item[1]))
        lowest_rsi, best_ticker, ind = chosen_list[0]
        last_price = ind.last_price
        atr_14 = max(ind.atr_14, last_price * 0.005)

        stop_loss = round(last_price - max(1.8 * atr_14, last_price * 0.018), 2)
        if stop_loss <= 0 or stop_loss >= last_price:
            stop_loss = round(last_price * 0.98, 2)
        take_profit = round(max(ind.bb_mid_20, last_price * 1.025), 2)

        confidence = min(0.90, max(0.65, round(0.65 + (38.0 - lowest_rsi) * 0.02, 2)))
        return OrderSignal(
            agent_id=self.agent_id,
            action="BUY",
            ticker=best_ticker,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            reasoning=(
                f"Buying oversold dip in {best_ticker}: RSI(14) is {ind.rsi_14:.1f} with price ${last_price:.2f} "
                f"near lower Bollinger Band (${ind.bb_lower_20:.2f}) while holding structural support above "
                f"94% of 30-SMA (${ind.sma_30:.2f}). Stop at ${stop_loss:.2f}, mean-reversion target at ${take_profit:.2f}."
            ),
            decision_timestamp=snapshot.decision_timestamp,
        )


class IntradayORBAgent(BaseAgent):
    """3. Intraday ORB (Flat by Close) - `coded_orb`."""

    DEFAULT_AGENT_ID = "coded_orb"

    def __init__(self, spec: Optional[AgentSpec] = None) -> None:
        super().__init__(spec)

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        # 1. Flat-by-close enforcement
        if snapshot.is_near_market_close:
            if state.positions:
                first_ticker = next(iter(state.positions.keys()))
                return OrderSignal(
                    agent_id=self.agent_id,
                    action="SELL",
                    ticker=first_ticker,
                    stop_loss=None,
                    take_profit=None,
                    confidence=0.95,
                    reasoning=(
                        f"Near market close ({snapshot.session_close_et} ET); liquidating {first_ticker} "
                        "so Intraday ORB finishes the session flat with zero overnight exposure."
                    ),
                    decision_timestamp=snapshot.decision_timestamp,
                )
            return self.hold_signal(
                snapshot,
                reasoning="Near market close; Intraday ORB stays flat overnight",
                confidence=0.95,
            )

        # 2. Check existing positions for breakdown below opening range midpoint
        for ticker in list(state.positions.keys()):
            ind = snapshot.indicators.get(ticker)
            if ind is None:
                continue
            if ind.opening_range_high is not None and ind.opening_range_low is not None:
                orb_mid = (ind.opening_range_high + ind.opening_range_low) / 2.0
                if ind.last_price < orb_mid:
                    return OrderSignal(
                        agent_id=self.agent_id,
                        action="SELL",
                        ticker=ticker,
                        stop_loss=None,
                        take_profit=None,
                        confidence=0.85,
                        reasoning=(
                            f"Exiting {ticker} intraday ORB position: price (${ind.last_price:.2f}) fell back "
                            f"below the opening range midpoint (${orb_mid:.2f}; ORH ${ind.opening_range_high:.2f} / "
                            f"ORL ${ind.opening_range_low:.2f})."
                        ),
                        decision_timestamp=snapshot.decision_timestamp,
                    )

        # 3. Scan for opening-range breakouts if under position cap
        if len(state.positions) >= MAX_OPEN_POSITIONS:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    f"Holding {len(state.positions)} intraday ORB positions at max limit ({MAX_OPEN_POSITIONS}); "
                    "all positions remain above their opening range midpoints."
                ),
                confidence=0.70,
            )

        candidates: List[Tuple[float, str, TickerIndicators]] = []
        for ticker, ind in snapshot.indicators.items():
            if ticker in state.positions or ind.last_price <= 0:
                continue
            orb_high = ind.opening_range_high
            orb_low = ind.opening_range_low
            if orb_high is None or orb_low is None or orb_high <= 0 or orb_low <= 0 or orb_high < orb_low:
                continue

            # Must break above ORB high, within 1.5% above breakout so we don't chase too far, with volume >= 1.1x
            if orb_high < ind.last_price <= orb_high * 1.015 and ind.volume_ratio >= 1.1:
                breakout_pct = ((ind.last_price - orb_high) / orb_high) * 100.0
                score = (ind.volume_ratio * 10.0) + ind.relative_strength_vs_spy - abs(breakout_pct - 0.35)
                candidates.append((score, ticker, ind))

        if not candidates:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    "No stock currently shows a fresh opening-range breakout (price between 0% and 1.5% above "
                    "opening range high with volume ratio >= 1.10x)."
                ),
                confidence=0.60,
            )

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_score, best_ticker, ind = candidates[0]
        last_price = ind.last_price
        orb_high = float(ind.opening_range_high)
        orb_low = float(ind.opening_range_low)
        orb_mid = (orb_high + orb_low) / 2.0

        # Use opening range midpoint unless it is too close (< 0.4%) or too far (> 2.5%) from entry
        mid_dist_pct = (last_price - orb_mid) / last_price if last_price > 0 else 0.0
        if 0.004 <= mid_dist_pct <= 0.025:
            stop_loss = round(orb_mid, 2)
            stop_desc = f"opening range midpoint (${stop_loss:.2f})"
        else:
            stop_loss = round(last_price * (1.0 - 0.012), 2)
            stop_desc = f"1.2% below entry (${stop_loss:.2f})"

        if stop_loss <= 0 or stop_loss >= last_price:
            stop_loss = round(last_price * 0.988, 2)

        take_profit = round(2.0 * (last_price - stop_loss) + last_price, 2)
        confidence = min(0.90, max(0.66, round(0.64 + min(ind.volume_ratio, 2.5) * 0.1, 2)))

        return OrderSignal(
            agent_id=self.agent_id,
            action="BUY",
            ticker=best_ticker,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            reasoning=(
                f"Buying {best_ticker} on 30-min Opening Range Breakout: price ${last_price:.2f} cleared "
                f"ORH ${orb_high:.2f} (ORL ${orb_low:.2f}) on {ind.volume_ratio:.2f}x relative volume. "
                f"Stop placed at {stop_desc} with 2R intraday target at ${take_profit:.2f}."
            ),
            decision_timestamp=snapshot.decision_timestamp,
        )


class SectorRotationAgent(BaseAgent):
    """4. Sector ETF Rotation - `coded_sector`."""

    DEFAULT_AGENT_ID = "coded_sector"
    MAX_SECTOR_POSITIONS = 4

    def __init__(self, spec: Optional[AgentSpec] = None) -> None:
        super().__init__(spec)

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        # Rank all available SPDR sector ETFs by composite relative strength score
        ranked_sectors: List[Tuple[float, str, TickerIndicators]] = []
        for etf in SECTOR_ETFS:
            ind = snapshot.indicators.get(etf)
            if ind is None or ind.last_price <= 0:
                continue
            composite_score = ind.relative_strength_vs_spy * 0.6 + ind.change_pct_5d * 0.4
            ranked_sectors.append((composite_score, etf, ind))

        if not ranked_sectors:
            return self.hold_signal(
                snapshot,
                reasoning="Sector ETF indicator data is unavailable in current snapshot; holding steady.",
                confidence=0.50,
            )

        # Sort descending by composite_score; tie-break deterministically by ticker
        ranked_sectors.sort(key=lambda item: (item[0], item[1]), reverse=True)
        rank_by_ticker = {etf: idx + 1 for idx, (_, etf, _) in enumerate(ranked_sectors)}
        score_by_ticker = {etf: score for score, etf, _ in ranked_sectors}

        # 1. Check held sector ETFs for rotation exit (rank > 5 or last_price < sma_20)
        for ticker in list(state.positions.keys()):
            ind = snapshot.indicators.get(ticker)
            rank = rank_by_ticker.get(ticker, 99)
            below_sma20 = ind is not None and ind.last_price < ind.sma_20
            if rank > 5 or below_sma20:
                reasons: List[str] = []
                if rank > 5:
                    reasons.append(
                        f"relative-strength rank slipped to #{rank} of {len(ranked_sectors)} "
                        f"(composite score {score_by_ticker.get(ticker, 0.0):+.2f}%)"
                    )
                if below_sma20 and ind is not None:
                    reasons.append(f"price (${ind.last_price:.2f}) broke below 20-SMA (${ind.sma_20:.2f})")
                return OrderSignal(
                    agent_id=self.agent_id,
                    action="SELL",
                    ticker=ticker,
                    stop_loss=None,
                    take_profit=None,
                    confidence=0.83,
                    reasoning=f"Rotating out of sector ETF {ticker}: {' and '.join(reasons)}.",
                    decision_timestamp=snapshot.decision_timestamp,
                )

        # 2. Buy highest-ranked unheld sector ETF (up to 4 positions) with last_price > sma_20
        if len(state.positions) >= self.MAX_SECTOR_POSITIONS:
            held_summary = ", ".join(
                f"{t} (#{rank_by_ticker.get(t, '?')})" for t in state.positions.keys()
            )
            return self.hold_signal(
                snapshot,
                reasoning=(
                    f"Holding maximum {self.MAX_SECTOR_POSITIONS} leading sector ETFs ({held_summary}); "
                    "all held sectors remain in the top 5 rankings and above their 20-SMA."
                ),
                confidence=0.75,
            )

        for rank_idx, (score, etf, ind) in enumerate(ranked_sectors, start=1):
            if etf in state.positions:
                continue
            if rank_idx <= 5 and ind.last_price > ind.sma_20:
                last_price = ind.last_price
                atr_14 = max(ind.atr_14, last_price * 0.005)
                stop_loss = round(last_price - max(2.0 * atr_14, last_price * 0.015), 2)
                if stop_loss <= 0 or stop_loss >= last_price:
                    stop_loss = round(last_price * 0.985, 2)
                take_profit = round(last_price * 1.04, 2)
                confidence = min(0.90, max(0.68, round(0.72 + max(0.0, score) * 0.04, 2)))
                return OrderSignal(
                    agent_id=self.agent_id,
                    action="BUY",
                    ticker=etf,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=confidence,
                    reasoning=(
                        f"Rotating into top-ranked sector ETF {etf} (rank #{rank_idx} of {len(ranked_sectors)}, "
                        f"composite RS score {score:+.2f}%, RS vs SPY {ind.relative_strength_vs_spy:+.2f}%, "
                        f"5d change {ind.change_pct_5d:+.2f}%) trading at ${last_price:.2f} above 20-SMA "
                        f"(${ind.sma_20:.2f}). Stop at ${stop_loss:.2f}, target at ${take_profit:.2f}."
                    ),
                    decision_timestamp=snapshot.decision_timestamp,
                )

        top_three = ", ".join(f"{etf} ({score:+.2f}%)" for score, etf, _ in ranked_sectors[:3])
        return self.hold_signal(
            snapshot,
            reasoning=(
                f"No unheld top-5 sector ETF currently trades above its 20-SMA (top rankings: {top_three})."
            ),
            confidence=0.62,
        )


class BuyAndHoldSPYAgent(BaseAgent):
    """9. Buy-and-Hold SPY (Benchmark) - `ctrl_spy`."""

    DEFAULT_AGENT_ID = "ctrl_spy"

    def __init__(self, spec: Optional[AgentSpec] = None) -> None:
        super().__init__(spec)

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        if "SPY" not in state.positions and state.cash > 100.0:
            spy_ind = snapshot.indicators.get("SPY")
            last_price = spy_ind.last_price if spy_ind is not None else 0.0
            if last_price <= 0 and "SPY" in snapshot.quotes_by_ticker:
                last_price = snapshot.quotes_by_ticker["SPY"].mid_price
            if last_price <= 0:
                return self.hold_signal(
                    snapshot,
                    reasoning="Waiting for valid SPY quote/indicator price to establish passive benchmark position.",
                    confidence=0.50,
                )

            stop_loss = round(last_price * 0.50, 2)
            take_profit = round(last_price * 2.00, 2)
            requested_shares = (state.cash * 0.995) / last_price
            return OrderSignal(
                agent_id=self.agent_id,
                action="BUY",
                ticker="SPY",
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=1.0,
                reasoning=(
                    f"Allocating passive S&P 500 benchmark capital into SPY at ${last_price:.2f} "
                    f"(requesting {requested_shares:.4f} shares with catastrophic stop floor at ${stop_loss:.2f}) "
                    "to hold throughout the 10-day competition."
                ),
                requested_shares=requested_shares,
                decision_timestamp=snapshot.decision_timestamp,
            )

        return self.hold_signal(
            snapshot,
            reasoning="Holding passive SPY benchmark position",
            confidence=1.0,
        )


class RandomTraderAgent(BaseAgent):
    """10. Random Trader (Luck Control) - `ctrl_random`."""

    DEFAULT_AGENT_ID = "ctrl_random"

    def __init__(self, spec: Optional[AgentSpec] = None) -> None:
        super().__init__(spec)

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        seed_hex = hashlib.sha256(
            f"{snapshot.decision_timestamp}:{state.agent_id}".encode()
        ).hexdigest()
        rng = random.Random(int(seed_hex, 16))

        roll = rng.random()

        # 1. With 25% probability (if holding positions), pick a random held position to SELL
        if state.positions and roll < 0.25:
            held_tickers = sorted(state.positions.keys())
            chosen_ticker = rng.choice(held_tickers)
            return OrderSignal(
                agent_id=self.agent_id,
                action="SELL",
                ticker=chosen_ticker,
                stop_loss=None,
                take_profit=None,
                confidence=0.50,
                reasoning=(
                    f"Null-hypothesis random control: deterministic cycle seed ({seed_hex[:8]}) rolled "
                    f"{roll:.3f} (< 0.25) to randomly exit held position {chosen_ticker}."
                ),
                decision_timestamp=snapshot.decision_timestamp,
            )

        # 2. With 45% probability, pick a random ticker from UNIVERSE to BUY (shared Risk Manager enforces max 5 open positions)
        buy_threshold = 0.70 if state.positions else 0.45
        if roll < buy_threshold:
            available_tickers = [
                t
                for t in UNIVERSE
                if t not in state.positions
                and t in snapshot.indicators
                and snapshot.indicators[t].last_price > 0
            ]
            if available_tickers:
                chosen_ticker = rng.choice(available_tickers)
                ind = snapshot.indicators[chosen_ticker]
                last_price = ind.last_price
                stop_dist_pct = rng.uniform(0.015, 0.030)
                stop_loss = round(last_price * (1.0 - stop_dist_pct), 2)
                if stop_loss <= 0 or stop_loss >= last_price:
                    stop_loss = round(last_price * 0.98, 2)
                actual_stop_dist = last_price - stop_loss
                take_profit = round(last_price + 2.0 * actual_stop_dist, 2)

                return OrderSignal(
                    agent_id=self.agent_id,
                    action="BUY",
                    ticker=chosen_ticker,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=0.50,
                    reasoning=(
                        f"Null-hypothesis random control: deterministic cycle seed ({seed_hex[:8]}) rolled "
                        f"{roll:.3f} to randomly buy {chosen_ticker} at ${last_price:.2f} with a "
                        f"{stop_dist_pct * 100:.2f}% stop (${stop_loss:.2f}) and 2R target (${take_profit:.2f})."
                    ),
                    decision_timestamp=snapshot.decision_timestamp,
                )

        # 3. Otherwise HOLD
        return self.hold_signal(
            snapshot,
            reasoning=(
                f"Null-hypothesis random control: deterministic cycle seed ({seed_hex[:8]}) rolled "
                f"{roll:.3f}; taking no action this cycle."
            ),
            confidence=0.50,
        )
