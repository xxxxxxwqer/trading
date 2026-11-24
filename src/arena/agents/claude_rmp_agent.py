"""Agent 14 — Claude Residual-Momentum Pullback (`claude_rmp`).

Purely deterministic, rule-based coded strategy designed by Claude prior to competition launch.
No runtime LLM calls, no randomness, no look-ahead, and code + `claude_rmp_params.json` are
SHA-256 locked before Day 1.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import (
    AGENT_SPECS_BY_ID,
    COMPETITION_TRADING_DAYS,
    DEFAULT_SPREAD_BPS,
    LIQUID_LARGE_CAP_STOCKS,
    PROJECT_ROOT,
    SLIPPAGE_RATE,
    STATE_DIR,
    AgentSpec,
)
from ..data.alpaca_client import parse_iso_utc, to_et_datetime
from ..models import AgentLedgerState, Bar, MarketSnapshot, OrderSignal, Position
from .base import BaseAgent

CLAUDE_RMP_ID = "claude_rmp"
PARAMS_PATH = Path(__file__).resolve().parent / "claude_rmp_params.json"
UNIVERSE_FUNDAMENTALS_PATH = PROJECT_ROOT / "data_cache" / "universe_fundamentals.json"
STATE_FILENAME = "claude_rmp_state.json"
CYCLE_LOGS_FILENAME = "claude_rmp_cycle_logs.json"


# ---------------------------------------------------------------------------
# Pure Mathematical Indicators (Exact Spec Section 2 & Section 5)
# ---------------------------------------------------------------------------

def load_claude_rmp_params(params_path: Path = PARAMS_PATH) -> Dict[str, Any]:
    with Path(params_path).open("r", encoding="utf-8") as f:
        return json.load(f)


def compute_sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / float(period)


def compute_wilder_atr(bars: List[Bar], period: int = 14) -> Optional[float]:
    """Wilder ATR(14) on completed daily bars."""
    if len(bars) < period + 1:
        return None
    trs: List[float] = []
    for i in range(1, len(bars)):
        hi = float(bars[i].high)
        lo = float(bars[i].low)
        prev_c = float(bars[i - 1].close)
        tr = max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))
        trs.append(tr)
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / float(period)
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / float(period)
    return atr


def compute_wilder_rsi(closes: List[float], period: int = 2) -> Optional[float]:
    """Wilder RSI(period) on daily closes (caller appends current intraday price P as provisional today close)."""
    if len(closes) < period + 1:
        return None
    deltas = [float(closes[i]) - float(closes[i - 1]) for i in range(1, len(closes))]
    if len(deltas) < period:
        return None
    avg_gain = sum(max(d, 0.0) for d in deltas[:period]) / float(period)
    avg_loss = sum(max(-d, 0.0) for d in deltas[:period]) / float(period)
    for d in deltas[period:]:
        gain = max(d, 0.0)
        loss = max(-d, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / float(period)
        avg_loss = (avg_loss * (period - 1) + loss) / float(period)
    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_log_returns(closes: List[float]) -> List[float]:
    rets: List[float] = []
    for i in range(1, len(closes)):
        c0 = float(closes[i - 1])
        c1 = float(closes[i])
        if c0 > 0 and c1 > 0:
            rets.append(math.log(c1 / c0))
        else:
            rets.append(0.0)
    return rets


def compute_realized_vol_annualized(closes: List[float], n_returns: int) -> Optional[float]:
    """RV(n) = sample std of last `n_returns` daily log returns * sqrt(252)."""
    rets = compute_log_returns(closes)
    if len(rets) < n_returns or n_returns < 2:
        return None
    window = rets[-n_returns:]
    mean_r = sum(window) / float(n_returns)
    var = sum((r - mean_r) ** 2 for r in window) / float(n_returns - 1)
    return math.sqrt(max(0.0, var)) * math.sqrt(252.0)


def compute_residual_momentum_score(
    stock_closes: List[float],
    spy_closes: List[float],
    beta_lookback: int = 63,
    residual_window: int = 58,
    skip_recent: int = 5,
) -> Optional[Tuple[float, float]]:
    """Spec Section 5:
    1. beta_i = Cov(r_i, r_m) / Var(r_m) over 63 daily log returns
    2. eps_{i,t} = r_{i,t} - beta_i * r_{m,t}  (NO intercept, preserving alpha in residual)
    3. W = t-63 ... t-6 (58 days, skipping most recent 5 trading days)
    4. Score_i = sum_W(eps_i) / (std_W(eps_i) * sqrt(58))
    Returns (score, beta) or None if insufficient data.
    """
    r_i_all = compute_log_returns(stock_closes)
    r_m_all = compute_log_returns(spy_closes)
    n_common = min(len(r_i_all), len(r_m_all))
    if n_common < beta_lookback or (residual_window + skip_recent) > beta_lookback:
        return None

    r_i = r_i_all[-beta_lookback:]
    r_m = r_m_all[-beta_lookback:]

    mean_i = sum(r_i) / float(beta_lookback)
    mean_m = sum(r_m) / float(beta_lookback)
    var_m = sum((rm - mean_m) ** 2 for rm in r_m)
    if var_m <= 1e-12:
        return None
    cov_im = sum((ri - mean_i) * (rm - mean_m) for ri, rm in zip(r_i, r_m))
    beta = cov_im / var_m

    # Residuals WITHOUT intercept
    eps = [ri - beta * rm for ri, rm in zip(r_i, r_m)]
    # Window W = t-63 ... t-6 (skip most recent `skip_recent` days)
    eps_w = eps[-(residual_window + skip_recent) : len(eps) - skip_recent] if skip_recent > 0 else eps[-residual_window:]
    if len(eps_w) != residual_window:
        return None

    sum_eps = sum(eps_w)
    mean_eps = sum_eps / float(residual_window)
    var_eps = sum((e - mean_eps) ** 2 for e in eps_w) / float(residual_window - 1)
    std_eps = math.sqrt(max(0.0, var_eps))
    if std_eps <= 1e-12:
        return None

    score = sum_eps / (std_eps * math.sqrt(float(residual_window)))
    return score, beta


def compute_daily_regime_and_leaders(
    daily_bars_by_ticker: Dict[str, List[Bar]],
    params: Dict[str, Any],
    eligible_stocks: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compute once-per-day Regime Gate (Section 4), Leader Top 10 (Section 5), and per-stock
    daily indicators (ATR14, SMA50, H10, prev_close, RV63) strictly from completed prior daily bars."""
    stocks = list(eligible_stocks) if eligible_stocks is not None else list(LIQUID_LARGE_CAP_STOCKS)
    min_bars = int(params.get("min_daily_bars", 70))

    spy_bars = daily_bars_by_ticker.get("SPY") or []
    if len(spy_bars) < min_bars:
        return {
            "valid": False,
            "reason": f"Insufficient SPY completed daily bars ({len(spy_bars)} < {min_bars})",
            "regime_score": 0,
            "slots": 0,
            "regime_flags": {"C1": False, "C2": False, "C3": False, "C4": False},
            "spy_prev_close": 0.0,
            "leaders_top10": [],
            "daily_stats": {},
        }

    spy_closes = [float(b.close) for b in spy_bars]
    spy_prev_close = spy_closes[-1]
    spy_sma50 = compute_sma(spy_closes, int(params["regime"]["spy_sma_long"])) or 0.0
    sma20_p = int(params["regime"]["spy_sma_short"])
    slope_lb = int(params["regime"]["spy_sma_short_slope_lookback"])
    spy_sma20_now = compute_sma(spy_closes, sma20_p) or 0.0
    spy_sma20_prev = compute_sma(spy_closes[:-slope_lb], sma20_p) if len(spy_closes) > sma20_p + slope_lb else None
    spy_rv10 = compute_realized_vol_annualized(spy_closes, int(params["regime"]["rv_short_days"]))
    spy_rv60 = compute_realized_vol_annualized(spy_closes, int(params["regime"]["rv_long_days"]))

    # Per-stock completed daily stats & breadth (C3)
    daily_stats: Dict[str, Dict[str, Any]] = {}
    above_sma50_count = 0
    total_with_sma50 = 0

    lb_cfg = params["leader_score"]
    h10_lb = int(params["entry"]["h10_lookback_days"])
    sma_trend_p = int(lb_cfg["sma_trend_days"])
    sma_slope_lb = int(lb_cfg["sma_slope_lookback"])
    max_rv63 = float(lb_cfg["max_rv63_annualized"])
    min_score = float(lb_cfg["min_score"])

    eligible_scored: List[Tuple[float, str, float]] = []

    for sym in sorted(stocks):
        s_bars = daily_bars_by_ticker.get(sym) or []
        if len(s_bars) < min_bars:
            continue
        closes = [float(b.close) for b in s_bars]
        prev_close = closes[-1]
        sma50_now = compute_sma(closes, sma_trend_p)
        sma50_prev = compute_sma(closes[:-sma_slope_lb], sma_trend_p) if len(closes) > sma_trend_p + sma_slope_lb else None
        atr14 = compute_wilder_atr(s_bars, 14)
        rv63 = compute_realized_vol_annualized(closes, int(lb_cfg["beta_lookback_days"]))
        h10 = max(closes[-h10_lb:]) if len(closes) >= h10_lb else prev_close

        if sma50_now is None or atr14 is None or atr14 <= 0 or rv63 is None:
            continue

        total_with_sma50 += 1
        if prev_close > sma50_now:
            above_sma50_count += 1

        rm_res = compute_residual_momentum_score(
            stock_closes=closes,
            spy_closes=spy_closes,
            beta_lookback=int(lb_cfg["beta_lookback_days"]),
            residual_window=int(lb_cfg["residual_window_days"]),
            skip_recent=int(lb_cfg["skip_recent_days"]),
        )
        score = rm_res[0] if rm_res is not None else None
        beta = rm_res[1] if rm_res is not None else None

        is_eligible_leader = (
            prev_close > sma50_now
            and sma50_prev is not None
            and sma50_now > sma50_prev
            and rv63 <= max_rv63
            and score is not None
            and score > min_score
        )
        if is_eligible_leader and score is not None and beta is not None:
            eligible_scored.append((score, sym, beta))

        daily_stats[sym] = {
            "prev_close": round(prev_close, 4),
            "sma50": round(sma50_now, 4),
            "sma50_10d_ago": round(sma50_prev, 4) if sma50_prev is not None else None,
            "atr14": round(atr14, 4),
            "h10": round(h10, 4),
            "rv63": round(rv63, 4),
            "score": round(score, 4) if score is not None else None,
            "beta": round(beta, 4) if beta is not None else None,
            "eligible_leader": is_eligible_leader,
            "closes_tail": [round(c, 4) for c in closes[-25:]],  # For Wilder RSI(2) with live P appended
        }

    breadth_ratio = (above_sma50_count / float(total_with_sma50)) if total_with_sma50 > 0 else 0.0

    c1 = spy_prev_close > spy_sma50
    c2 = (spy_sma20_prev is not None) and (spy_sma20_now > spy_sma20_prev)
    c3 = breadth_ratio >= float(params["regime"]["breadth_min_ratio"])
    c4 = (
        spy_rv10 is not None
        and spy_rv60 is not None
        and spy_rv60 > 0
        and spy_rv10 <= float(params["regime"]["rv_ratio_max"]) * spy_rv60
    )
    regime_score = int(c1) + int(c2) + int(c3) + int(c4)
    slots = int(params["regime"]["slots_by_score"].get(str(regime_score), 0))

    # Deterministic tie-break: Score descending, ticker ascending
    eligible_scored.sort(key=lambda x: (-x[0], x[1]))
    top_n = int(lb_cfg["top_n"])
    leaders_top10 = [
        {"ticker": sym, "score": round(sc, 4), "beta": round(bt, 3)}
        for sc, sym, bt in eligible_scored[:top_n]
    ]

    return {
        "valid": True,
        "regime_score": regime_score,
        "slots": slots,
        "regime_flags": {"C1": c1, "C2": c2, "C3": c3, "C4": c4},
        "breadth_ratio": round(breadth_ratio, 4),
        "spy_prev_close": round(spy_prev_close, 4),
        "spy_sma50": round(spy_sma50, 4),
        "spy_sma20": round(spy_sma20_now, 4),
        "spy_rv10": round(spy_rv10, 4) if spy_rv10 is not None else None,
        "spy_rv60": round(spy_rv60, 4) if spy_rv60 is not None else None,
        "leaders_top10": leaders_top10,
        "daily_stats": daily_stats,
    }


