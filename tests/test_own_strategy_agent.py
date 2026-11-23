"""Unit tests for Agent 13 (Own Strategy): blind prompt neutrality, stricter-only limits,
US stock >$5 & >1M volume filter, intraday revision block, and full version archiving."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from arena.agents.own_strategy_agent import (
    OwnStrategyAgent,
    build_blind_design_prompt,
    clamp_stricter_limits,
    is_eligible_agent13_stock,
)
from arena.engine.risk_manager import RiskManager
from arena.models import (
    AgentLedgerState,
    Bar,
    MarketSnapshot,
    OrderSignal,
    Quote,
    TickerIndicators,
    TradeRecord,
)


def _make_snapshot_with_bars(price_aapl: float = 225.0, vol_per_30m: float = 200_000.0) -> MarketSnapshot:
    bars_aapl = [
        Bar("AAPL", f"2026-11-20T{14 + (i // 2):02d}:{(i % 2) * 30:02d}:00Z", price_aapl, price_aapl + 1, price_aapl - 1, price_aapl, vol_per_30m)
        for i in range(13)
    ]
    bars_low_vol = [
        Bar("MSFT", f"2026-11-20T{14 + (i // 2):02d}:{(i % 2) * 30:02d}:00Z", 420.0, 421.0, 419.0, 420.0, 20_000.0)
        for i in range(13)  # 260,000 daily shares (< 1,000,000)
    ]
    bars_penny = [
        Bar("INTC", f"2026-11-20T{14 + (i // 2):02d}:{(i % 2) * 30:02d}:00Z", 4.50, 4.60, 4.40, 4.50, 300_000.0)
        for i in range(13)
    ]
    ind_aapl = TickerIndicators(
        ticker="AAPL", last_price=price_aapl, prev_close=price_aapl - 1, change_pct_1d=0.45, change_pct_5d=1.5,
        sma_10=price_aapl - 1, sma_20=price_aapl - 2, sma_30=price_aapl - 3, rsi_14=58.0, atr_14=2.5,
        bb_upper_20=price_aapl + 5, bb_mid_20=price_aapl - 2, bb_lower_20=price_aapl - 9,
        high_20=price_aapl + 2, low_20=price_aapl - 8, volume_ratio=1.2, relative_strength_vs_spy=1.1,
    )
    ind_msft = TickerIndicators(
        ticker="MSFT", last_price=420.0, prev_close=419.0, change_pct_1d=0.2, change_pct_5d=0.5,
        sma_10=419.0, sma_20=418.0, sma_30=417.0, rsi_14=55.0, atr_14=4.0,
        bb_upper_20=430.0, bb_mid_20=418.0, bb_lower_20=406.0, high_20=425.0, low_20=410.0,
    )
    ind_intc = TickerIndicators(
        ticker="INTC", last_price=4.50, prev_close=4.40, change_pct_1d=2.2, change_pct_5d=1.0,
        sma_10=4.45, sma_20=4.40, sma_30=4.35, rsi_14=55.0, atr_14=0.15,
        bb_upper_20=4.80, bb_mid_20=4.40, bb_lower_20=4.00, high_20=4.70, low_20=4.20,
    )
    ind_spy = TickerIndicators(
        ticker="SPY", last_price=600.0, prev_close=599.0, change_pct_1d=0.17, change_pct_5d=0.8,
        sma_10=598.0, sma_20=596.0, sma_30=594.0, rsi_14=56.0, atr_14=3.0,
        bb_upper_20=610.0, bb_mid_20=596.0, bb_lower_20=582.0, high_20=605.0, low_20=590.0,
    )
    return MarketSnapshot(
        decision_timestamp="2026-11-23T15:00:00Z",
        trading_date_et="2026-11-23",
        trading_day_number=1,
        is_near_market_close=False,
        session_open_et="09:30",
        session_close_et="16:00",
        bars_by_ticker={"AAPL": bars_aapl, "MSFT": bars_low_vol, "INTC": bars_penny, "SPY": bars_aapl},
        quotes_by_ticker={
            "AAPL": Quote("AAPL", "2026-11-23T15:00:00Z", 224.95, 225.05, 225.0, 4.44),
            "MSFT": Quote("MSFT", "2026-11-23T15:00:00Z", 419.90, 420.10, 420.0, 4.76),
            "INTC": Quote("INTC", "2026-11-23T15:00:00Z", 4.49, 4.51, 4.50, 44.4),
            "SPY": Quote("SPY", "2026-11-23T15:00:00Z", 599.95, 600.05, 600.0, 1.67),
        },
        indicators={"AAPL": ind_aapl, "MSFT": ind_msft, "INTC": ind_intc, "SPY": ind_spy},
    )


class TestOwnStrategyAgent(unittest.TestCase):
    def test_blind_design_prompt_suggests_no_strategy_indicator_or_style(self) -> None:
        prompt = build_blind_design_prompt().lower()
        forbidden_hints = [
            "momentum",
            "mean reversion",
            "breakout",
            "trend following",
            "rsi",
            "macd",
            "bollinger",
            "moving average",
            "sma",
            "ema",
            "atr",
            "value investing",
            "scalp",
            "swing trade",
        ]
        for hint in forbidden_hints:
            self.assertNotIn(hint, prompt, f"Blind prompt must NOT suggest '{hint}'")
        # Must include core hard facts
        self.assertIn("$10,000.00", prompt)
        self.assertIn("10 us trading days", prompt)
        self.assertIn("1,000,000", prompt)
        self.assertIn("$5.00", prompt)

    def test_stricter_limits_can_tighten_never_loosen(self) -> None:
        # Attempt to loosen every limit beyond hard rules -> clamped back to hard rules
        looser = clamp_stricter_limits(
            {
                "max_risk_per_trade_pct": 5.0,
                "max_position_equity_pct": 50.0,
                "max_open_positions": 12,
                "daily_loss_limit_pct": -8.0,
            }
        )
        self.assertEqual(looser["max_risk_per_trade_pct"], 1.0)
        self.assertEqual(looser["max_position_equity_pct"], 20.0)
        self.assertEqual(looser["max_open_positions"], 5)
        self.assertEqual(looser["daily_loss_limit_pct"], -3.0)

        # Tighten every limit -> honored by RiskManager
        tighter = clamp_stricter_limits(
            {
                "max_risk_per_trade_pct": 0.5,
                "max_position_equity_pct": 10.0,
                "max_open_positions": 2,
                "daily_loss_limit_pct": -1.5,
            }
        )
        self.assertEqual(tighter["max_risk_per_trade_pct"], 0.5)
        self.assertEqual(tighter["max_position_equity_pct"], 10.0)
        self.assertEqual(tighter["max_open_positions"], 2)
        self.assertEqual(tighter["daily_loss_limit_pct"], -1.5)

        rm = RiskManager()
        state = AgentLedgerState(agent_id="llm_own_strategy", name="13. Own Strategy", category="Autonomous LLM")
        quote = Quote("AAPL", "2026-11-23T15:00:00Z", 99.98, 100.02, 100.0, 4.0)
        sig = OrderSignal(
            agent_id="llm_own_strategy",
            action="BUY",
            ticker="AAPL",
            stop_loss=99.0,  # $1 stop -> 0.5% risk ($50) = 50 shares ($5,000), but 10% cap = $1,000 (10 shares)
            decision_timestamp="2026-11-23T15:00:00Z",
        )
        res = rm.evaluate(sig, state, quote, stricter_limits=tighter)
        self.assertTrue(res.approved)
        self.assertLessEqual(res.approved_shares, 10.0)

        # Stricter daily loss limit (-1.5%) blocks new buys even when above -3.0% hard limit
        state.daily_pnl_pct = -1.6
        res_halted = rm.evaluate(sig, state, quote, stricter_limits=tighter)
        self.assertFalse(res_halted.approved)
        self.assertIn("-1.5%", res_halted.rejection_reason)

    def test_stock_eligibility_enforces_us_stocks_above_5_and_1m_volume(self) -> None:
        snap = _make_snapshot_with_bars()
        ok_aapl, _ = is_eligible_agent13_stock(snap, "AAPL")
        self.assertTrue(ok_aapl)  # $225 > $5, 2.6M daily shares > 1M

        ok_spy, reason_spy = is_eligible_agent13_stock(snap, "SPY")
        self.assertFalse(ok_spy)  # ETF excluded
        self.assertIn("individual stock", reason_spy)

        ok_intc, reason_intc = is_eligible_agent13_stock(snap, "INTC")
        self.assertFalse(ok_intc)  # $4.50 <= $5.00
        self.assertIn("$5.00", reason_intc)

        ok_msft, reason_msft = is_eligible_agent13_stock(snap, "MSFT")
        self.assertFalse(ok_msft)  # 260,000 <= 1,000,000 shares
        self.assertIn("1,000,000", reason_msft)

    def test_post_close_review_blocks_intraday_changes_and_archives_every_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = OwnStrategyAgent(state_dir=Path(tmp), allow_offline_simulation=True, api_key="")
            hist = agent.ensure_initial_rulebook(overwrite=True, mode="DRY_RUN")
            self.assertEqual(hist["current_version"], 1)
            self.assertEqual(len(hist["versions"]), 1)

            state = AgentLedgerState(
                agent_id="llm_own_strategy",
                name=agent.name,
                category=agent.category,
                equity=9910.0,
                daily_pnl_pct=-0.90,
            )
            state.trades.append(
                TradeRecord(
                    trade_id="t1", agent_id="llm_own_strategy", side="SELL", ticker="AAPL", shares=5.0,
                    decision_timestamp="2026-11-23T15:00:00Z", fill_timestamp="2026-11-23T15:30:00Z",
                    reference_price=220.0, fill_price=219.8, spread_cost_dollars=0.2, slippage_cost_dollars=0.55,
                    commission_dollars=0.0, notional_dollars=1099.0, stop_loss=220.0, take_profit=235.0,
                    realized_pnl=-90.0, realized_pnl_pct=-7.5, exit_reason="STOP_LOSS",
                )
            )

            # 1. Intraday call MUST be blocked (no changes during market hours)
            blocked = agent.run_daily_post_close_review(
                state=state,
                trading_date_et="2026-11-23",
                trading_day_number=1,
                is_market_closed_or_closing=False,
            )
            self.assertIsNone(blocked)
            self.assertEqual(agent.load_history()["current_version"], 1)

            # 2. Post-close call succeeds and archives v1 + v2 with diff & rationale
            rev = agent.run_daily_post_close_review(
                state=state,
                trading_date_et="2026-11-23",
                trading_day_number=1,
                is_market_closed_or_closing=True,
            )
            self.assertIsNotNone(rev)
            self.assertTrue(rev["revised_rulebook"])
            h2 = agent.load_history()
            self.assertEqual(h2["current_version"], 2)
            self.assertEqual(len(h2["versions"]), 2)
            self.assertTrue(len(h2["versions"][1]["changes_from_previous"]) >= 1)

            # 3. Second post-close call on the SAME trading day MUST be ignored (max 1 review/day)
            dup = agent.run_daily_post_close_review(
                state=state,
                trading_date_et="2026-11-23",
                trading_day_number=1,
                is_market_closed_or_closing=True,
            )
            self.assertIsNone(dup)
            self.assertEqual(len(agent.load_history()["daily_reviews"]), 1)

    def test_live_mode_without_api_key_strictly_holds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = OwnStrategyAgent(state_dir=Path(tmp), allow_offline_simulation=False, api_key="")
            snap = _make_snapshot_with_bars()
            state = AgentLedgerState(agent_id="llm_own_strategy", name=agent.name, category=agent.category)
            sig = agent.decide(snap, state)
            self.assertEqual(sig.action, "HOLD")


if __name__ == "__main__":
    unittest.main()
