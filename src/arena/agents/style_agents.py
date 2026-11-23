"""Learned Filing-Style Trading Agents (#11 Berkshire Style & #12 Congress Style).

Hard rule enforced:
- NEVER buy a stock because it appeared in a 13F or House PTR filing.
- Use the learned rules (from historical filings public before start date) to evaluate
  stocks in our liquid UNIVERSE using today's normal price data + cached fundamentals,
  routing every order through the exact same shared Risk Manager as all other agents.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arena.config import (
    AGENT_SPECS_BY_ID,
    LIQUID_LARGE_CAP_STOCKS,
    MAX_OPEN_POSITIONS,
    STATE_DIR,
    AgentSpec,
)
from arena.models import AgentLedgerState, MarketSnapshot, OrderSignal, TickerIndicators
from arena.research.berkshire_learner import BERKSHIRE_PATTERN_PATH, evaluate_berkshire_rules
from arena.research.congress_learner import CONGRESS_PATTERN_PATH, evaluate_congress_rules
from arena.research.market_history import UNIVERSE_FUNDAMENTALS_PATH, MarketHistoryCache
from .base import BaseAgent


def _load_json_safe(path: Path) -> Dict[str, Any]:
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _days_held(opened_at: str, decision_timestamp: str) -> int:
    try:
        d1 = date.fromisoformat(opened_at[:10])
        d2 = date.fromisoformat(decision_timestamp[:10])
        return max(0, (d2 - d1).days)
    except Exception:
        return 0


class _BaseStyleAgent(BaseAgent):
    """Shared base for pattern-learned style agents reading cached rules and universe features."""

    PATTERN_PATH: Path

    def __init__(
        self,
        spec: Optional[AgentSpec] = None,
        pattern_path: Optional[Path] = None,
        universe_fundamentals_path: Optional[Path] = None,
    ) -> None:
        super().__init__(spec)
        self.pattern_path = Path(pattern_path) if pattern_path else self.PATTERN_PATH
        self.universe_fundamentals_path = (
            Path(universe_fundamentals_path) if universe_fundamentals_path else UNIVERSE_FUNDAMENTALS_PATH
        )
        self._pattern_cache: Optional[Dict[str, Any]] = None
        self._fund_cache: Optional[Dict[str, Any]] = None

    def load_pattern(self) -> Dict[str, Any]:
        if self._pattern_cache is None:
            self._pattern_cache = _load_json_safe(self.pattern_path)
        return self._pattern_cache

    def load_universe_fundamentals(self) -> Dict[str, Dict[str, Any]]:
        if self._fund_cache is None:
            self._fund_cache = _load_json_safe(self.universe_fundamentals_path)
        return self._fund_cache

    def _build_live_stock_features(
        self,
        ticker: str,
        ind: TickerIndicators,
        fund: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Combine today's live/normal market snapshot price & indicators with cached company metadata.

        Dynamically updates distance from 52-week high, 3-month trend, and 30-day move using
        today's live price (`ind.last_price`) relative to the cached reference close so intraday
        price changes are always reflected.
        """
        last_px = float(ind.last_price)
        ref_close = float(fund.get("close") or last_px or 1.0)
        px_ratio = (last_px / ref_close) if ref_close > 0 else 1.0

        # Adjust cached 52w high distance using live price
        high_52w = float(fund.get("high_52w") or max(last_px, ind.high_20))
        effective_high_52w = max(high_52w, last_px)
        dist_52w = ((last_px - effective_high_52w) / effective_high_52w) * 100.0 if effective_high_52w > 0 else 0.0

        # Adjust cached 3m trend & 30d move by live price delta since cache date
        cached_t3m = fund.get("trend_3m_pct")
        if cached_t3m is not None:
            trend_3m = ((1.0 + float(cached_t3m) / 100.0) * px_ratio - 1.0) * 100.0
        else:
            trend_3m = float(ind.change_pct_5d)

        cached_m30 = fund.get("move_30d_pct")
        if cached_m30 is not None:
            move_30d = ((1.0 + float(cached_m30) / 100.0) * px_ratio - 1.0) * 100.0
        else:
            move_30d = float(ind.change_pct_5d)

        # Adjust cached P/E & market cap proportionally with live price
        pe = fund.get("pe_ratio")
        adj_pe = round(float(pe) * px_ratio, 2) if pe is not None and float(pe) > 0 else None
        mcap = fund.get("market_cap")
        adj_mcap = round(float(mcap) * px_ratio, 2) if mcap is not None and float(mcap) > 0 else None

        div_y = fund.get("dividend_yield_pct")
        adj_div = round(float(div_y) / px_ratio, 2) if (div_y is not None and px_ratio > 0) else 0.0

        return {
            "ticker": ticker,
            "sector": fund.get("sector", "Unknown"),
            "market_cap": adj_mcap,
            "pe_ratio": adj_pe,
            "dist_from_52w_high_pct": round(dist_52w, 2),
            "trend_3m_pct": round(trend_3m, 2),
            "move_30d_pct": round(move_30d, 2),
            "dividend_yield_pct": adj_div,
            "volume_ratio": float(ind.volume_ratio),
            "relative_strength_vs_spy": float(ind.relative_strength_vs_spy),
        }


