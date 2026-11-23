"""Unit tests for #11 Berkshire Style and #12 Congress Style pattern-learned agents."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arena.agents.style_agents import BerkshireStyleAgent, CongressStyleAgent
from arena.engine.risk_manager import RiskManager
from arena.models import (
    AgentLedgerState,
    MarketSnapshot,
    Quote,
    TickerIndicators,
)
from arena.research.berkshire_learner import evaluate_berkshire_rules
from arena.research.congress_learner import evaluate_congress_rules


def _make_indicator(ticker: str, price: float, sma20: float = None, sma30: float = None) -> TickerIndicators:
    s20 = sma20 if sma20 is not None else price * 0.99
    s30 = sma30 if sma30 is not None else price * 0.98
    return TickerIndicators(
        ticker=ticker,
        last_price=price,
        prev_close=price * 0.995,
        change_pct_1d=0.5,
        change_pct_5d=1.2,
        sma_10=price * 0.995,
        sma_20=s20,
        sma_30=s30,
        rsi_14=54.0,
        atr_14=price * 0.015,
        bb_upper_20=price * 1.04,
        bb_mid_20=s20,
        bb_lower_20=price * 0.96,
        high_20=price * 1.02,
        low_20=price * 0.95,
        opening_range_high=price * 1.01,
        opening_range_low=price * 0.99,
        volume_ratio=1.15,
        relative_strength_vs_spy=1.4,
    )


class TestStyleAgents(unittest.TestCase):
    def test_berkshire_explicit_rules_accept_value_dividend_large_cap_and_reject_expensive_growth(self) -> None:
        rules = {
            "never_copy_filings": True,
            "timing_learned_from_13f": False,
            "preferred_sectors": ["Financials", "Technology", "Consumer Staples"],
            "min_market_cap_usd": 10_000_000_000.0,
            "min_pe_ratio": 4.0,
            "max_pe_ratio": 30.0,
            "min_dist_from_52w_high_pct": -35.0,
            "max_dist_from_52w_high_pct": 0.0,
            "min_trend_3m_pct": -15.0,
            "max_trend_3m_pct": 25.0,
            "require_dividend": True,
            "min_dividend_yield_pct": 1.0,
        }

        good_stock = {
            "ticker": "JPM",
            "sector": "Financials",
            "market_cap": 650_000_000_000.0,
            "pe_ratio": 14.5,
            "dist_from_52w_high_pct": -8.2,
            "trend_3m_pct": 4.1,
            "dividend_yield_pct": 2.1,
        }
        ok, score, passed, failed = evaluate_berkshire_rules(good_stock, rules)
        self.assertTrue(ok)
        self.assertGreater(score, 80.0)
        self.assertEqual(failed, [])
        self.assertGreaterEqual(len(passed), 5)

        expensive_non_div = {
            "ticker": "PLTR",
            "sector": "Technology",
            "market_cap": 150_000_000_000.0,
            "pe_ratio": 180.0,
            "dist_from_52w_high_pct": -2.0,
            "trend_3m_pct": 65.0,
            "dividend_yield_pct": 0.0,
        }
        ok2, _, _, failed2 = evaluate_berkshire_rules(expensive_non_div, rules)
        self.assertFalse(ok2)
        self.assertGreaterEqual(len(failed2), 3)

    def test_congress_explicit_rules_match_sector_size_and_30d_move(self) -> None:
        rules = {
            "never_copy_filings": True,
            "shares_only": True,
            "preferred_sectors": ["Technology", "Health Care", "Financials"],
            "min_market_cap_usd": 50_000_000_000.0,
            "min_pre_trade_30d_move_pct": -8.0,
            "max_pre_trade_30d_move_pct": 10.0,
            "target_pre_trade_30d_move_pct": 0.5,
            "typical_hold_days": 59,
        }

        good_stock = {
            "ticker": "MSFT",
            "sector": "Technology",
            "market_cap": 3_100_000_000_000.0,
            "move_30d_pct": 2.4,
            "relative_strength_vs_spy": 1.2,
            "volume_ratio": 1.1,
        }
        ok, score, _, failed = evaluate_congress_rules(good_stock, rules)
        self.assertTrue(ok)
        self.assertGreater(score, 75.0)
        self.assertEqual(failed, [])

        overextended_small = {
            "ticker": "SMALL",
            "sector": "Utilities",
            "market_cap": 5_000_000_000.0,
            "move_30d_pct": 32.0,
        }
        ok2, _, _, failed2 = evaluate_congress_rules(overextended_small, rules)
        self.assertFalse(ok2)
        self.assertEqual(len(failed2), 3)

    def test_style_agents_pick_from_universe_without_copying_filings_and_pass_shared_risk_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            b_pat = tmp_path / "berkshire_style.json"
            c_pat = tmp_path / "congress_style.json"
            u_fund = tmp_path / "universe_fundamentals.json"

            b_pat.write_text(
                json.dumps(
                    {
                        "learned_rules": BerkshireStyleAgent.DEFAULT_RULES,
                        "never_copy_filings": True,
                        "timing_learned_from_13f": False,
                    }
                ),
                encoding="utf-8",
            )
            c_pat.write_text(
                json.dumps(
                    {
                        "learned_rules": CongressStyleAgent.DEFAULT_RULES,
                        "never_copy_filings": True,
                        "shares_only": True,
                    }
                ),
                encoding="utf-8",
            )
            u_fund.write_text(
                json.dumps(
                    {
                        "JPM": {
                            "ticker": "JPM",
                            "sector": "Financials",
                            "market_cap": 650_000_000_000.0,
                            "pe_ratio": 14.0,
                            "dividend_yield_pct": 2.2,
                            "dist_from_52w_high_pct": -9.0,
                            "trend_3m_pct": 3.5,
                            "move_30d_pct": 1.8,
                            "high_52w": 265.0,
                            "close": 240.0,
                        },
                        "MSFT": {
                            "ticker": "MSFT",
                            "sector": "Technology",
                            "market_cap": 3_000_000_000_000.0,
                            "pe_ratio": 34.0,
                            "dividend_yield_pct": 0.7,
                            "dist_from_52w_high_pct": -5.0,
                            "trend_3m_pct": 6.0,
                            "move_30d_pct": 2.0,
                            "high_52w": 460.0,
                            "close": 430.0,
                        },
                    }
                ),
                encoding="utf-8",
            )

            snap = MarketSnapshot(
                decision_timestamp="2026-11-23T15:00:00Z",
                trading_date_et="2026-11-23",
                trading_day_number=1,
                is_near_market_close=False,
                session_open_et="09:30",
                session_close_et="16:00",
                bars_by_ticker={},
                quotes_by_ticker={
                    "JPM": Quote("JPM", "2026-11-23T15:00:00Z", 239.95, 240.05, 240.0, 4.17),
                    "MSFT": Quote("MSFT", "2026-11-23T15:00:00Z", 429.90, 430.10, 430.0, 4.65),
                },
                indicators={
                    "JPM": _make_indicator("JPM", 240.0),
                    "MSFT": _make_indicator("MSFT", 430.0),
                },
            )

            rm = RiskManager()

            b_agent = BerkshireStyleAgent(pattern_path=b_pat, universe_fundamentals_path=u_fund)
            b_state = AgentLedgerState(agent_id="style_berkshire", name=b_agent.name, category=b_agent.category)
            b_sig = b_agent.decide(snap, b_state)
            self.assertEqual(b_sig.action, "BUY")
            self.assertEqual(b_sig.ticker, "JPM")
            self.assertIsNotNone(b_sig.stop_loss)
            b_res = rm.evaluate(b_sig, b_state, snap.quotes_by_ticker[b_sig.ticker])
            self.assertTrue(b_res.approved)
            self.assertLessEqual(b_res.approved_shares * snap.quotes_by_ticker[b_sig.ticker].mid_price, 2000.01)
            self.assertLessEqual(b_res.risk_dollars, 100.01)

            c_agent = CongressStyleAgent(pattern_path=c_pat, universe_fundamentals_path=u_fund)
            c_state = AgentLedgerState(agent_id="style_congress", name=c_agent.name, category=c_agent.category)
            c_sig = c_agent.decide(snap, c_state)
            self.assertEqual(c_sig.action, "BUY")
            self.assertIn(c_sig.ticker, ("JPM", "MSFT"))
            c_res = rm.evaluate(c_sig, c_state, snap.quotes_by_ticker[c_sig.ticker])
            self.assertTrue(c_res.approved)
            self.assertLessEqual(c_res.approved_shares * snap.quotes_by_ticker[c_sig.ticker].mid_price, 2000.01)
            self.assertLessEqual(c_res.risk_dollars, 100.01)

    def test_cached_learned_patterns_enforce_no_lookahead_and_no_copying(self) -> None:
        from arena.research.berkshire_learner import BERKSHIRE_PATTERN_PATH
        from arena.research.congress_learner import CONGRESS_PATTERN_PATH

        for p in (BERKSHIRE_PATTERN_PATH, CONGRESS_PATTERN_PATH):
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                self.assertTrue(data["no_lookahead_verified"])
                self.assertTrue(data["never_copy_filings"])
                self.assertIn("out_of_sample_test", data)
                self.assertIn("beat_spy", data["out_of_sample_test"])
                self.assertTrue(len(data.get("plain_language_rules") or []) >= 4)


if __name__ == "__main__":
    unittest.main()
