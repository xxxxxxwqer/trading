"""Gemini Flash LLM trading agents, shared snapshot builder, and strict JSON validator."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple, Type

from ..config import (
    AGENT_SPECS_BY_ID,
    INDEX_AND_MACRO_ETFS,
    LIQUID_LARGE_CAP_STOCKS,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_EQUITY_PCT,
    MIN_STOP_DISTANCE_PCT,
    SECTOR_ETFS,
    UNIVERSE_SET,
    AgentSpec,
)
from ..models import AgentLedgerState, MarketSnapshot, OrderSignal, TickerIndicators
from .base import BaseAgent

logger = logging.getLogger(__name__)

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
PRIMARY_GEMINI_MODEL = "gemini-2.5-flash"
FALLBACK_GEMINI_MODEL = "gemini-2.0-flash"
MAX_LLM_CALLS_PER_CYCLE = 4
DEFAULT_POLITE_PAUSE_SECONDS = 1.2

# Module-level state for free-tier rate limiting and shared payload caching
_LAST_GEMINI_CALL_MONO: float = 0.0
_CYCLE_LLM_CALL_COUNTS: Dict[str, int] = {}
_SHARED_MARKET_PAYLOAD_CACHE: Tuple[int, str, Dict[str, Any]] = (0, "", {})

_POSITIVE_NEWS_KEYWORDS = (
    "beat",
    "beats",
    "upgrade",
    "upgraded",
    "surge",
    "surges",
    "record",
    "launch",
    "launches",
    "partnership",
    "strong",
    "bullish",
    "outperform",
    "growth",
    "rally",
    "rallies",
    "demand",
    "breakthrough",
    "raises",
    "buyback",
    "approval",
    "contract",
    "expands",
    "soars",
    "tops",
)

_NEGATIVE_NEWS_KEYWORDS = (
    "miss",
    "misses",
    "downgrade",
    "downgraded",
    "probe",
    "lawsuit",
    "cut",
    "cuts",
    "weak",
    "drop",
    "drops",
    "warning",
    "delay",
    "decline",
    "declines",
    "recall",
    "investigation",
    "slump",
    "falls",
    "plunge",
    "headwind",
    "deficit",
)


def _sanitize_error_message(message: str, api_key: Optional[str] = None) -> str:
    """Strip any API key or query-string key parameter from error messages."""
    if not message:
        return "Unknown LLM error"
    cleaned = str(message)
    if api_key:
        cleaned = cleaned.replace(api_key, "[REDACTED]")
    cleaned = re.sub(r"key=[^&\s\"']+", "key=[REDACTED]", cleaned)
    return cleaned


def _build_cached_market_and_news_section(snapshot: MarketSnapshot) -> Dict[str, Any]:
    """Build (and cache per snapshot cycle) the shared market universe and news payload."""
    global _SHARED_MARKET_PAYLOAD_CACHE
    cache_id, cache_ts, cached_data = _SHARED_MARKET_PAYLOAD_CACHE
    if cache_id == id(snapshot) and cache_ts == snapshot.decision_timestamp and cached_data:
        return cached_data

    market_universe: List[Dict[str, Any]] = []
    for ticker in sorted(snapshot.indicators.keys()):
        ind: TickerIndicators = snapshot.indicators[ticker]
        market_universe.append(
            {
                "ticker": ind.ticker,
                "last_price": round(float(ind.last_price), 2),
                "change_pct_1d": round(float(ind.change_pct_1d), 2),
                "change_pct_5d": round(float(ind.change_pct_5d), 2),
                "sma_10": round(float(ind.sma_10), 2),
                "sma_20": round(float(ind.sma_20), 2),
                "sma_30": round(float(ind.sma_30), 2),
                "rsi_14": round(float(ind.rsi_14), 1),
                "atr_14": round(float(ind.atr_14), 2),
                "bb_lower_20": round(float(ind.bb_lower_20), 2),
                "bb_upper_20": round(float(ind.bb_upper_20), 2),
                "high_20": round(float(ind.high_20), 2),
                "low_20": round(float(ind.low_20), 2),
                "volume_ratio": round(float(ind.volume_ratio), 2),
                "relative_strength_vs_spy": round(float(ind.relative_strength_vs_spy), 2),
            }
        )

    recent_news: List[Dict[str, Any]] = []
    for item in snapshot.news[:20]:
        recent_news.append(
            {
                "headline": item.headline,
                "symbols": list(item.symbols),
                "created_at": item.created_at,
                "source": item.source,
            }
        )

    shared_section = {
        "decision_timestamp": snapshot.decision_timestamp,
        "trading_date_et": snapshot.trading_date_et,
        "trading_day_number": int(snapshot.trading_day_number),
        "is_near_market_close": bool(snapshot.is_near_market_close),
        "regime_summary": snapshot.regime_summary or "Neutral / mixed intraday regime",
        "market_universe": market_universe,
        "recent_news": recent_news,
    }
    _SHARED_MARKET_PAYLOAD_CACHE = (id(snapshot), snapshot.decision_timestamp, shared_section)
    return shared_section


def build_shared_llm_snapshot_payload(
    snapshot: MarketSnapshot,
    state: AgentLedgerState,
) -> Dict[str, Any]:
    """Construct the standardized point-in-time market & portfolio payload for all 4 LLM traders.

    Every cycle, all 4 LLM traders receive the exact same market prices, technical
    indicators, regime summary, and recent headlines, alongside their own current
    portfolio state.
    """
    shared_section = _build_cached_market_and_news_section(snapshot)

    open_positions_payload: List[Dict[str, Any]] = []
    for ticker in sorted(state.positions.keys()):
        pos = state.positions[ticker]
        open_positions_payload.append(
            {
                "ticker": pos.ticker,
                "shares": round(float(pos.shares), 4),
                "entry_price": round(float(pos.avg_entry_price), 2),
                "current_price": round(float(pos.current_price), 2),
                "stop_loss": round(float(pos.stop_loss), 2),
                "take_profit": (
                    round(float(pos.take_profit), 2) if pos.take_profit is not None else None
                ),
                "unrealized_pnl_pct": round(float(pos.unrealized_pnl_pct), 2),
            }
        )

    return {
        "decision_timestamp": shared_section["decision_timestamp"],
        "trading_date_et": shared_section["trading_date_et"],
        "trading_day_number": shared_section["trading_day_number"],
        "is_near_market_close": shared_section["is_near_market_close"],
        "regime_summary": shared_section["regime_summary"],
        "portfolio": {
            "cash": round(float(state.cash), 2),
            "equity": round(float(state.equity), 2),
            "daily_pnl_pct": round(float(state.daily_pnl_pct), 2),
            "current_drawdown_pct": round(float(state.current_drawdown_pct), 2),
            "open_positions_count": len(open_positions_payload),
            "max_open_positions": MAX_OPEN_POSITIONS,
            "open_positions": open_positions_payload,
        },
        "market_universe": shared_section["market_universe"],
        "recent_news": shared_section["recent_news"],
    }


def _strip_markdown_fences(raw_text: str) -> str:
    """Strip optional ```json ... ``` fences or surrounding prose around a JSON object."""
    text = (raw_text or "").strip()
    if not text:
        return ""

    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1].strip()
    return text


def _get_reference_price(
    ticker: str,
    snapshot: MarketSnapshot,
    state: AgentLedgerState,
) -> Optional[float]:
    """Lookup current reference price for a ticker from indicators, quotes, or open position."""
    if ticker in snapshot.indicators:
        price = float(snapshot.indicators[ticker].last_price)
        if math.isfinite(price) and price > 0:
            return price
    if ticker in snapshot.quotes_by_ticker:
        quote = snapshot.quotes_by_ticker[ticker]
        price = float(quote.ask_price or quote.mid_price or quote.bid_price)
        if math.isfinite(price) and price > 0:
            return price
    if ticker in state.positions:
        price = float(state.positions[ticker].current_price)
        if math.isfinite(price) and price > 0:
            return price
    return None


def parse_and_validate_llm_json(
    raw_text: str,
    agent_id: str,
    snapshot: MarketSnapshot,
    state: AgentLedgerState,
) -> OrderSignal:
    """Parse and strictly validate LLM JSON response into an OrderSignal.

    Hard rule: Any invalid answer, JSON syntax error, missing/invalid stop-loss
    on BUY, invalid ticker, or out-of-bounds value immediately defaults to HOLD
    with a descriptive reasoning string (never raises an unhandled exception).
    """
    decision_ts = getattr(snapshot, "decision_timestamp", "") or ""

    def _safe_hold(reason: str, confidence: float = 0.5) -> OrderSignal:
        conf = max(0.0, min(1.0, float(confidence))) if math.isfinite(confidence) else 0.5
        return OrderSignal(
            agent_id=agent_id,
            action="HOLD",
            ticker="",
            stop_loss=None,
            take_profit=None,
            confidence=round(conf, 2),
            reasoning=reason[:500],
            requested_shares=None,
            decision_timestamp=decision_ts,
        )

    try:
        cleaned = _strip_markdown_fences(raw_text)
        if not cleaned:
            return _safe_hold("HOLD fallback: Empty LLM response payload.")

        try:
            payload = json.loads(cleaned)
        except Exception as json_exc:
            return _safe_hold(f"HOLD fallback: Malformed LLM JSON ({type(json_exc).__name__}).")

        if not isinstance(payload, dict):
            return _safe_hold("HOLD fallback: LLM JSON root must be an object.")

        raw_action = payload.get("action")
        if not isinstance(raw_action, str):
            return _safe_hold("HOLD fallback: Missing or non-string 'action' field in LLM JSON.")

        action = raw_action.strip().upper()
        if action not in {"BUY", "SELL", "HOLD"}:
            return _safe_hold(
                f"HOLD fallback: Unsupported action '{raw_action}'; must be BUY, SELL, or HOLD."
            )

        raw_conf = payload.get("confidence", 0.5)
        try:
            confidence = float(raw_conf) if raw_conf is not None else 0.5
            if not math.isfinite(confidence):
                confidence = 0.5
            confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            confidence = 0.5

        raw_reasoning = payload.get("reasoning")
        if isinstance(raw_reasoning, str) and raw_reasoning.strip():
            reasoning = " ".join(raw_reasoning.strip().split())
        else:
            reasoning = f"{agent_id} emitted {action} signal."

        if action == "HOLD":
            return _safe_hold(reasoning, confidence=confidence)

        raw_ticker = payload.get("ticker")
        if not isinstance(raw_ticker, str) or not raw_ticker.strip():
            return _safe_hold(
                f"HOLD fallback: LLM emitted {action} without a valid ticker symbol ({reasoning}).",
                confidence=confidence,
            )
        ticker = raw_ticker.strip().upper()

        if action == "SELL":
            if ticker not in state.positions or state.positions[ticker].shares <= 0:
                return _safe_hold(
                    f"HOLD fallback: Cannot SELL {ticker} because it is not an open position ({reasoning}).",
                    confidence=confidence,
                )
            held_shares = float(state.positions[ticker].shares)
            requested_shares = round(held_shares, 4)
            raw_size = payload.get("size")
            if raw_size is not None:
                try:
                    size_val = float(raw_size)
                    if math.isfinite(size_val) and size_val > 1.0 and size_val < held_shares:
                        requested_shares = round(size_val, 4)
                except (TypeError, ValueError):
                    pass

            return OrderSignal(
                agent_id=agent_id,
                action="SELL",
                ticker=ticker,
                stop_loss=None,
                take_profit=None,
                confidence=round(confidence, 2),
                reasoning=reasoning[:500],
                requested_shares=requested_shares,
                decision_timestamp=decision_ts,
            )

        # Action == "BUY"
        if ticker not in UNIVERSE_SET:
            return _safe_hold(
                f"HOLD fallback: Ticker '{ticker}' is outside the approved liquid universe ({reasoning}).",
                confidence=confidence,
            )

        ref_price = _get_reference_price(ticker, snapshot, state)
        if ref_price is None or ref_price <= 0:
            return _safe_hold(
                f"HOLD fallback: No valid market reference price available for {ticker} ({reasoning}).",
                confidence=confidence,
            )

        # Hard rule: Missing or invalid stop_loss on BUY -> HOLD
        raw_stop = payload.get("stop_loss")
        if raw_stop is None:
            return _safe_hold(
                f"HOLD fallback: BUY {ticker} rejected due to missing stop_loss ({reasoning}).",
                confidence=confidence,
            )
        try:
            stop_loss = float(raw_stop)
        except (TypeError, ValueError):
            return _safe_hold(
                f"HOLD fallback: BUY {ticker} rejected due to non-numeric stop_loss '{raw_stop}' ({reasoning}).",
                confidence=confidence,
            )

        if not math.isfinite(stop_loss) or stop_loss <= 0 or stop_loss >= ref_price:
            return _safe_hold(
                f"HOLD fallback: BUY {ticker} rejected because stop_loss ({stop_loss}) must be > 0 and below current price ({ref_price:.2f}).",
                confidence=confidence,
            )

        min_stop_gap = ref_price * MIN_STOP_DISTANCE_PCT
        if (ref_price - stop_loss) < min_stop_gap:
            stop_loss = round(ref_price - min_stop_gap, 2)
            if stop_loss <= 0:
                return _safe_hold(
                    f"HOLD fallback: BUY {ticker} stop_loss too close to reference price ({ref_price:.2f}).",
                    confidence=confidence,
                )

        take_profit: Optional[float] = None
        raw_tp = payload.get("take_profit")
        if raw_tp is not None:
            try:
                tp_val = float(raw_tp)
                if not math.isfinite(tp_val) or tp_val <= ref_price:
                    return _safe_hold(
                        f"HOLD fallback: BUY {ticker} rejected because take_profit ({raw_tp}) must be above current price ({ref_price:.2f}).",
                        confidence=confidence,
                    )
                take_profit = round(tp_val, 2)
            except (TypeError, ValueError):
                return _safe_hold(
                    f"HOLD fallback: BUY {ticker} rejected due to invalid take_profit '{raw_tp}'.",
                    confidence=confidence,
                )

        # Convert 'size' (fraction of equity 0.0 < size <= 1.0, or share count > 1.0) to requested_shares
        requested_shares: Optional[float] = None
        raw_size = payload.get("size")
        if raw_size is not None:
            try:
                size_val = float(raw_size)
                if math.isfinite(size_val) and size_val > 0:
                    if size_val <= 1.0:
                        capped_fraction = min(size_val, MAX_POSITION_EQUITY_PCT)
                        equity_base = max(float(state.equity), 0.0)
                        if equity_base > 0:
                            requested_shares = round((equity_base * capped_fraction) / ref_price, 4)
                    else:
                        requested_shares = round(size_val, 4)
                    if requested_shares is not None and requested_shares <= 0:
                        requested_shares = None
            except (TypeError, ValueError):
                requested_shares = None

        return OrderSignal(
            agent_id=agent_id,
            action="BUY",
            ticker=ticker,
            stop_loss=round(stop_loss, 2),
            take_profit=take_profit,
            confidence=round(confidence, 2),
            reasoning=reasoning[:500],
            requested_shares=requested_shares,
            decision_timestamp=decision_ts,
        )
    except Exception as exc:
        return _safe_hold(
            f"HOLD fallback: Unexpected validation error ({type(exc).__name__})."
        )


class BaseLLMAgent(BaseAgent):
    """Base class for the 4 Gemini Flash LLM trading personas."""

    DEFAULT_AGENT_ID: str = ""
    PERSONA_PROMPT: str = ""

    def __new__(
        cls,
        spec: Optional[AgentSpec] = None,
        *args: Any,
        **kwargs: Any,
    ) -> "BaseLLMAgent":
        if cls is BaseLLMAgent:
            agent_id = spec.agent_id if spec is not None else kwargs.get("agent_id", "")
            target_cls = LLM_AGENT_CLASSES.get(str(agent_id))
            if target_cls is not None:
                return super().__new__(target_cls)
        return super().__new__(cls)

    def __init__(
        self,
        spec: Optional[AgentSpec] = None,
        allow_offline_simulation: Optional[bool] = None,
        allow_offline_llm_simulation: Optional[bool] = None,
        api_key: Optional[str] = None,
        polite_pause_seconds: float = DEFAULT_POLITE_PAUSE_SECONDS,
        timeout_seconds: float = 20.0,
        **kwargs: Any,
    ) -> None:
        if spec is None and "agent_id" in kwargs and kwargs["agent_id"] in AGENT_SPECS_BY_ID:
            spec = AGENT_SPECS_BY_ID[str(kwargs["agent_id"])]
        super().__init__(spec=spec)
        if allow_offline_simulation is None and allow_offline_llm_simulation is not None:
            allow_offline_simulation = allow_offline_llm_simulation
        if allow_offline_simulation is None and "offline_simulation" in kwargs:
            allow_offline_simulation = bool(kwargs["offline_simulation"])
        if allow_offline_simulation is None:
            env_flag = os.environ.get("ARENA_ALLOW_OFFLINE_LLM_SIMULATION", "").strip().lower()
            allow_offline_simulation = env_flag in {"1", "true", "yes"}
        self.allow_offline_simulation: bool = bool(allow_offline_simulation)
        self._explicit_api_key: Optional[str] = api_key
        self.polite_pause_seconds: float = max(0.0, float(polite_pause_seconds))
        self.timeout_seconds: float = max(5.0, float(timeout_seconds))

    def _get_api_key(self) -> str:
        if self._explicit_api_key is not None:
            return self._explicit_api_key.strip()
        return os.environ.get("GEMINI_API_KEY", "").strip()

    def build_prompt(self, payload: Dict[str, Any]) -> str:
        """Build the system/user prompt combining persona playbook, risk rules, and market JSON."""
        compact_payload = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        return (
            f"You are '{self.name}' (agent_id='{self.agent_id}') competing in the 10-Day AI Trading Arena.\n"
            f"PLAYBOOK & PERSONA:\n{self.spec.playbook}\n"
            f"{self.PERSONA_PROMPT}\n\n"
            "SHARED ARENA RISK RULES (ENFORCED BY SYSTEM RISK MANAGER):\n"
            "- Starting capital: $10,000. Long-only US stocks and ETFs from the provided market_universe.\n"
            "- Max risk per trade: 1.0% of current equity (sized automatically by distance from current price to stop_loss).\n"
            "- Max single position size: 20% of equity (size <= 0.20). Max 5 open positions at any time.\n"
            "- Every BUY order MUST include a numeric stop_loss strictly below last_price and a take_profit above last_price.\n"
            "- Every SELL order must reference a ticker currently in portfolio.open_positions.\n"
            "- Daily loss limit is -3.0% (halts new entries for the day); max total drawdown is -10.0%.\n\n"
            "REQUIRED OUTPUT FORMAT:\n"
            "Respond ONLY with a single valid JSON object matching this exact schema (no markdown fences, no extra keys):\n"
            "{\n"
            '  "action": "BUY" | "SELL" | "HOLD",\n'
            '  "ticker": "NVDA",\n'
            '  "size": 0.15,\n'
            '  "stop_loss": 135.50,\n'
            '  "take_profit": 148.00,\n'
            '  "confidence": 0.78,\n'
            '  "reasoning": "Concise 1-2 sentence explanation citing specific data from the snapshot."\n'
            "}\n"
            "For HOLD, set ticker to \"\", size to 0.0, stop_loss to null, and take_profit to null.\n\n"
            f"POINT-IN-TIME MARKET & PORTFOLIO SNAPSHOT:\n{compact_payload}\n"
        )

    def _call_gemini_api(self, prompt_text: str, api_key: str, decision_ts: str) -> Tuple[Optional[str], Optional[str]]:
        """Invoke Gemini Flash via standard library urllib.request with fallback and rate-limit protection.

        Returns (raw_response_text, error_reason). Never logs or exposes api_key.
        """
        global _LAST_GEMINI_CALL_MONO

        cycle_calls = _CYCLE_LLM_CALL_COUNTS.get(decision_ts, 0)
        if cycle_calls >= MAX_LLM_CALLS_PER_CYCLE:
            return None, f"Cycle LLM call cap ({MAX_LLM_CALLS_PER_CYCLE}) reached for {decision_ts}"

        # Polite pause between Gemini calls to stay comfortably within the free tier
        now_mono = time.monotonic()
        elapsed = now_mono - _LAST_GEMINI_CALL_MONO
        if _LAST_GEMINI_CALL_MONO > 0 and elapsed < self.polite_pause_seconds:
            time.sleep(self.polite_pause_seconds - elapsed)

        _CYCLE_LLM_CALL_COUNTS[decision_ts] = cycle_calls + 1

        request_body = json.dumps(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt_text}],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 1024,
                    "responseMimeType": "application/json",
                },
            }
        ).encode("utf-8")

        models_to_try = (PRIMARY_GEMINI_MODEL, FALLBACK_GEMINI_MODEL)
        last_error = "Gemini API request failed"

        for idx, model_name in enumerate(models_to_try):
            url = f"{GEMINI_API_BASE_URL}/{model_name}:generateContent?key={api_key}"
            req = urllib.request.Request(
                url=url,
                data=request_body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                _LAST_GEMINI_CALL_MONO = time.monotonic()
                with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw_bytes = resp.read()
                resp_data = json.loads(raw_bytes.decode("utf-8"))
                candidates = resp_data.get("candidates") or []
                if not candidates:
                    last_error = f"Empty candidates from {model_name}"
                    continue
                content = candidates[0].get("content") or {}
                parts = content.get("parts") or []
                if not parts or "text" not in parts[0]:
                    last_error = f"Missing text part in {model_name} response"
                    continue
                return str(parts[0]["text"]), None
            except urllib.error.HTTPError as http_err:
                status_code = getattr(http_err, "code", 0)
                if status_code == 429:
                    last_error = f"Gemini free-tier rate limit (HTTP 429) on {model_name}"
                else:
                    last_error = f"Gemini HTTP {status_code} on {model_name}"
                if idx == 0:
                    time.sleep(0.5)
                    continue
            except urllib.error.URLError as url_err:
                reason = _sanitize_error_message(str(getattr(url_err, "reason", url_err)), api_key)
                last_error = f"Network error calling {model_name}: {reason}"
                break
            except Exception as exc:
                sanitized = _sanitize_error_message(str(exc), api_key)
                last_error = f"Error calling {model_name} ({type(exc).__name__}: {sanitized})"
                break

        return None, last_error

    def simulate_offline_response(
        self,
        snapshot: MarketSnapshot,
        state: AgentLedgerState,
        payload: Dict[str, Any],
    ) -> str:
        """Produce a persona-faithful JSON response string for pre-launch historical dry-run mode."""
        raise NotImplementedError

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        """Evaluate market snapshot and agent state to return a validated OrderSignal."""
        try:
            if state.status != "ACTIVE":
                return self.hold_signal(
                    snapshot,
                    reasoning=f"Agent status is {state.status} ({state.status_reason}); holding.",
                    confidence=0.5,
                )

            payload = build_shared_llm_snapshot_payload(snapshot, state)
            api_key = self._get_api_key()

            if not api_key:
                if self.allow_offline_simulation:
                    simulated_json = self.simulate_offline_response(snapshot, state, payload)
                    return parse_and_validate_llm_json(
                        raw_text=simulated_json,
                        agent_id=self.agent_id,
                        snapshot=snapshot,
                        state=state,
                    )
                return self.hold_signal(
                    snapshot,
                    reasoning="HOLD fallback: GEMINI_API_KEY is not configured in live competition mode.",
                    confidence=0.5,
                )

            prompt_text = self.build_prompt(payload)
            raw_text, error_reason = self._call_gemini_api(
                prompt_text=prompt_text,
                api_key=api_key,
                decision_ts=snapshot.decision_timestamp,
            )
            if raw_text is None:
                return self.hold_signal(
                    snapshot,
                    reasoning=f"HOLD fallback: {error_reason or 'Gemini API unavailable'}.",
                    confidence=0.5,
                )

            return parse_and_validate_llm_json(
                raw_text=raw_text,
                agent_id=self.agent_id,
                snapshot=snapshot,
                state=state,
            )
        except Exception as exc:
            sanitized = _sanitize_error_message(str(exc), self._get_api_key())
            return self.hold_signal(
                snapshot,
                reasoning=f"HOLD fallback: Unexpected error in {self.agent_id} ({type(exc).__name__}: {sanitized}).",
                confidence=0.5,
            )


class TechnicalLLMAgent(BaseLLMAgent):
    """Agent 5: Technical Analyst (Gemini Flash) — price action, RSI, Bollinger, volume & S/R."""

    DEFAULT_AGENT_ID = "llm_technical"
    PERSONA_PROMPT = (
        "Focus strictly on technical structure: SMA(10/20/30) alignment, RSI(14) momentum or oversold "
        "confluence with 20-period Bollinger Bands, 20-bar high/low support & resistance, and volume_ratio. "
        "Require at least a 2:1 reward-to-risk ratio from entry to take_profit vs stop_loss."
    )

    def simulate_offline_response(
        self,
        snapshot: MarketSnapshot,
        state: AgentLedgerState,
        payload: Dict[str, Any],
    ) -> str:
        # 1. Inspect existing positions for technical breakdown or upper-band resistance harvest
        for ticker in sorted(state.positions.keys()):
            pos = state.positions[ticker]
            ind = snapshot.indicators.get(ticker)
            if ind is None:
                continue
            if ind.last_price < ind.sma_20 and ind.rsi_14 < 43.0:
                return json.dumps(
                    {
                        "action": "SELL",
                        "ticker": ticker,
                        "size": 0.15,
                        "stop_loss": None,
                        "take_profit": None,
                        "confidence": 0.76,
                        "reasoning": (
                            f"{ticker} broke below its 20-bar SMA (${ind.sma_20:.2f}) at ${ind.last_price:.2f} "
                            f"with weakening RSI(14) at {ind.rsi_14:.1f}; exiting to respect trend support."
                        ),
                    }
                )
            if pos.unrealized_pnl_pct >= 1.6 and (ind.rsi_14 >= 71.0 or ind.last_price >= ind.bb_upper_20):
                return json.dumps(
                    {
                        "action": "SELL",
                        "ticker": ticker,
                        "size": 0.15,
                        "stop_loss": None,
                        "take_profit": None,
                        "confidence": 0.80,
                        "reasoning": (
                            f"Locking in +{pos.unrealized_pnl_pct:.2f}% gain on {ticker} as price (${ind.last_price:.2f}) "
                            f"tests upper Bollinger resistance (${ind.bb_upper_20:.2f}) with RSI(14) at {ind.rsi_14:.1f}."
                        ),
                    }
                )

        # 2. Scan for high-confluence technical entries if capacity allows
        if len(state.positions) < MAX_OPEN_POSITIONS and not snapshot.is_near_market_close:
            best_candidate: Optional[Tuple[float, TickerIndicators, str]] = None
            candidate_tickers = sorted(set(LIQUID_LARGE_CAP_STOCKS + SECTOR_ETFS))

            for ticker in candidate_tickers:
                if ticker in state.positions:
                    continue
                ind = snapshot.indicators.get(ticker)
                if ind is None or ind.last_price <= 0 or ind.atr_14 <= 0:
                    continue

                # Setup A: Uptrend momentum breakout / continuation with volume confirmation
                if (
                    ind.sma_10 > ind.sma_20 >= ind.sma_30 * 0.998
                    and 50.0 <= ind.rsi_14 <= 67.5
                    and ind.volume_ratio >= 1.02
                    and ind.last_price >= ind.sma_10
                ):
                    score = (
                        (ind.volume_ratio * 2.0)
                        + ind.relative_strength_vs_spy
                        + ((ind.last_price - ind.sma_20) / max(ind.atr_14, 0.01))
                    )
                    if best_candidate is None or score > best_candidate[0]:
                        best_candidate = (score, ind, "breakout")

                # Setup B: Bollinger lower-band oversold bounce within broader structural support
                elif (
                    ind.rsi_14 <= 36.0
                    and ind.last_price <= ind.bb_lower_20 * 1.008
                    and ind.sma_20 >= ind.sma_30 * 0.985
                ):
                    score = (40.0 - ind.rsi_14) * 0.4 + ind.volume_ratio
                    if best_candidate is None or score > best_candidate[0]:
                        best_candidate = (score, ind, "reversion_bounce")

            if best_candidate is not None:
                _, ind, setup_kind = best_candidate
                price = ind.last_price
                risk_dist = max(1.6 * ind.atr_14, price * 0.01)
                stop_loss = round(price - risk_dist, 2)
                take_profit = round(price + 2.3 * risk_dist, 2)

                if setup_kind == "breakout":
                    reason = (
                        f"{ind.ticker} exhibits bullish SMA(10/20/30) alignment (${ind.sma_10:.2f} > ${ind.sma_20:.2f} > ${ind.sma_30:.2f}) "
                        f"with RSI(14) at {ind.rsi_14:.1f} and {ind.volume_ratio:.2f}x volume; buying with 2.3:1 R/R (stop ${stop_loss:.2f}, target ${take_profit:.2f})."
                    )
                else:
                    reason = (
                        f"{ind.ticker} is testing lower Bollinger Band support (${ind.bb_lower_20:.2f}) with oversold RSI(14)={ind.rsi_14:.1f}; "
                        f"entering mean-reversion bounce with tight stop at ${stop_loss:.2f} and 2.3:1 target at ${take_profit:.2f}."
                    )

                return json.dumps(
                    {
                        "action": "BUY",
                        "ticker": ind.ticker,
                        "size": 0.16,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "confidence": 0.78,
                        "reasoning": reason,
                    }
                )

        return json.dumps(
            {
                "action": "HOLD",
                "ticker": "",
                "size": 0.0,
                "stop_loss": None,
                "take_profit": None,
                "confidence": 0.65,
                "reasoning": (
                    "No unheld ticker currently satisfies strict CMT trend/Bollinger confluence with >=2:1 reward-to-risk; "
                    "maintaining existing technical stops."
                ),
            }
        )


class CatalystLLMAgent(BaseLLMAgent):
    """Agent 6: News & Catalyst Trader (Gemini Flash) — headlines, earnings catalysts & volume reaction."""

    DEFAULT_AGENT_ID = "llm_catalyst"
    PERSONA_PROMPT = (
        "Focus on event-driven catalysts from recent_news confirmed by price and volume_ratio reaction. "
        "Buy fresh positive news catalysts with momentum confirmation; exit when news turns negative or catalyst momentum fades."
    )

    def _score_news_by_ticker(self, snapshot: MarketSnapshot) -> Dict[str, Tuple[int, str, str]]:
        """Return mapping of ticker -> (sentiment_score, best_headline, source)."""
        scores: Dict[str, Tuple[int, str, str]] = {}
        for item in snapshot.news:
            text_lower = f"{item.headline} {item.summary}".lower()
            pos_hits = sum(1 for kw in _POSITIVE_NEWS_KEYWORDS if kw in text_lower)
            neg_hits = sum(1 for kw in _NEGATIVE_NEWS_KEYWORDS if kw in text_lower)
            delta = pos_hits - neg_hits
            if delta == 0:
                delta = 1  # Fresh coverage in active catalyst feed is mildly constructive if price confirms

            for sym in item.symbols:
                ticker = sym.strip().upper()
                if ticker not in UNIVERSE_SET:
                    continue
                prev_score, prev_hl, prev_src = scores.get(ticker, (0, item.headline, item.source))
                new_score = prev_score + delta
                best_hl = item.headline if abs(delta) >= abs(prev_score) else prev_hl
                scores[ticker] = (new_score, best_hl, item.source or prev_src)
        return scores

    def simulate_offline_response(
        self,
        snapshot: MarketSnapshot,
        state: AgentLedgerState,
        payload: Dict[str, Any],
    ) -> str:
        news_map = self._score_news_by_ticker(snapshot)

        # 1. Check held positions for negative news catalyst or post-catalyst exhaustion
        for ticker in sorted(state.positions.keys()):
            pos = state.positions[ticker]
            ind = snapshot.indicators.get(ticker)
            if ind is None:
                continue
            sentiment, headline, _ = news_map.get(ticker, (0, "", ""))
            if sentiment < 0 and ind.change_pct_1d < 0:
                return json.dumps(
                    {
                        "action": "SELL",
                        "ticker": ticker,
                        "size": 0.15,
                        "stop_loss": None,
                        "take_profit": None,
                        "confidence": 0.81,
                        "reasoning": (
                            f"Exiting {ticker} after adverse headline ('{headline[:90]}') coincided with "
                            f"intraday weakness ({ind.change_pct_1d:+.2f}%)."
                        ),
                    }
                )
            if pos.unrealized_pnl_pct >= 2.0 and (ind.rsi_14 >= 69.0 or ind.change_pct_1d < -0.3):
                return json.dumps(
                    {
                        "action": "SELL",
                        "ticker": ticker,
                        "size": 0.15,
                        "stop_loss": None,
                        "take_profit": None,
                        "confidence": 0.77,
                        "reasoning": (
                            f"Harvesting +{pos.unrealized_pnl_pct:.2f}% catalyst gain on {ticker} as post-news "
                            f"momentum stabilizes at RSI(14)={ind.rsi_14:.1f}."
                        ),
                    }
                )

        # 2. Look for positive news catalyst confirmed by price & volume
        if len(state.positions) < MAX_OPEN_POSITIONS and not snapshot.is_near_market_close:
            best_news_pick: Optional[Tuple[float, TickerIndicators, str, str]] = None
            for ticker, (sentiment, headline, source) in sorted(news_map.items()):
                if ticker in state.positions or sentiment <= 0:
                    continue
                ind = snapshot.indicators.get(ticker)
                if ind is None or ind.last_price <= 0 or ind.atr_14 <= 0:
                    continue
                if ind.change_pct_1d >= 0.1 and 44.0 <= ind.rsi_14 <= 72.0:
                    rank_score = (
                        sentiment * 2.0
                        + ind.change_pct_1d
                        + ind.volume_ratio
                        + ind.relative_strength_vs_spy
                    )
                    if best_news_pick is None or rank_score > best_news_pick[0]:
                        best_news_pick = (rank_score, ind, headline, source)

            if best_news_pick is not None:
                _, ind, headline, source = best_news_pick
                price = ind.last_price
                risk_dist = max(1.6 * ind.atr_14, price * 0.011)
                stop_loss = round(price - risk_dist, 2)
                take_profit = round(price + 2.2 * risk_dist, 2)
                short_hl = headline[:85] + ("..." if len(headline) > 85 else "")
                return json.dumps(
                    {
                        "action": "BUY",
                        "ticker": ind.ticker,
                        "size": 0.15,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "confidence": 0.80,
                        "reasoning": (
                            f"Positive catalyst on {ind.ticker} ({source}: '{short_hl}') confirmed by "
                            f"{ind.change_pct_1d:+.2f}% price move and {ind.volume_ratio:.2f}x volume; stop ${stop_loss:.2f}, target ${take_profit:.2f}."
                        ),
                    }
                )

            # Secondary catalyst scan: unusual volume + relative strength breakout when headlines are quiet
            best_flow_pick: Optional[Tuple[float, TickerIndicators]] = None
            for ticker in LIQUID_LARGE_CAP_STOCKS:
                if ticker in state.positions:
                    continue
                ind = snapshot.indicators.get(ticker)
                if ind is None or ind.last_price <= 0 or ind.atr_14 <= 0:
                    continue
                if (
                    ind.volume_ratio >= 1.15
                    and ind.change_pct_1d >= 0.65
                    and ind.relative_strength_vs_spy >= 0.4
                    and 52.0 <= ind.rsi_14 <= 70.0
                ):
                    flow_score = ind.volume_ratio * 2.0 + ind.change_pct_1d + ind.relative_strength_vs_spy
                    if best_flow_pick is None or flow_score > best_flow_pick[0]:
                        best_flow_pick = (flow_score, ind)

            if best_flow_pick is not None:
                _, ind = best_flow_pick
                price = ind.last_price
                risk_dist = max(1.6 * ind.atr_14, price * 0.012)
                stop_loss = round(price - risk_dist, 2)
                take_profit = round(price + 2.2 * risk_dist, 2)
                return json.dumps(
                    {
                        "action": "BUY",
                        "ticker": ind.ticker,
                        "size": 0.14,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "confidence": 0.72,
                        "reasoning": (
                            f"{ind.ticker} shows institutional momentum flow ({ind.change_pct_1d:+.2f}% on {ind.volume_ratio:.2f}x volume, "
                            f"RS vs SPY {ind.relative_strength_vs_spy:+.2f}); entering with stop ${stop_loss:.2f} and target ${take_profit:.2f}."
                        ),
                    }
                )

        return json.dumps(
            {
                "action": "HOLD",
                "ticker": "",
                "size": 0.0,
                "stop_loss": None,
                "take_profit": None,
                "confidence": 0.64,
                "reasoning": (
                    "Awaiting fresh actionable company headlines or high-volume catalyst breakouts; "
                    "staying patient with current portfolio."
                ),
            }
        )


class MacroLLMAgent(BaseLLMAgent):
    """Agent 7: Macro & Sector Strategist (Gemini Flash) — SPY/QQQ/TLT/GLD regime & SPDR sector rotation."""

    DEFAULT_AGENT_ID = "llm_macro"
    PERSONA_PROMPT = (
        "First diagnose the top-down regime across SPY, QQQ, IWM, TLT, and GLD. "
        "In risk-on regimes, allocate to leading SPDR sector ETFs (XLK, XLF, XLI, XLC, XLY) and macro leaders with top relative_strength_vs_spy. "
        "In risk-off or rising-yield regimes, rotate into defensive assets (GLD, TLT, XLP, XLV, XLU) or hold cash."
    )

    def _diagnose_macro_regime(self, snapshot: MarketSnapshot) -> Tuple[str, str]:
        spy = snapshot.indicators.get("SPY")
        qqq = snapshot.indicators.get("QQQ")
        tlt = snapshot.indicators.get("TLT")
        gld = snapshot.indicators.get("GLD")

        spy_1d = spy.change_pct_1d if spy else 0.0
        qqq_1d = qqq.change_pct_1d if qqq else 0.0
        tlt_1d = tlt.change_pct_1d if tlt else 0.0
        gld_1d = gld.change_pct_1d if gld else 0.0
        spy_trend_up = bool(spy and spy.sma_10 >= spy.sma_20 * 0.998)

        if (spy_1d >= 0.0 and spy_trend_up) or (qqq_1d >= 0.25 and spy_1d > -0.2):
            regime = "RISK_ON"
            desc = f"Risk-On regime (SPY {spy_1d:+.2f}%, QQQ {qqq_1d:+.2f}%, TLT {tlt_1d:+.2f}%)"
        elif spy_1d <= -0.35 or (gld_1d > spy_1d + 0.3 and tlt_1d > spy_1d):
            regime = "RISK_OFF"
            desc = f"Defensive/Risk-Off regime (SPY {spy_1d:+.2f}%, GLD {gld_1d:+.2f}%, TLT {tlt_1d:+.2f}%)"
        else:
            regime = "NEUTRAL"
            desc = f"Balanced macro regime (SPY {spy_1d:+.2f}%, QQQ {qqq_1d:+.2f}%, GLD {gld_1d:+.2f}%)"
        return regime, desc

    def simulate_offline_response(
        self,
        snapshot: MarketSnapshot,
        state: AgentLedgerState,
        payload: Dict[str, Any],
    ) -> str:
        regime, regime_desc = self._diagnose_macro_regime(snapshot)
        defensive_basket = {"GLD", "TLT", "XLP", "XLV", "XLU", "XLE"}

        # 1. Rebalance out of laggards or regime-mismatched holdings
        for ticker in sorted(state.positions.keys()):
            pos = state.positions[ticker]
            ind = snapshot.indicators.get(ticker)
            if ind is None:
                continue
            if regime == "RISK_OFF" and ticker not in defensive_basket and ind.relative_strength_vs_spy < -0.3:
                return json.dumps(
                    {
                        "action": "SELL",
                        "ticker": ticker,
                        "size": 0.18,
                        "stop_loss": None,
                        "take_profit": None,
                        "confidence": 0.78,
                        "reasoning": (
                            f"{regime_desc}: rotating out of cyclical holding {ticker} as relative strength vs SPY "
                            f"weakens to {ind.relative_strength_vs_spy:+.2f}%."
                        ),
                    }
                )
            if pos.unrealized_pnl_pct >= 1.8 and ind.rsi_14 >= 69.0:
                return json.dumps(
                    {
                        "action": "SELL",
                        "ticker": ticker,
                        "size": 0.18,
                        "stop_loss": None,
                        "take_profit": None,
                        "confidence": 0.76,
                        "reasoning": (
                            f"{regime_desc}: locking in +{pos.unrealized_pnl_pct:.2f}% sector rotation gain on {ticker} "
                            f"with RSI(14) extended at {ind.rsi_14:.1f}."
                        ),
                    }
                )

        # 2. Select top macro / sector ETF or sector leader matching the diagnosed regime
        if len(state.positions) < MAX_OPEN_POSITIONS and not snapshot.is_near_market_close:
            if regime == "RISK_ON":
                target_pool = SECTOR_ETFS + ["QQQ", "SMH", "SPY", "NVDA", "MSFT", "AMZN", "META", "JPM", "LLY"]
            elif regime == "RISK_OFF":
                target_pool = ["GLD", "TLT", "XLP", "XLV", "XLU", "XLE", "WMT", "COST", "JNJ", "PG"]
            else:
                target_pool = SECTOR_ETFS + INDEX_AND_MACRO_ETFS

            best_macro: Optional[Tuple[float, TickerIndicators]] = None
            for ticker in target_pool:
                if ticker in state.positions:
                    continue
                ind = snapshot.indicators.get(ticker)
                if ind is None or ind.last_price <= 0 or ind.atr_14 <= 0:
                    continue
                if ind.sma_10 >= ind.sma_20 * 0.997 and 44.0 <= ind.rsi_14 <= 69.0:
                    macro_score = (
                        ind.relative_strength_vs_spy * 2.0
                        + ind.change_pct_5d * 0.6
                        + ind.change_pct_1d * 0.8
                    )
                    if macro_score > 0.05 and (best_macro is None or macro_score > best_macro[0]):
                        best_macro = (macro_score, ind)

            if best_macro is not None:
                _, ind = best_macro
                price = ind.last_price
                risk_dist = max(1.7 * ind.atr_14, price * 0.009)
                stop_loss = round(price - risk_dist, 2)
                take_profit = round(price + 2.2 * risk_dist, 2)
                return json.dumps(
                    {
                        "action": "BUY",
                        "ticker": ind.ticker,
                        "size": 0.18,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "confidence": 0.79,
                        "reasoning": (
                            f"{regime_desc}: allocating into {ind.ticker} on top-decile relative strength vs SPY "
                            f"({ind.relative_strength_vs_spy:+.2f}%) and 5d momentum ({ind.change_pct_5d:+.2f}%); stop ${stop_loss:.2f}, target ${take_profit:.2f}."
                        ),
                    }
                )

        return json.dumps(
            {
                "action": "HOLD",
                "ticker": "",
                "size": 0.0,
                "stop_loss": None,
                "take_profit": None,
                "confidence": 0.68,
                "reasoning": (
                    f"{regime_desc}: current sector ETF and macro allocations are aligned with cross-asset trend signals."
                ),
            }
        )


class CautiousLLMAgent(BaseLLMAgent):
    """Agent 8: Cautious Risk-First Trader (Gemini Flash) — high cash, tight stops, >=2.5:1 R/R."""

    DEFAULT_AGENT_ID = "llm_cautious"
    PERSONA_PROMPT = (
        "Capital preservation is your #1 priority. Hold at most 2 concurrent positions so the majority of capital "
        "remains in cash. Only enter A+ setups with low volatility, clear uptrend support, tight stop_loss, and "
        "at least a 2.5:1 reward-to-risk ratio."
    )

    def simulate_offline_response(
        self,
        snapshot: MarketSnapshot,
        state: AgentLedgerState,
        payload: Dict[str, Any],
    ) -> str:
        # 1. Strict capital protection on any open position
        for ticker in sorted(state.positions.keys()):
            pos = state.positions[ticker]
            ind = snapshot.indicators.get(ticker)
            if ind is None:
                continue
            if ind.last_price < ind.sma_20 or pos.unrealized_pnl_pct <= -0.75:
                return json.dumps(
                    {
                        "action": "SELL",
                        "ticker": ticker,
                        "size": 0.12,
                        "stop_loss": None,
                        "take_profit": None,
                        "confidence": 0.85,
                        "reasoning": (
                            f"Risk-first exit on {ticker} at ${ind.last_price:.2f} (unrealized {pos.unrealized_pnl_pct:+.2f}%) "
                            f"to preserve capital as price tests/loses 20-period SMA support (${ind.sma_20:.2f})."
                        ),
                    }
                )
            if pos.unrealized_pnl_pct >= 1.5 or ind.rsi_14 >= 66.0:
                return json.dumps(
                    {
                        "action": "SELL",
                        "ticker": ticker,
                        "size": 0.12,
                        "stop_loss": None,
                        "take_profit": None,
                        "confidence": 0.84,
                        "reasoning": (
                            f"Banking +{pos.unrealized_pnl_pct:.2f}% profit on {ticker} into strength (RSI {ind.rsi_14:.1f}) "
                            f"to return portfolio to a defensive cash posture."
                        ),
                    }
                )

        # 2. Only consider a new trade if open positions < 2, daily PnL > -1.0%, and an A+ asymmetric setup exists
        if (
            len(state.positions) < 2
            and state.daily_pnl_pct > -1.0
            and state.current_drawdown_pct > -3.0
            and not snapshot.is_near_market_close
        ):
            quality_watchlist = [
                "SPY", "QQQ", "XLK", "XLV", "XLP", "XLF", "GLD",
                "MSFT", "AAPL", "GOOGL", "AMZN", "JPM", "BRK.B", "V", "MA", "COST", "WMT", "LLY", "JNJ",
            ]
            best_a_plus: Optional[Tuple[float, TickerIndicators]] = None
            for ticker in quality_watchlist:
                if ticker in state.positions:
                    continue
                ind = snapshot.indicators.get(ticker)
                if ind is None or ind.last_price <= 0 or ind.atr_14 <= 0:
                    continue
                atr_pct = ind.atr_14 / ind.last_price
                # Require steady uptrend, low volatility, non-overbought RSI, and positive relative strength
                if (
                    ind.sma_10 > ind.sma_20 > ind.sma_30
                    and atr_pct <= 0.018
                    and 44.0 <= ind.rsi_14 <= 59.0
                    and ind.relative_strength_vs_spy >= 0.05
                    and ind.last_price >= ind.sma_20
                ):
                    asymmetry_score = (
                        ind.relative_strength_vs_spy * 1.5
                        + (0.02 - atr_pct) * 100.0
                        + (60.0 - abs(ind.rsi_14 - 51.0)) * 0.05
                    )
                    if best_a_plus is None or asymmetry_score > best_a_plus[0]:
                        best_a_plus = (asymmetry_score, ind)

            if best_a_plus is not None:
                _, ind = best_a_plus
                price = ind.last_price
                risk_dist = max(1.25 * ind.atr_14, price * 0.0075)
                stop_loss = round(price - risk_dist, 2)
                take_profit = round(price + 2.8 * risk_dist, 2)  # 2.8:1 reward-to-risk (>= 2.5:1 requirement)
                return json.dumps(
                    {
                        "action": "BUY",
                        "ticker": ind.ticker,
                        "size": 0.12,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "confidence": 0.86,
                        "reasoning": (
                            f"A+ asymmetric setup on {ind.ticker}: low volatility (ATR {ind.atr_14:.2f}), clean SMA(10>20>30) trend, "
                            f"and RSI {ind.rsi_14:.1f} allow a tight stop at ${stop_loss:.2f} with a 2.8:1 reward-to-risk target at ${take_profit:.2f}."
                        ),
                    }
                )

        cash_pct = (state.cash / state.equity * 100.0) if state.equity > 0 else 100.0
        return json.dumps(
            {
                "action": "HOLD",
                "ticker": "",
                "size": 0.0,
                "stop_loss": None,
                "take_profit": None,
                "confidence": 0.82,
                "reasoning": (
                    f"Capital preservation priority: holding {cash_pct:.1f}% cash and waiting for a low-volatility setup "
                    f"with >=2.5:1 reward-to-risk asymmetry."
                ),
            }
        )


# Aliases for convenience across modules
LLMAgent = BaseLLMAgent
LLMTechnicalAgent = TechnicalLLMAgent
LLMCatalystAgent = CatalystLLMAgent
LLMMacroAgent = MacroLLMAgent
LLMCautiousAgent = CautiousLLMAgent

LLM_AGENT_CLASSES: Dict[str, Type[BaseLLMAgent]] = {
    "llm_technical": TechnicalLLMAgent,
    "llm_catalyst": CatalystLLMAgent,
    "llm_macro": MacroLLMAgent,
    "llm_cautious": CautiousLLMAgent,
}


def create_llm_agent(
    agent_id: str,
    allow_offline_simulation: Optional[bool] = None,
    api_key: Optional[str] = None,
    polite_pause_seconds: float = DEFAULT_POLITE_PAUSE_SECONDS,
) -> BaseLLMAgent:
    """Instantiate one of the 4 Gemini Flash LLM agents by agent_id."""
    if agent_id not in LLM_AGENT_CLASSES:
        raise ValueError(
            f"Unknown LLM agent_id '{agent_id}'. Expected one of {sorted(LLM_AGENT_CLASSES.keys())}."
        )
    cls = LLM_AGENT_CLASSES[agent_id]
    spec = AGENT_SPECS_BY_ID[agent_id]
    return cls(
        spec=spec,
        allow_offline_simulation=allow_offline_simulation,
        api_key=api_key,
        polite_pause_seconds=polite_pause_seconds,
    )


def build_llm_agents(
    allow_offline_simulation: Optional[bool] = None,
    api_key: Optional[str] = None,
    polite_pause_seconds: float = DEFAULT_POLITE_PAUSE_SECONDS,
) -> Dict[str, BaseLLMAgent]:
    """Instantiate all 4 Gemini Flash LLM agents keyed by agent_id."""
    return {
        agent_id: create_llm_agent(
            agent_id=agent_id,
            allow_offline_simulation=allow_offline_simulation,
            api_key=api_key,
            polite_pause_seconds=polite_pause_seconds,
        )
        for agent_id in ("llm_technical", "llm_catalyst", "llm_macro", "llm_cautious")
    }