class BerkshireStyleAgent(_BaseStyleAgent):
    """11. Berkshire Style — applies explicit rules learned from 5 years of Berkshire 13F filings."""

    DEFAULT_AGENT_ID = "style_berkshire"
    PATTERN_PATH = BERKSHIRE_PATTERN_PATH

    DEFAULT_RULES: Dict[str, Any] = {
        "never_copy_filings": True,
        "timing_learned_from_13f": False,
        "preferred_sectors": ["Financials", "Technology", "Consumer Staples", "Energy", "Communication Services"],
        "min_market_cap_usd": 15_000_000_000.0,
        "min_pe_ratio": 4.0,
        "max_pe_ratio": 28.0,
        "min_dist_from_52w_high_pct": -35.0,
        "max_dist_from_52w_high_pct": 0.0,
        "min_trend_3m_pct": -20.0,
        "max_trend_3m_pct": 30.0,
        "require_dividend": True,
        "min_dividend_yield_pct": 0.5,
        "target_max_positions": 5,
        "low_turnover_min_hold_days": 5,
        "stop_loss_atr_mult": 2.5,
        "take_profit_pct": 8.0,
    }

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        pattern = self.load_pattern()
        rules = pattern.get("learned_rules") or self.DEFAULT_RULES
        fundamentals = self.load_universe_fundamentals()

        min_hold_days = int(rules.get("low_turnover_min_hold_days", 5))
        max_pos = min(MAX_OPEN_POSITIONS, int(rules.get("target_max_positions", 5)))

        # 1. Patient / low-turnover exit check: only exit if held >= min_hold_days AND stock severely violates style
        for ticker in sorted(state.positions.keys()):
            ind = snapshot.indicators.get(ticker)
            if not ind or ind.last_price <= 0:
                continue
            pos = state.positions[ticker]
            held = _days_held(pos.opened_at, snapshot.decision_timestamp)
            if held < min_hold_days:
                continue
            fund = fundamentals.get(ticker, {})
            feat = self._build_live_stock_features(ticker, ind, fund)
            ok, score, _, failed = evaluate_berkshire_rules(feat, rules)
            if not ok and score < 45.0:
                return OrderSignal(
                    agent_id=self.agent_id,
                    action="SELL",
                    ticker=ticker,
                    stop_loss=None,
                    take_profit=None,
                    confidence=0.72,
                    reasoning=(
                        f"Berkshire Style exit on {ticker} after {held} days: style score fell to {score:.1f}/100 "
                        f"({'; '.join(failed[:2])})."
                    ),
                    decision_timestamp=snapshot.decision_timestamp,
                )

        if len(state.positions) >= max_pos:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    f"Berkshire Style holding {len(state.positions)}/{max_pos} concentrated value positions "
                    "patiently (low-turnover rule learned from multi-quarter 13F holding periods)."
                ),
                confidence=0.78,
            )

        # 2. Screen liquid stocks in UNIVERSE against explicit learned Berkshire rules (NEVER copying filings)
        candidates: List[Tuple[float, str, TickerIndicators, List[str]]] = []
        for ticker in LIQUID_LARGE_CAP_STOCKS:
            if ticker in state.positions:
                continue
            ind = snapshot.indicators.get(ticker)
            if not ind or ind.last_price <= 0:
                continue
            fund = fundamentals.get(ticker)
            if not fund:
                continue
            feat = self._build_live_stock_features(ticker, ind, fund)
            ok, score, passed, _ = evaluate_berkshire_rules(feat, rules)
            if ok:
                candidates.append((score, ticker, ind, passed))

        if not candidates:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    "No unheld large-cap stock in our universe currently satisfies all explicit Berkshire Style "
                    "rules (sector, size, P/E valuation ceiling, 52w-high window, 3m trend, dividend). Staying patient in cash."
                ),
                confidence=0.70,
            )

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_score, best_ticker, ind, passed = candidates[0]
        last_price = ind.last_price
        atr_mult = float(rules.get("stop_loss_atr_mult", 2.5))
        tp_pct = float(rules.get("take_profit_pct", 8.0)) / 100.0

        stop_dist = max(atr_mult * ind.atr_14, last_price * 0.025)
        stop_loss = round(max(0.01, last_price - stop_dist), 2)
        take_profit = round(last_price * (1.0 + tp_pct), 2)
        confidence = round(min(0.92, max(0.62, best_score / 100.0)), 2)

        return OrderSignal(
            agent_id=self.agent_id,
            action="BUY",
            ticker=best_ticker,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            reasoning=(
                f"Berkshire Style buy on {best_ticker} (pattern score {best_score:.1f}/100, selected strictly from "
                f"normal market data, NOT copied from filings): {'; '.join(passed[:4])}. "
                f"Risk-managed stop at ${stop_loss:.2f}, target ${take_profit:.2f}."
            ),
            decision_timestamp=snapshot.decision_timestamp,
        )


