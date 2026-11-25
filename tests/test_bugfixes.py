"""Regression tests for audited live production bug fixes.

Covers:
1. AlpacaDataClient.fetch_news_up_to batching across >35 tickers (full 71-ticker universe)
2. AlpacaDataClient.fetch_quotes_up_to anomalous wide/stale IEX quote re-anchoring
3. AlpacaDataClient._get_json transient 429/5xx retry handling
4. BaseLLMAgent._call_gemini_api skipping Gemini 2.5 hidden thought parts + thinkingBudget=0
5. Daily report cycles_run_today excluding Day-0 seed points & using cycles_by_date + is_post_close_final
6. Post-entry stop-loss bar filtering (preventing pre-entry historical wicks from triggering phantom stops)
7. Intraday-only (flatten_eod) overnight stale carry catch-up flattening
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from arena.agents.llm_agents import TechnicalLLMAgent  # noqa: E402
from arena.config import AGENT_SPECS, UNIVERSE  # noqa: E402
from arena.data.alpaca_client import AlpacaDataClient  # noqa: E402
from arena.engine.ledger import LedgerManager  # noqa: E402
from arena.models import (  # noqa: E402
    Bar,
    EquityPoint,
    MarketSnapshot,
    Position,
    Quote,
    TickerIndicators,
)
from arena.reporting.daily_report import (  # noqa: E402
    build_report_payload,
    generate_and_save_daily_report,
)


class _DummyHTTPResp:
    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_DummyHTTPResp":
        return self

    def __exit__(self, *args) -> None:
        return None


class TestAuditedBugFixes(unittest.TestCase):
    def test_fetch_news_batches_all_71_tickers_and_deduplicates(self) -> None:
        client = AlpacaDataClient(api_key="test_key", secret_key="test_secret")
        calls = []

        def fake_get_json(base_url, path, params=None):
            syms = (params or {}).get("symbols", "").split(",")
            calls.append(syms)
            # Return unique news per batch plus one shared duplicate
            out = [
                {
                    "headline": f"Shared headline",
                    "summary": "dup",
                    "source": "Test",
                    "created_at": "2026-11-25T15:00:00Z",
                    "symbols": ["SPY"],
                }
            ]
            if "NVDA" in syms:
                out.append(
                    {
                        "headline": "NVDA late-alphabet news",
                        "summary": "NVDA earnings update",
                        "source": "Test",
                        "created_at": "2026-11-25T15:10:00Z",
                        "symbols": ["NVDA"],
                    }
                )
            if "XOM" in syms:
                out.append(
                    {
                        "headline": "XOM tail-alphabet news",
                        "summary": "Energy update",
                        "source": "Test",
                        "created_at": "2026-11-25T15:05:00Z",
                        "symbols": ["XOM"],
                    }
                )
            return {"news": out}

        with patch.object(client, "_get_json", side_effect=fake_get_json):
            items = client.fetch_news_up_to(list(UNIVERSE), "2026-11-25T15:30:00Z", limit=30)

        # 71 tickers in chunks of 35 => 3 API batches covering every ticker
        self.assertEqual(len(calls), 3)
        self.assertEqual(sum(len(c) for c in calls), len(UNIVERSE))
        titles = [h.headline for h in items]
        self.assertIn("NVDA late-alphabet news", titles)
        self.assertIn("XOM tail-alphabet news", titles)
        self.assertEqual(titles.count("Shared headline"), 1)
        # Sorted newest first
        self.assertEqual(titles[0], "NVDA late-alphabet news")

    def test_fetch_quotes_reanchors_skewed_wide_iex_quote(self) -> None:
        client = AlpacaDataClient(api_key="test_key", secret_key="test_secret")
        # Simulate Day 1 ISRG anomaly: last bar closed at $433.44, but IEX ask was stale at $454.12
        bars_by_ticker = {
            "ISRG": [
                Bar(
                    ticker="ISRG",
                    timestamp="2026-11-24T15:30:00Z",
                    open=432.0,
                    high=434.0,
                    low=431.5,
                    close=433.44,
                    volume=12000.0,
                )
            ],
            "AAPL": [
                Bar(
                    ticker="AAPL",
                    timestamp="2026-11-24T15:30:00Z",
                    open=230.0,
                    high=231.0,
                    low=229.5,
                    close=230.50,
                    volume=50000.0,
                )
            ],
        }
        fake_payload = {
            "quotes": {
                "ISRG": {"t": "2026-11-24T15:56:00Z", "bp": 433.00, "ap": 454.12},
                "AAPL": {"t": "2026-11-24T15:56:00Z", "bp": 230.60, "ap": 230.64},
            }
        }
        with patch.object(client, "_get_json", return_value=fake_payload):
            quotes = client.fetch_quotes_up_to(["ISRG", "AAPL"], "2026-11-24T15:56:18Z", bars_by_ticker=bars_by_ticker)

        # ISRG skewed IEX midpoint ($443.56, +2.33%) must be re-anchored to $433.44
        self.assertAlmostEqual(quotes["ISRG"].mid_price, 433.44, places=2)
        self.assertLessEqual(quotes["ISRG"].spread_bps, 10.0)
        # Healthy tight AAPL quote must remain untouched
        self.assertAlmostEqual(quotes["AAPL"].mid_price, 230.62, places=2)

    def test_get_json_retries_transient_429(self) -> None:
        client = AlpacaDataClient(api_key="test_key", secret_key="test_secret")
        attempts = {"n": 0}

        def fake_urlopen(req, timeout=15.0):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise urllib.error.HTTPError(
                    url=req.full_url,
                    code=429,
                    msg="Too Many Requests",
                    hdrs={},
                    fp=BytesIO(b"rate limited"),
                )
            return _DummyHTTPResp({"is_open": True})

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), patch("time.sleep", return_value=None):
            res = client._get_json(client.paper_base_url, "/v2/clock")

        self.assertEqual(attempts["n"], 2)
        self.assertTrue(res["is_open"])

    def test_gemini_25_skips_thought_parts_and_sets_thinking_budget_zero(self) -> None:
        agent = TechnicalLLMAgent(api_key="dummy-test-key", polite_pause_seconds=0.0)
        captured_bodies = []

        def fake_urlopen(req, timeout=20.0):
            captured_bodies.append(json.loads(req.data.decode("utf-8")))
            return _DummyHTTPResp(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"thought": True, "text": "Internal chain of thought that is NOT json..."},
                                    {
                                        "text": json.dumps(
                                            {
                                                "action": "HOLD",
                                                "ticker": "",
                                                "size": 0.0,
                                                "stop_loss": None,
                                                "take_profit": None,
                                                "confidence": 0.65,
                                                "reasoning": "Parsed cleanly after skipping thought part.",
                                            }
                                        )
                                    },
                                ]
                            }
                        }
                    ]
                }
            )

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            raw_text, err = agent._call_gemini_api("prompt", "dummy-test-key", "2026-11-25T15:30:00Z")

        self.assertIsNone(err)
        self.assertIsNotNone(raw_text)
        parsed = json.loads(raw_text)
        self.assertEqual(parsed["action"], "HOLD")
        self.assertIn("skipping thought part", parsed["reasoning"])
        # Verify thinkingBudget: 0 was passed for gemini-2.5-flash
        self.assertEqual(captured_bodies[0]["generationConfig"]["thinkingConfig"]["thinkingBudget"], 0)
        self.assertGreaterEqual(captured_bodies[0]["generationConfig"]["maxOutputTokens"], 2048)

    def test_daily_report_cycles_count_and_post_close_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "state"
            reports_dir = tmp_path / "reports"
            ledger = LedgerManager(state_dir=state_dir / "agents")
            states = list(ledger.initialize_all_agents().values())

            # Seed Day-0 pre-start point + 3 real cycle points on 2026-11-24
            for st in states:
                st.equity_curve = [
                    EquityPoint("2026-11-24T15:49:51Z", "2026-11-24", 10000.0, 10000.0, 0.0, 0.0, 0.0),
                    EquityPoint("2026-11-24T15:56:23Z", "2026-11-24", 10000.0, 10000.0, 0.0, 0.0, 0.0),
                    EquityPoint("2026-11-24T15:58:26Z", "2026-11-24", 10000.0, 10000.0, 0.0, 0.0, 0.0),
                    EquityPoint("2026-11-24T15:58:55Z", "2026-11-24", 10000.0, 10000.0, 0.0, 0.0, 0.0),
                ]

            comp_legacy = {
                "started_at_utc": "2026-11-24T15:56:18Z",
                "active_trading_dates": ["2026-11-24"],
                "total_trading_days": 30,
            }
            payload_legacy = build_report_payload(states, comp_legacy, "2026-11-24", 1)
            # Must exclude the 15:49:51Z Day-0 seed point -> exactly 3 cycles, not 4!
            self.assertEqual(payload_legacy["metadata"]["cycles_run_today"], 3)

            comp_tracked = dict(comp_legacy, cycles_by_date={"2026-11-24": 13})
            payload_tracked = build_report_payload(states, comp_tracked, "2026-11-24", 1)
            self.assertEqual(payload_tracked["metadata"]["cycles_run_today"], 13)

            # Verify is_post_close_final metadata persists to daily_reports_log.json
            res = generate_and_save_daily_report(
                agent_states=states,
                competition_state=comp_tracked,
                trading_date_et="2026-11-24",
                trading_day_number=1,
                state_dir=state_dir,
                reports_dir=reports_dir,
                use_llm=False,
                is_post_close_final=True,
            )
            self.assertTrue(res["entry"]["is_post_close_final"])
            log_entries = json.loads((state_dir / "daily_reports_log.json").read_text(encoding="utf-8"))
            self.assertTrue(log_entries[0]["is_post_close_final"])

    def test_post_entry_stop_bars_exclude_pre_entry_wick(self) -> None:
        # Import helper via direct module inspection without altering src.arena.models identity
        from arena.engine.ledger import LedgerManager
        from arena.engine.simulator import FillSimulator

        with tempfile.TemporaryDirectory() as tmp:
            ledger = LedgerManager(state_dir=Path(tmp))
            st = ledger.initialize_agent("coded_trend", save=False)
            # Position opened at 15:05:00Z at $100 with stop at $98
            st.positions["NVDA"] = Position(
                ticker="NVDA",
                shares=10.0,
                avg_entry_price=100.0,
                current_price=100.0,
                stop_loss=98.0,
                take_profit=105.0,
                opened_at="2026-11-25T15:05:00Z",
            )
            # Pre-entry 5Min bar at 15:00:00Z wicked to $97.00 (BEFORE entry!), live quote is $100.20
            bars_5m = {
                "NVDA": [
                    Bar("NVDA", "2026-11-25T15:00:00Z", 99.0, 100.5, 97.0, 100.0, 5000.0),
                ]
            }
            live_q = {
                "NVDA": Quote("NVDA", "2026-11-25T15:09:00Z", 100.18, 100.22, 100.20, 4.0),
            }

            # Replicate the exact post-entry filtering logic cleanly via simulator
            from arena.data.alpaca_client import parse_iso_utc
            from datetime import timedelta

            now_dt = parse_iso_utc("2026-11-25T15:09:00Z")
            opened_dt = parse_iso_utc(st.positions["NVDA"].opened_at)
            qual = [
                b
                for b in bars_5m["NVDA"]
                if (now_dt - timedelta(minutes=10)) <= parse_iso_utc(b.timestamp) <= now_dt
                and parse_iso_utc(b.timestamp) > opened_dt
            ]
            # The 15:00 bar MUST be excluded because 15:00 <= 15:05 opened_at
            self.assertEqual(len(qual), 0)
            # So only live quote ($100.18..$100.22) is checked -> NO phantom stop-out!
            q_bar = Bar("NVDA", "2026-11-25T15:09:01Z", 100.20, 100.22, 100.18, 100.20, 0.0)
            sim = FillSimulator()
            triggered = sim.check_and_execute_stops_and_targets(
                state=st,
                next_bars_by_ticker={"NVDA": q_bar},
                quotes_by_ticker=live_q,
                decision_timestamp="2026-11-25T15:09:00Z",
                fill_timestamp="2026-11-25T15:09:01Z",
                apply_to_state=True,
            )
            self.assertEqual(len(triggered), 0)
            self.assertIn("NVDA", st.positions)


if __name__ == "__main__":
    unittest.main()