def _parse_hhmm_minutes(hhmm: str, default_min: int) -> int:
    try:
        h, m = (int(x) for x in str(hhmm).strip().split(":")[:2])
        return h * 60 + m
    except Exception:
        return default_min


class ClaudeRMPAgent(BaseAgent):
    """Agent 14 — Claude Residual-Momentum Pullback (`claude_rmp`)."""

    DEFAULT_AGENT_ID = CLAUDE_RMP_ID

    def __init__(
        self,
        spec: Optional[AgentSpec] = None,
        state_dir: Path = STATE_DIR,
        params_path: Path = PARAMS_PATH,
        universe_fundamentals_path: Path = UNIVERSE_FUNDAMENTALS_PATH,
    ) -> None:
        super().__init__(spec)
        self.state_dir = Path(state_dir)
        self.params_path = Path(params_path)
        self.params = load_claude_rmp_params(self.params_path)
        self.universe_fundamentals_path = Path(universe_fundamentals_path)
        self.rmp_state_path = self.state_dir / STATE_FILENAME
        self.cycle_logs_path = self.state_dir / CYCLE_LOGS_FILENAME
        self._sector_map: Optional[Dict[str, str]] = None

    def get_effective_stricter_limits(self) -> Dict[str, Any]:
        sr = self.params["sizing_and_risk"]
        return {
            "max_risk_per_trade_pct": float(sr["target_risk_per_trade_pct"]),  # 0.75% <= 1.0%
            "max_position_equity_pct": float(sr["max_position_equity_pct"]),   # 20.0%
            "max_open_positions": 5,
            "daily_loss_limit_pct": float(sr["daily_loss_halt_pct"]),          # -2.0% stricter than -3.0%
        }

    def _get_sector(self, ticker: str) -> str:
        if self._sector_map is None:
            self._sector_map = {}
            if self.universe_fundamentals_path.exists():
                try:
                    data = json.loads(self.universe_fundamentals_path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for sym, rec in data.items():
                            if isinstance(rec, dict) and rec.get("sector"):
                                self._sector_map[sym.upper()] = str(rec["sector"])
                except Exception:
                    pass
        return self._sector_map.get(ticker.upper(), "Unknown")

    def load_rmp_state(self) -> Dict[str, Any]:
        if self.rmp_state_path.exists():
            try:
                data = json.loads(self.rmp_state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {
            "trading_dates_seen": [],
            "daily_cache_date": "",
            "daily_cache": {},
            "entries_today_date": "",
            "entries_today_count": 0,
            "first_cycle_done_date": "",
            "position_meta": {},       # ticker -> {entry_price, initial_stop, initial_r, current_stop, highest_price, entry_date, entry_day_idx}
            "cooldown_until_idx": {},  # ticker -> trading_day_idx (inclusive block until > idx)
            "news_veto_until_idx": {}, # ticker -> trading_day_idx (2 trading day block)
            "regime_exposure_log": [], # [{date, spy_up, exposure_pct}] for H2 evaluation
        }

    def save_rmp_state(self, rmp_state: Dict[str, Any]) -> None:
        self.rmp_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.rmp_state_path.write_text(json.dumps(rmp_state, indent=2), encoding="utf-8")

    def append_cycle_log(self, log_entry: Dict[str, Any]) -> None:
        logs: List[Dict[str, Any]] = []
        if self.cycle_logs_path.exists():
            try:
                loaded = json.loads(self.cycle_logs_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    logs = loaded
            except Exception:
                logs = []
        logs.append(log_entry)
        # Keep most recent 200 cycle logs
        if len(logs) > 200:
            logs = logs[-200:]
        self.cycle_logs_path.parent.mkdir(parents=True, exist_ok=True)
        self.cycle_logs_path.write_text(json.dumps(logs, indent=2), encoding="utf-8")

    def reset_internal_state(self) -> None:
        if self.rmp_state_path.exists():
            self.rmp_state_path.unlink()
        if self.cycle_logs_path.exists():
            self.cycle_logs_path.unlink()

    def _sync_position_meta(
        self,
        rmp_state: Dict[str, Any],
        ledger_state: AgentLedgerState,
        day_idx: int,
        trading_date_et: str,
    ) -> None:
        """Keep `position_meta` synchronized with actual open positions in `ledger_state`.
        Detects any intrabar STOP_LOSS fills executed by FillSimulator and applies the 2-day cooldown."""
        pos_meta: Dict[str, Any] = rmp_state.setdefault("position_meta", {})
        cooldowns: Dict[str, int] = rmp_state.setdefault("cooldown_until_idx", {})
        cd_days = int(self.params["sizing_and_risk"]["stopout_cooldown_trading_days"])

        # Detect closed positions since last cycle
        for ticker in list(pos_meta.keys()):
            if ticker not in ledger_state.positions or ledger_state.positions[ticker].shares <= 0:
                # Check if closed via STOP_LOSS to trigger 2-trading-day cooldown
                recent_sells = [t for t in reversed(ledger_state.trades) if t.side == "SELL" and t.ticker == ticker]
                if recent_sells and recent_sells[0].exit_reason in ("STOP_LOSS", "EXIT_STOP"):
                    cooldowns[ticker] = max(cooldowns.get(ticker, -1), day_idx + cd_days)
                del pos_meta[ticker]

        # Register any newly filled open positions
        for ticker, pos in ledger_state.positions.items():
            if pos.shares <= 0:
                continue
            if ticker not in pos_meta:
                init_stop = float(pos.stop_loss) if pos.stop_loss > 0 else round(pos.avg_entry_price * 0.95, 4)
                init_r = max(0.01, float(pos.avg_entry_price) - init_stop)
                pos_meta[ticker] = {
                    "entry_price": round(float(pos.avg_entry_price), 4),
                    "initial_stop": round(init_stop, 4),
                    "initial_r": round(init_r, 4),
                    "current_stop": round(init_stop, 4),
                    "highest_price": round(max(float(pos.avg_entry_price), float(pos.current_price)), 4),
                    "entry_date": pos.opened_at[:10] if pos.opened_at else trading_date_et,
                    "entry_day_idx": day_idx,
                }

    def _update_news_vetos(
        self,
        rmp_state: Dict[str, Any],
        snapshot: MarketSnapshot,
        day_idx: int,
    ) -> None:
        """Scan last 24h Alpaca headlines for fixed veto keywords -> exclude ticker for 2 trading days."""
        vetos: Dict[str, int] = rmp_state.setdefault("news_veto_until_idx", {})
        keywords = [k.lower() for k in self.params["entry"]["news_veto_keywords"]]
        veto_hours = float(self.params["entry"]["news_veto_hours"])
        veto_days = int(self.params["entry"]["news_veto_cooldown_trading_days"])
        now_dt = parse_iso_utc(snapshot.decision_timestamp)
        cutoff_dt = now_dt - timedelta(hours=veto_hours)

        for item in snapshot.news or []:
            try:
                created_dt = parse_iso_utc(item.created_at)
            except Exception:
                continue
            if created_dt < cutoff_dt or created_dt > now_dt:
                continue
            blob = f"{item.headline} {item.summary}".lower()
            if any(kw in blob for kw in keywords):
                for sym in item.symbols or []:
                    s = str(sym).strip().upper()
                    if s:
                        vetos[s] = max(vetos.get(s, -1), day_idx + veto_days)

    def _compute_portfolio_heat(
        self,
        ledger_state: AgentLedgerState,
        rmp_state: Dict[str, Any],
        snapshot: MarketSnapshot,
    ) -> float:
        """Heat = sum max(0, (P - stop) * qty) across open positions."""
        pos_meta = rmp_state.get("position_meta") or {}
        heat = 0.0
        for sym, pos in ledger_state.positions.items():
            if pos.shares <= 0:
                continue
            ind = snapshot.indicators.get(sym)
            p_now = float(ind.last_price) if (ind and ind.last_price > 0) else float(pos.current_price)
            meta = pos_meta.get(sym) or {}
            stop_px = float(meta.get("current_stop", pos.stop_loss))
            heat += max(0.0, (p_now - stop_px) * float(pos.shares))
        return round(heat, 2)

    def _is_in_entry_time_window(self, snapshot: MarketSnapshot) -> bool:
        """Spec Section 6: open + 30min to close - 30min (handles early-close sessions)."""
        dt_et = to_et_datetime(snapshot.decision_timestamp)
        cur_min = dt_et.hour * 60 + dt_et.minute
        open_min = _parse_hhmm_minutes(snapshot.session_open_et, 9 * 60 + 30)
        close_min = _parse_hhmm_minutes(snapshot.session_close_et, 16 * 60)
        buf_open = int(self.params["entry"]["open_buffer_minutes"])
        buf_close = int(self.params["entry"]["close_buffer_minutes"])
        return (open_min + buf_open) <= cur_min <= (close_min - buf_close)

    def _get_today_intraday_bar_stats(
        self,
        snapshot: MarketSnapshot,
        ticker: str,
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Return (today_low, today_high, prev_30m_close) from intraday 30-min bars for `trading_date_et`."""
        bars = snapshot.bars_by_ticker.get(ticker) or []
        today_bars = [
            b for b in bars
            if to_et_datetime(b.timestamp).strftime("%Y-%m-%d") == snapshot.trading_date_et
        ]
        if not today_bars:
            if len(bars) >= 2:
                return float(bars[-1].low), float(bars[-1].high), float(bars[-2].close)
            if bars:
                return float(bars[-1].low), float(bars[-1].high), float(bars[-1].open)
            return None, None, None

        today_low = min(float(b.low) for b in today_bars)
        today_high = max(float(b.high) for b in today_bars)
        if len(today_bars) >= 2:
            prev_30m_close = float(today_bars[-2].close)
        elif len(bars) >= 2:
            prev_30m_close = float(bars[-2].close)
        else:
            prev_30m_close = float(today_bars[0].open)
        return today_low, today_high, prev_30m_close

    # ------------------------------------------------------------------
    # Multi-Order Batch Decision Engine (Spec Sections 4, 5, 6, 7, 8, 9)
    # ------------------------------------------------------------------

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        """Evaluate exits (priority 1->5) and up to 2 new pullback entries per cycle."""
        rmp_state = self.load_rmp_state()
        tdate = snapshot.trading_date_et

        # Track unique trading dates seen (monotonic index for exact trading-day counts & cooldowns)
        dates_seen: List[str] = rmp_state.setdefault("trading_dates_seen", [])
        if tdate and (not dates_seen or dates_seen[-1] != tdate):
            if tdate not in dates_seen:
                dates_seen.append(tdate)
        day_idx = max(0, len(dates_seen) - 1)

        is_first_cycle_today = rmp_state.get("first_cycle_done_date") != tdate
        if rmp_state.get("entries_today_date") != tdate:
            rmp_state["entries_today_date"] = tdate
            rmp_state["entries_today_count"] = 0

        # Sync position metadata & update 24h news veto list
        self._sync_position_meta(rmp_state, state, day_idx, tdate)
        self._update_news_vetos(rmp_state, snapshot, day_idx)

        # Compute & cache daily Regime + Leader Top 10 + ATR/SMA ONCE per trading day (first cycle)
        if rmp_state.get("daily_cache_date") != tdate or not rmp_state.get("daily_cache", {}).get("valid"):
            daily_cache = compute_daily_regime_and_leaders(
                daily_bars_by_ticker=snapshot.daily_bars_by_ticker,
                params=self.params,
                eligible_stocks=LIQUID_LARGE_CAP_STOCKS,
            )
            rmp_state["daily_cache_date"] = tdate
            rmp_state["daily_cache"] = daily_cache
        else:
            daily_cache = rmp_state["daily_cache"]

        if not daily_cache.get("valid"):
            rmp_state["first_cycle_done_date"] = tdate
            self.save_rmp_state(rmp_state)
            self.append_cycle_log(
                {
                    "timestamp": snapshot.decision_timestamp,
                    "trading_date_et": tdate,
                    "regime_score": 0,
                    "slots": 0,
                    "dd_pct": round(state.current_drawdown_pct, 2),
                    "heat_dollars": 0.0,
                    "heat_cap_dollars": 0.0,
                    "leaders_top10": [],
                    "candidate_checks": [],
                    "actions": [{"code": "SKIP_NO_DAILY_DATA", "detail": daily_cache.get("reason", "")}],
                }
            )
            return [self.hold_signal(snapshot, reasoning=f"SKIP_NO_DAILY_DATA: {daily_cache.get('reason')}")]

        regime_score = int(daily_cache["regime_score"])
        slots = int(daily_cache["slots"])
        daily_stats = daily_cache["daily_stats"]
        leaders_top10 = daily_cache["leaders_top10"]
        spy_prev_close = float(daily_cache["spy_prev_close"])

        spy_ind = snapshot.indicators.get("SPY")
        spy_p = float(spy_ind.last_price) if (spy_ind and spy_ind.last_price > 0) else spy_prev_close
        spy_intraday_ret = ((spy_p / spy_prev_close) - 1.0) if spy_prev_close > 0 else 0.0

        signals: List[OrderSignal] = []
        action_logs: List[Dict[str, Any]] = []
        exiting_tickers: set = set()
        pos_meta = rmp_state.setdefault("position_meta", {})
        cooldowns = rmp_state.setdefault("cooldown_until_idx", {})
        cd_days = int(self.params["sizing_and_risk"]["stopout_cooldown_trading_days"])
        exit_cfg = self.params["exit"]

        # ------------------------------------------------------------------
        # SECTION 8: EXITS (Strict Priority 1 -> 5 on every open position)
        # ------------------------------------------------------------------
        for sym in sorted(state.positions.keys()):
            pos = state.positions[sym]
            if pos.shares <= 0:
                continue
            meta = pos_meta.get(sym)
            if not meta:
                continue
            ind = snapshot.indicators.get(sym)
            p_now = float(ind.last_price) if (ind and ind.last_price > 0) else float(pos.current_price)
            _, today_high, _ = self._get_today_intraday_bar_stats(snapshot, sym)
            if today_high is not None:
                meta["highest_price"] = round(max(float(meta["highest_price"]), p_now, today_high), 4)
            else:
                meta["highest_price"] = round(max(float(meta["highest_price"]), p_now), 4)

            entry_px = float(meta["entry_price"])
            init_stop = float(meta["initial_stop"])
            r_unit = max(0.01, float(meta["initial_r"]))
            cur_stop = float(meta["current_stop"])
            highest_px = float(meta["highest_price"])
            stock_d = daily_stats.get(sym) or {}
            atr14 = float(stock_d.get("atr14") or (ind.atr_14 if ind else entry_px * 0.02))
            sma50 = stock_d.get("sma50")
            prev_close = stock_d.get("prev_close")

            # Priority 1: Stop-loss hit (P <= stop)
            if p_now <= cur_stop:
                is_trail = cur_stop > init_stop + 1e-6
                code = "EXIT_TRAIL" if is_trail else "EXIT_STOP"
                if not is_trail:
                    cooldowns[sym] = max(cooldowns.get(sym, -1), day_idx + cd_days)
                exiting_tickers.add(sym)
                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="SELL",
                        ticker=sym,
                        confidence=0.95,
                        reasoning=f"{code}: {sym} price ${p_now:.2f} <= stop ${cur_stop:.2f} (entry ${entry_px:.2f}, init stop ${init_stop:.2f}).",
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )
                action_logs.append({"code": code, "ticker": sym, "price": p_now, "stop": cur_stop})
                continue

            # Priority 2: Ratchet stop UP (NEVER move stop down)
            new_stop = cur_stop
            if highest_px >= entry_px + float(exit_cfg["breakeven_trigger_r"]) * r_unit:
                new_stop = max(new_stop, entry_px + float(exit_cfg["breakeven_lock_r"]) * r_unit)
            if highest_px >= entry_px + float(exit_cfg["trail_trigger_r"]) * r_unit:
                new_stop = max(new_stop, highest_px - float(exit_cfg["trail_atr_mult"]) * atr14)
            if new_stop > cur_stop + 1e-4:
                meta["current_stop"] = round(new_stop, 4)
                pos.stop_loss = round(new_stop, 4)
                cur_stop = meta["current_stop"]
                action_logs.append({"code": "STOP_RAISED", "ticker": sym, "new_stop": cur_stop, "highest": highest_px})

            unrealized_r = (p_now - entry_px) / r_unit

            # Priority 3: Time stop (after 5 full trading days excluding entry day, on first cycle, if unrealized < +0.5R)
            trading_days_held = day_idx - int(meta.get("entry_day_idx", day_idx))
            if is_first_cycle_today and trading_days_held >= int(exit_cfg["time_stop_trading_days"]):
                if unrealized_r < float(exit_cfg["time_stop_min_unrealized_r"]):
                    exiting_tickers.add(sym)
                    signals.append(
                        OrderSignal(
                            agent_id=self.agent_id,
                            action="SELL",
                            ticker=sym,
                            confidence=0.85,
                            reasoning=(
                                f"EXIT_TIME: {sym} held {trading_days_held} trading days (excluding entry) with "
                                f"unrealized {unrealized_r:+.2f}R < +{exit_cfg['time_stop_min_unrealized_r']}R."
                            ),
                            decision_timestamp=snapshot.decision_timestamp,
                        )
                    )
                    action_logs.append({"code": "EXIT_TIME", "ticker": sym, "days_held": trading_days_held, "unrealized_r": round(unrealized_r, 2)})
                    continue

            # Priority 4: Structural breakdown (prior completed daily close < SMA50)
            if prev_close is not None and sma50 is not None and float(prev_close) < float(sma50):
                exiting_tickers.add(sym)
                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="SELL",
                        ticker=sym,
                        confidence=0.88,
                        reasoning=f"EXIT_SMA50: {sym} prior close ${float(prev_close):.2f} broke below SMA50 ${float(sma50):.2f}.",
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )
                action_logs.append({"code": "EXIT_SMA50", "ticker": sym, "prev_close": prev_close, "sma50": sma50})
                continue

            # Priority 5: Regime OFF (slots == 0) on first cycle of the day
            if slots == 0 and is_first_cycle_today:
                if unrealized_r < float(exit_cfg["regime_off_min_unrealized_r"]):
                    exiting_tickers.add(sym)
                    signals.append(
                        OrderSignal(
                            agent_id=self.agent_id,
                            action="SELL",
                            ticker=sym,
                            confidence=0.85,
                            reasoning=(
                                f"EXIT_REGIME: Regime OFF (score {regime_score}/4, slots=0) and {sym} "
                                f"unrealized {unrealized_r:+.2f}R < +{exit_cfg['regime_off_min_unrealized_r']}R."
                            ),
                            decision_timestamp=snapshot.decision_timestamp,
                        )
                    )
                    action_logs.append({"code": "EXIT_REGIME", "ticker": sym, "unrealized_r": round(unrealized_r, 2)})
                    continue
                else:
                    tight_stop = max(cur_stop, p_now - float(exit_cfg["regime_off_tighten_atr_mult"]) * atr14)
                    if tight_stop > cur_stop + 1e-4:
                        meta["current_stop"] = round(tight_stop, 4)
                        pos.stop_loss = round(tight_stop, 4)
                        action_logs.append({"code": "REGIME_OFF_TIGHTEN_STOP", "ticker": sym, "new_stop": meta["current_stop"]})

        # ------------------------------------------------------------------
        # SECTION 6 & 7: ENTRIES (E1..E6 + Regime + Crash Guard + HeatCap)
        # ------------------------------------------------------------------
        equity = float(state.equity) if state.equity > 0 else 10_000.0
        peak = max(float(state.peak_equity), equity)
        dd_frac = (equity / peak) - 1.0  # <= 0
        sr = self.params["sizing_and_risk"]
        heat = self._compute_portfolio_heat(state, rmp_state, snapshot)
        heat_cap_pct = min(float(sr["heat_cap_base_pct"]) / 100.0, (float(sr["heat_cap_dd_intercept_pct"]) / 100.0) + dd_frac)
        heat_cap = round(max(0.0, equity * heat_cap_pct), 2)

        candidate_checks: List[Dict[str, Any]] = []
        entry_block_reason: Optional[str] = None

        crash_thresh = float(self.params["regime"]["intraday_spy_crash_guard_pct"]) / 100.0  # -0.0125
        if bool(self.params["entry"].get("no_new_entries_on_final_day", True)) and snapshot.trading_day_number >= COMPETITION_TRADING_DAYS:
            entry_block_reason = "SKIP_DAY10"
        elif slots <= 0:
            entry_block_reason = "SKIP_REGIME_OFF"
        elif spy_intraday_ret <= crash_thresh:
            entry_block_reason = "SKIP_CRASH_GUARD"
        elif state.daily_pnl_pct <= float(sr["daily_loss_halt_pct"]):
            entry_block_reason = "SKIP_DAILY_HALT"
        elif not self._is_in_entry_time_window(snapshot):
            entry_block_reason = "SKIP_WINDOW"

        if entry_block_reason:
            action_logs.append({"code": entry_block_reason, "spy_intraday_pct": round(spy_intraday_ret * 100.0, 2)})
        else:
            open_after_exits = [sym for sym, p in state.positions.items() if p.shares > 0 and sym not in exiting_tickers]
            sector_counts: Dict[str, int] = {}
            for sym in open_after_exits:
                sec = self._get_sector(sym)
                sector_counts[sec] = sector_counts.get(sec, 0) + 1

            remaining_slots = max(0, slots - len(open_after_exits))
            max_new_cycle = int(self.params["entry"]["max_new_per_cycle"])
            max_new_day = int(self.params["entry"]["max_new_per_day"])
            entries_today = int(rmp_state.get("entries_today_count", 0))
            entries_this_cycle = 0
            running_heat = heat
            news_vetos = rmp_state.get("news_veto_until_idx") or {}

            e_cfg = self.params["entry"]
            for leader in leaders_top10:
                sym = leader["ticker"]
                score = leader["score"]
                if sym in state.positions and state.positions[sym].shares > 0:
                    candidate_checks.append({"ticker": sym, "score": score, "result": "ALREADY_HELD"})
                    continue

                if day_idx < int(cooldowns.get(sym, -1)):
                    candidate_checks.append({"ticker": sym, "score": score, "result": "SKIP_COOLDOWN"})
                    continue

                if day_idx < int(news_vetos.get(sym, -1)):
                    candidate_checks.append({"ticker": sym, "score": score, "E6_news": False, "result": "SKIP_NEWS"})
                    continue

                ind = snapshot.indicators.get(sym)
                sd = daily_stats.get(sym)
                if not ind or not sd or ind.last_price <= 0:
                    continue

                p_now = float(ind.last_price)
                atr14 = float(sd["atr14"])
                sma50 = float(sd["sma50"])
                h10 = float(sd["h10"])
                prev_close = float(sd["prev_close"])
                closes_tail = list(sd["closes_tail"]) + [p_now]
                rsi2 = compute_wilder_rsi(closes_tail, period=2)
                today_low, _, prev_30m_close = self._get_today_intraday_bar_stats(snapshot, sym)

                pullback_atr = (h10 - p_now) / atr14 if atr14 > 0 else -99.0
                e1 = float(e_cfg["pullback_atr_min"]) <= pullback_atr <= float(e_cfg["pullback_atr_max"])
                e2 = p_now > sma50
                e3 = (rsi2 is not None) and (rsi2 <= float(e_cfg["rsi2_max"]))
                e4 = (
                    prev_30m_close is not None
                    and today_low is not None
                    and p_now > prev_30m_close
                    and p_now >= (today_low + float(e_cfg["intraday_bounce_atr_mult"]) * atr14)
                )
                stock_intraday_ret = ((p_now / prev_close) - 1.0) if prev_close > 0 else 0.0
                rel_intraday = stock_intraday_ret - spy_intraday_ret
                e5 = rel_intraday >= (float(e_cfg["min_relative_intraday_vs_spy_pct"]) / 100.0)
                e6 = True  # Already passed news veto check above

                check_row: Dict[str, Any] = {
                    "ticker": sym,
                    "score": score,
                    "price": round(p_now, 2),
                    "pullback_atr": round(pullback_atr, 2),
                    "rsi2": round(rsi2, 1) if rsi2 is not None else None,
                    "E1": e1,
                    "E2": e2,
                    "E3": e3,
                    "E4": e4,
                    "E5": e5,
                    "E6": e6,
                }

                if not (e1 and e2 and e3 and e4 and e5 and e6):
                    check_row["result"] = "NO_TRIGGER"
                    candidate_checks.append(check_row)
                    continue

                # Trigger passed — check portfolio constraints (slots, sector cap, cycle/day cap, HeatCap)
                if remaining_slots <= 0:
                    check_row["result"] = "SKIP_SLOTS"
                    candidate_checks.append(check_row)
                    continue
                if entries_this_cycle >= max_new_cycle:
                    check_row["result"] = "SKIP_CYCLE_CAP"
                    candidate_checks.append(check_row)
                    continue
                if entries_today >= max_new_day:
                    check_row["result"] = "SKIP_DAY_CAP"
                    candidate_checks.append(check_row)
                    continue

                sec = self._get_sector(sym)
                if sec != "Unknown" and sector_counts.get(sec, 0) >= int(e_cfg["max_per_sector"]):
                    check_row["result"] = "SKIP_SECTOR_CAP"
                    candidate_checks.append(check_row)
                    continue

                target_r_dollars = min(
                    (float(sr["target_risk_per_trade_pct"]) / 100.0) * equity,
                    max(0.0, heat_cap - running_heat),
                )
                min_r_dollars = (float(sr["min_risk_per_trade_pct"]) / 100.0) * equity
                if target_r_dollars < min_r_dollars:
                    check_row["result"] = "SKIP_HEAT"
                    candidate_checks.append(check_row)
                    continue

                # Section 7 sizing & stop
                quote = snapshot.quotes_by_ticker.get(sym)
                ask_px = float(quote.ask_price) if (quote and quote.ask_price > 0) else p_now * (1.0 + DEFAULT_SPREAD_BPS / 20_000.0)
                est_fill = ask_px * (1.0 + SLIPPAGE_RATE)
                raw_d = (float(sr["stop_atr_mult"]) * atr14) / p_now
                d = max(float(sr["stop_min_pct"]), min(float(sr["stop_max_pct"]), raw_d))
                stop_px = round(est_fill * (1.0 - d), 4)
                stop_dist = max(0.01, est_fill - stop_px)
                qty = target_r_dollars / stop_dist
                # Cap by 20% equity notional (common RiskManager also enforces final clipping)
                max_qty_20 = (equity * (float(sr["max_position_equity_pct"]) / 100.0)) / est_fill
                qty = round(min(qty, max_qty_20), 4)
                actual_heat_added = max(0.0, (est_fill - stop_px) * qty)

                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="BUY",
                        ticker=sym,
                        stop_loss=stop_px,
                        take_profit=None,  # Trailing / R-based exits managed by Section 8
                        confidence=round(min(0.95, max(0.65, 0.65 + score * 0.08)), 2),
                        requested_shares=qty,
                        reasoning=(
                            f"ENTRY_PULLBACK: {sym} (Score {score:.2f}, Regime {regime_score}/4, {sec}) "
                            f"pulled back {pullback_atr:.2f} ATR from H10 (${h10:.2f}), RSI(2)={rsi2:.1f}, "
                            f"bounced off day low; stop ${stop_px:.2f} ({d*100:.2f}%), risk ${actual_heat_added:.2f}."
                        ),
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )
                check_row["result"] = "ENTRY_PULLBACK"
                check_row["stop"] = stop_px
                check_row["risk_dollars"] = round(actual_heat_added, 2)
                candidate_checks.append(check_row)
                action_logs.append({"code": "ENTRY_PULLBACK", "ticker": sym, "score": score, "stop": stop_px, "risk_dollars": round(actual_heat_added, 2)})

                remaining_slots -= 1
                entries_this_cycle += 1
                entries_today += 1
                running_heat += actual_heat_added
                if sec != "Unknown":
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1

            rmp_state["entries_today_count"] = entries_today

        # Mark first cycle done & record exposure sample for H2 verification
        rmp_state["first_cycle_done_date"] = tdate
        pos_val = sum(p.market_value for p in state.positions.values() if p.shares > 0)
        exp_pct = round((pos_val / equity) * 100.0, 2) if equity > 0 else 0.0
        exp_log: List[Dict[str, Any]] = rmp_state.setdefault("regime_exposure_log", [])
        if not exp_log or exp_log[-1].get("date") != tdate:
            exp_log.append({"date": tdate, "spy_up": spy_intraday_ret >= 0.0, "exposure_pct": exp_pct})
        else:
            exp_log[-1]["spy_up"] = spy_intraday_ret >= 0.0
            exp_log[-1]["exposure_pct"] = exp_pct

        self.save_rmp_state(rmp_state)
        self.append_cycle_log(
            {
                "timestamp": snapshot.decision_timestamp,
                "trading_date_et": tdate,
                "regime_score": regime_score,
                "regime_flags": daily_cache["regime_flags"],
                "breadth_ratio": daily_cache.get("breadth_ratio"),
                "slots": slots,
                "dd_pct": round(dd_frac * 100.0, 2),
                "heat_dollars": heat,
                "heat_cap_dollars": heat_cap,
                "spy_intraday_pct": round(spy_intraday_ret * 100.0, 2),
                "leaders_top10": leaders_top10,
                "candidate_checks": candidate_checks,
                "actions": action_logs,
            }
        )

        if not signals:
            reason_code = entry_block_reason or "HOLD_NO_SETUP"
            return [
                self.hold_signal(
                    snapshot,
                    reasoning=(
                        f"{reason_code}: Regime {regime_score}/4 (slots={slots}), Heat ${heat:.0f}/${heat_cap:.0f}, "
                        f"checked {len(candidate_checks)} Top-10 leaders — no entry/exit triggers."
                    ),
                    confidence=0.70,
                )
            ]
        return signals

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        """Single-signal compatibility entrypoint returning the highest-priority OrderSignal."""
        batch = self.decide_batch(snapshot, state)
        return batch[0]
