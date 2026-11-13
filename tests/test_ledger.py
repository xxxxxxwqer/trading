"""Unit tests for the LedgerManager, mark-to-market, circuit breakers, EOD flatten, and metrics."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.arena.engine.ledger import LedgerManager, compute_leaderboard_metrics
from src.arena.engine.simulator import FillSimulator
from src.arena.models import Quote, TradeRecord


class TestLedgerManager(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger = LedgerManager(state_dir=Path(self.temp_dir.name))
        self.sim = FillSimulator()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_initialize_save_and_load_round_trip(self) -> None:
        state = self.ledger.initialize_agent(
            "coded_trend",
            initial_cash=10_000.0,
            timestamp="2026-11-12T14:30:00Z",
            trading_date_et="2026-11-12",
        )
        loaded = self.ledger.load_agent_state("coded_trend")
        self.assertEqual(loaded.agent_id, "coded_trend")
        self.assertEqual(loaded.cash, 10_000.0)
        self.assertEqual(loaded.current_trading_date_et, "2026-11-12")

    def test_fractional_shares_weighted_avg_entry_and_realized_pnl(self) -> None:
        state = self.ledger.initialize_agent("coded_trend", trading_date_et="2026-11-12")

        # Buy 10.25 shares @ $100.00
        buy1 = TradeRecord(
            trade_id="t1",
            agent_id="coded_trend",
            side="BUY",
            ticker="AAPL",
            shares=10.25,
            decision_timestamp="2026-11-12T14:30:00Z",
            fill_timestamp="2026-11-12T14:31:00Z",
            reference_price=100.0,
            fill_price=100.0,
            spread_cost_dollars=0.2,
            slippage_cost_dollars=0.5,
            commission_dollars=0.0,
            notional_dollars=1_025.00,
            stop_loss=95.0,
            take_profit=110.0,
            reasoning="Initial breakout entry",
        )
        self.ledger.apply_trade(state, buy1)
        self.assertAlmostEqual(state.cash, 8_975.00, places=2)
        self.assertAlmostEqual(state.positions["AAPL"].shares, 10.25, places=4)
        self.assertAlmostEqual(state.positions["AAPL"].avg_entry_price, 100.0, places=4)

        # Add 9.75 shares @ $110.00 -> total 20.0 shares, weighted avg = (1025 + 1072.50)/20 = 104.875
        buy2 = TradeRecord(
            trade_id="t2",
            agent_id="coded_trend",
            side="BUY",
            ticker="AAPL",
            shares=9.75,
            decision_timestamp="2026-11-12T15:00:00Z",
            fill_timestamp="2026-11-12T15:01:00Z",
            reference_price=110.0,
            fill_price=110.0,
            spread_cost_dollars=0.2,
            slippage_cost_dollars=0.5,
            commission_dollars=0.0,
            notional_dollars=1_072.50,
            stop_loss=102.0,
            take_profit=120.0,
            reasoning="Pyramid add-on",
        )
        self.ledger.apply_trade(state, buy2)
        self.assertAlmostEqual(state.positions["AAPL"].shares, 20.0, places=4)
        self.assertAlmostEqual(state.positions["AAPL"].avg_entry_price, 104.875, places=4)

        # Sell all 20.0 shares @ $115.00 -> proceeds = $2,300.00, cost = $2,097.50 -> realized PnL = +$202.50
        sell = TradeRecord(
            trade_id="t3",
            agent_id="coded_trend",
            side="SELL",
            ticker="AAPL",
            shares=20.0,
            decision_timestamp="2026-11-12T15:30:00Z",
            fill_timestamp="2026-11-12T15:31:00Z",
            reference_price=115.0,
            fill_price=115.0,
            spread_cost_dollars=0.2,
            slippage_cost_dollars=0.5,
            commission_dollars=0.0,
            notional_dollars=2_300.00,
            stop_loss=102.0,
            take_profit=120.0,
            exit_reason="AGENT_SIGNAL",
            reasoning="Target reached",
        )
        self.ledger.apply_trade(state, sell)
        self.assertNotIn("AAPL", state.positions)
        self.assertAlmostEqual(sell.realized_pnl or 0.0, 202.50, places=2)
        self.assertAlmostEqual(state.cash, 10_202.50, places=2)

    def test_mark_to_market_circuit_breakers_and_new_day_rollover(self) -> None:
        state = self.ledger.initialize_agent("coded_mean_rev", trading_date_et="2026-11-12")
        buy = TradeRecord(
            trade_id="t_cb",
            agent_id="coded_mean_rev",
            side="BUY",
            ticker="NVDA",
            shares=50.0,
            decision_timestamp="2026-11-12T14:30:00Z",
            fill_timestamp="2026-11-12T14:31:00Z",
            reference_price=100.0,
            fill_price=100.0,
            spread_cost_dollars=0.0,
            slippage_cost_dollars=0.0,
            commission_dollars=0.0,
            notional_dollars=5_000.0,
            stop_loss=75.0,
            take_profit=120.0,
        )
        self.ledger.apply_trade(state, buy)

        # Drop NVDA from $100 to $93 -> position value = $4,650, cash = $5,000 -> equity = $9,650 (-3.5% daily loss)
        self.ledger.update_mark_to_market(state, {"NVDA": 93.0}, "2026-11-12T15:00:00Z", "2026-11-12")
        self.assertEqual(state.status, "DAILY_HALTED")
        self.assertAlmostEqual(state.daily_pnl_pct, -3.5, places=2)
        self.assertAlmostEqual(state.max_drawdown_pct, -3.5, places=2)

        # New trading day rollover resets DAILY_HALTED back to ACTIVE and updates day_start_equity
        rolled = self.ledger.start_of_day_check(state, "2026-11-13")
        self.assertTrue(rolled)
        self.assertEqual(state.status, "ACTIVE")
        self.assertEqual(state.day_start_equity, 9_650.0)

        # Drop NVDA to $79 -> position value = $3,950, cash = $5,000 -> equity = $8,950 (-10.5% total drawdown)
        self.ledger.update_mark_to_market(state, {"NVDA": 79.0}, "2026-11-13T15:00:00Z", "2026-11-13")
        self.assertEqual(state.status, "BLOWN_UP")
        self.assertLessEqual(state.current_drawdown_pct, -10.0)

        # Subsequent day rollover MUST NOT reset BLOWN_UP
        self.ledger.start_of_day_check(state, "2026-11-16")
        self.assertEqual(state.status, "BLOWN_UP")

    def test_flatten_all_positions_and_leaderboard_metrics(self) -> None:
        state = self.ledger.initialize_agent("coded_orb", trading_date_et="2026-11-12")
        buy = TradeRecord(
            trade_id="t_orb",
            agent_id="coded_orb",
            side="BUY",
            ticker="QQQ",
            shares=10.0,
            decision_timestamp="2026-11-12T14:30:00Z",
            fill_timestamp="2026-11-12T14:31:00Z",
            reference_price=200.0,
            fill_price=200.0,
            spread_cost_dollars=0.4,
            slippage_cost_dollars=1.0,
            commission_dollars=0.0,
            notional_dollars=2_000.0,
            stop_loss=196.0,
            take_profit=208.0,
            reasoning="ORB breakout above 30m high",
        )
        self.ledger.apply_trade(state, buy)

        close_quote = Quote(
            ticker="QQQ",
            timestamp="2026-11-12T20:55:00Z",
            bid_price=204.96,
            ask_price=205.04,
            mid_price=205.00,
            spread_bps=3.9,
        )
        flattened = self.ledger.flatten_all_positions(
            state=state,
            quotes_by_ticker={"QQQ": close_quote},
            decision_timestamp="2026-11-12T20:54:00Z",
            fill_timestamp="2026-11-12T20:55:00Z",
            exit_reason="EOD_FLAT",
            simulator=self.sim,
        )
        self.assertEqual(len(flattened), 1)
        self.assertEqual(len(state.positions), 0)
        self.assertEqual(flattened[0].exit_reason, "EOD_FLAT")

        self.ledger.update_mark_to_market(state, {"QQQ": 205.0}, "2026-11-12T20:55:00Z", "2026-11-12")
        journal = self.ledger.update_daily_journal(state, "2026-11-12", trading_day_number=1)
        self.assertEqual(journal.trades_count, 2)
        self.assertIn("QQQ", journal.summary_text)

        metrics = compute_leaderboard_metrics(state, spy_return_pct=0.25)
        self.assertGreater(metrics["equity"], 10_000.0)
        self.assertGreater(metrics["profit_dollars"], 0.0)
        self.assertEqual(metrics["trades_count"], 2)
        self.assertEqual(metrics["closed_trades_count"], 1)
        self.assertEqual(metrics["win_rate_pct"], 100.0)
        self.assertGreater(metrics["avg_win_dollars"], 0.0)


if __name__ == "__main__":
    unittest.main()