class CongressStyleAgent(_BaseStyleAgent):
    """12. Congress Style — applies explicit rules learned from 2 years of House PTR stock purchases."""

    DEFAULT_AGENT_ID = "style_congress"
    PATTERN_PATH = CONGRESS_PATTERN_PATH

    DEFAULT_RULES: Dict[str, Any] = {
        "never_copy_filings": True,
        "shares_only": True,
        "preferred_sectors": ["Technology", "Financials", "Health Care", "Industrials", "Consumer Discretionary"],
        "min_market_cap_usd": 15_000_000_000.0,
        "min_pre_trade_30d_move_pct": -6.0,
        "max_pre_trade_30d_move_pct": 12.0,
        "target_pre_trade_30d_move_pct": 2.0,
        "typical_hold_days": 60,
        "stop_loss_atr_mult": 2.0,
        "take_profit_pct": 6.5,
    }

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        pattern = self.load_pattern()
        rules = pattern.get("learned_rules") or self.DEFAULT_RULES
        fundamentals = self.load_universe_fundamentals()

        typical_hold_days = int(rules.get("typical_hold_days", 60))

        # 1. Exit check: holding-horizon maturity OR severe momentum breakdown below SMA30 & 30d move band
        for ticker in sorted(state.positions.keys()):
            ind = snapshot.indicators.get(ticker)
            if not ind or ind.last_price <= 0:
                continue
            pos = state.positions[ticker]
            held = _days_held(pos.opened_at, snapshot.decision_timestamp)
            fund = fundamentals.get(ticker, {})
            feat = self._build_live_stock_features(ticker, ind, fund)
            if held >= typical_hold_days:
                return OrderSignal(
                    agent_id=self.agent_id,
                    action="SELL",
                    ticker=ticker,
                    stop_loss=None,
                    take_profit=None,
                    confidence=0.75,
                    reasoning=(
                        f"Congress Style time-horizon exit on {ticker}: held {held} days, reaching learned "
                        f"typical purchase-to-sale holding cadence ({typical_hold_days} days)."
                    ),
                    decision_timestamp=snapshot.decision_timestamp,
                )
            if ind.last_price < ind.sma_30 and ind.rsi_14 < 40.0 and feat["move_30d_pct"] < float(rules.get("min_pre_trade_30d_move_pct", -8.0)):
                return OrderSignal(
                    agent_id=self.agent_id,
                    action="SELL",
                    ticker=ticker,
                    stop_loss=None,
                    take_profit=None,
                    confidence=0.70,
                    reasoning=(
                        f"Congress Style exit on {ticker}: 30-day return ({feat['move_30d_pct']:+.1f}%) fell below "
                        f"learned pre-trade band with price ${ind.last_price:.2f} < SMA30 ${ind.sma_30:.2f} (RSI {ind.rsi_14:.1f})."
                    ),
                    decision_timestamp=snapshot.decision_timestamp,
                )

        if len(state.positions) >= MAX_OPEN_POSITIONS:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    f"Congress Style holding {len(state.positions)}/{MAX_OPEN_POSITIONS} positions within "
                    f"learned holding horizon ({typical_hold_days} days)."
                ),
                confidence=0.72,
            )

        # 2. Screen liquid stocks in UNIVERSE against explicit learned Congress rules (NEVER copying filings)
        candidates: List[Tuple[float, str, TickerIndicators, List[str]]] = []
        for ticker in LIQUID_LARGE_CAP_STOCKS:
            if ticker in state.positions:
                continue
            ind = snapshot.indicators.get(ticker)
            if not ind or ind.last_price <= 0:
                continue
            fund = fundamentals.get(ticker)
            if not fund:
                continue
            feat = self._build_live_stock_features(ticker, ind, fund)
            ok, score, passed, _ = evaluate_congress_rules(feat, rules)
            if ok and ind.last_price >= ind.sma_20 * 0.985:
                candidates.append((score, ticker, ind, passed))

        if not candidates:
            return self.hold_signal(
                snapshot,
                reasoning=(
                    "No unheld large-cap stock in our universe currently matches all explicit Congress Style "
                    "rules (learned sectors, company size, and 30-day price move window). Holding cash."
                ),
                confidence=0.65,
            )

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best_score, best_ticker, ind, passed = candidates[0]
        last_price = ind.last_price
        atr_mult = float(rules.get("stop_loss_atr_mult", 2.0))
        tp_pct = float(rules.get("take_profit_pct", 6.5)) / 100.0

        stop_dist = max(atr_mult * ind.atr_14, last_price * 0.020)
        stop_loss = round(max(0.01, last_price - stop_dist), 2)
        take_profit = round(last_price * (1.0 + tp_pct), 2)
        confidence = round(min(0.90, max(0.60, best_score / 100.0)), 2)

        return OrderSignal(
            agent_id=self.agent_id,
            action="BUY",
            ticker=best_ticker,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            reasoning=(
                f"Congress Style buy on {best_ticker} (pattern score {best_score:.1f}/100, selected strictly from "
                f"normal market data, NOT copied from PTR trades): {'; '.join(passed[:3])}. "
                f"Stop at ${stop_loss:.2f}, target ${take_profit:.2f} (typical hold ~{typical_hold_days}d)."
            ),
            decision_timestamp=snapshot.decision_timestamp,
        )
