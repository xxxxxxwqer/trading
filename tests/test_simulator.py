"""Unit tests for the FillSimulator and strict no-look-ahead enforcement."""

from __future__ import annotations

import unittest

from src.arena.engine.simulator import FillSimulator, verify_no_lookahead
from src.arena.models import AgentLedgerState, Bar, OrderSignal, Position, Quote


class TestFillSimulator(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FillSimulator()

    def test_verify_no_lookahead_raises_on_same_or_earlier_fill_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            verify_no_lookahead("2026-11-12T15:00:00Z", "2026-11-12T15:00:00Z")
        with self.assertRaises(ValueError):
            verify_no_lookahead("2026-11-12T15:00:00Z", "2026-11-12T14:55:00Z")

        # Strictly greater fill timestamp succeeds
        verify_no_lookahead("2026-11-12T15:00:00Z", "2026-11-12T15:01:00Z")

    def test_buy_and_sell_cross_spread_and_incur_5bps_slippage_with_zero_commission(self) -> None:
        quote = Quote(
            ticker="AAPL",
            timestamp="2026-11-12T15:01:00Z",
            bid_price=99.98,
            ask_price=100.02,
            mid_price=100.00,
            spread_bps=4.0,
        )
        buy_sig = OrderSignal(
            agent_id="coded_trend",
            action="BUY",
            ticker="AAPL",
            stop_loss=98.0,
            take_profit=105.0,
            requested_shares=10.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        buy_trade = self.sim.simulate_fill(buy_sig, quote, shares=10.0)
        expected_buy_fill = round(100.02 * (1.0 + 0.0005), 4)
        self.assertEqual(buy_trade.fill_price, expected_buy_fill)
        self.assertEqual(buy_trade.commission_dollars, 0.0)
        self.assertAlmostEqual(buy_trade.spread_cost_dollars, round((100.02 - 100.00) * 10.0, 4), places=4)
        self.assertAlmostEqual(
            buy_trade.slippage_cost_dollars,
            round((expected_buy_fill - 100.02) * 10.0, 4),
            places=4,
        )

        sell_sig = OrderSignal(
            agent_id="coded_trend",
            action="SELL",
            ticker="AAPL",
            requested_shares=10.0,
            decision_timestamp="2026-11-12T15:00:00Z",
        )
        sell_trade = self.sim.simulate_fill(sell_sig, quote, shares=10.0)
        expected_sell_fill = round(99.98 * (1.0 - 0.0005), 4)
        self.assertEqual(sell_trade.fill_price, expected_sell_fill)
        self.assertEqual(sell_trade.commission_dollars, 0.0)
        self.assertAlmostEqual(sell_trade.spread_cost_dollars, round((100.00 - 99.98) * 10.0, 4), places=4)
        self.assertAlmostEqual(
            sell_trade.slippage_cost_dollars,
            round((99.98 - expected_sell_fill) * 10.0, 4),
            places=4,
        )

    def test_intrabar_stop_loss_triggers_at_min_of_stop_and_open(self) -> None:
        state = AgentLedgerState(
            agent_id="coded_trend",
            name="Trend",
            category="Coded Strategy",
            cash=8_000.0,
            equity=10_000.0,
        )
        state.positions["AAPL"] = Position(
            ticker="AAPL",
            shares=20.0,
            avg_entry_price=100.0,
            current_price=100.0,
            stop_loss=97.0,
            take_profit=106.0,
            opened_at="2026-11-12T14:30:00Z",
            market_value=2_000.0,
        )

        # Next bar opens at 98.50 and drops to 96.50 -> triggers at min(97.0, 98.50) = 97.0
        next_bar = Bar(
            ticker="AAPL",
            timestamp="2026-11-12T15:00:00Z",
            open=98.50,
            high=99.00,
            low=96.50,
            close=96.80,
            volume=100_000,
        )
        trades = self.sim.check_and_execute_stops_and_targets(state, {"AAPL": next_bar})
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.exit_reason, "STOP_LOSS")
        self.assertEqual(t.reference_price, 97.0)
        self.assertLess(t.fill_price, 97.0)
        self.assertLess(t.realized_pnl or 0.0, 0.0)
        self.assertNotIn("AAPL", state.positions)

    def test_intrabar_take_profit_triggers_at_max_of_target_and_open(self) -> None:
        state = AgentLedgerState(
            agent_id="coded_trend",
            name="Trend",
            category="Coded Strategy",
            cash=8_000.0,
            equity=10_000.0,
        )
        state.positions["NVDA"] = Position(
            ticker="NVDA",
            shares=10.0,
            avg_entry_price=100.0,
            current_price=100.0,
            stop_loss=95.0,
            take_profit=105.0,
            opened_at="2026-11-12T14:30:00Z",
            market_value=1_000.0,
        )

        # Gap-up open at 106.0 above take_profit 105.0 -> triggers at max(105.0, 106.0) = 106.0
        next_bar = Bar(
            ticker="NVDA",
            timestamp="2026-11-12T15:00:00Z",
            open=106.0,
            high=107.5,
            low=105.5,
            close=107.0,
            volume=150_000,
        )
        trades = self.sim.check_and_execute_stops_and_targets(state, {"NVDA": next_bar})
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertEqual(t.exit_reason, "TAKE_PROFIT")
        self.assertEqual(t.reference_price, 106.0)
        self.assertGreater(t.realized_pnl or 0.0, 0.0)
        self.assertNotIn("NVDA", state.positions)


if __name__ == "__main__":
    unittest.main()
