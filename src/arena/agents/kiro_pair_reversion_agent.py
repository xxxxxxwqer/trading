"""Agent 15 — Kiro Pair-Spread Reversion (`kiro_pair_reversion`, PSR).

Deterministic, rule-based coded strategy designed before competition start (zero runtime LLM calls,
zero randomness, locked code + `kiro_pair_reversion_params.json` SHA-256).

Core specification (Part A, A0–A11):
- 12 pre-fixed same-industry large-cap equity pairs (long-only laggard mean-reversion; SPY regime only).
- Daily cache strictly from completed prior daily bars (<= D-1, zero look-ahead):
  60-day log-return beta (clipped [0.5, 2.0]), residual e_t = r_A - beta*r_B, sigma_e (ddof=1),
  60-day Pearson correlation rho60, 9-day cumulative residual S_9, 10-day shock_max,
  Wilder ATR14_d, ADV20, SPY_SMA20, SPY_RV20, SPY_ret5.
- Intraday 30-min updates: e_today, z_live, laggard & lz, SPY_intraday, bar_confirm, cost_bps,
  24h both-leg news veto.
- 3-tier regime filter (CRASH / RISK_OFF / NORMAL), F1–F7 candidate filters + 4-tier tiebreak,
  E1–E9 entry rules, drawdown scaling m_dd, open risk <= 2.5%, breakeven +1R stop ratchet,
  and strict 1->10 exit priority.
"""

from __future__ import annotations

import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import DEFAULT_SPREAD_BPS, PROJECT_ROOT, STATE_DIR, AgentSpec
from ..data.alpaca_client import parse_iso_utc, to_et_datetime
from ..models import AgentLedgerState, Bar, MarketSnapshot, OrderSignal
from .base import BaseAgent
from .claude_rmp_agent import compute_realized_vol_annualized, compute_sma, compute_wilder_atr

KIRO_PSR_ID = "kiro_pair_reversion"
PARAMS_PATH = Path(__file__).resolve().parent / "kiro_pair_reversion_params.json"
UNIVERSE_FUNDAMENTALS_PATH = PROJECT_ROOT / "data_cache" / "universe_fundamentals.json"
STATE_FILENAME = "kiro_psr_state.json"
CYCLE_LOGS_FILENAME = "kiro_psr_cycle_logs.json"


def load_kiro_psr_params(path: Path = PARAMS_PATH) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def _bar_et_date(bar: Bar) -> str:
    ts = (bar.timestamp or "").strip()
    if not ts:
        return ""
    if len(ts) == 10 and ts[4] == "-" and ts[7] == "-":
        return ts
    try:
        return to_et_datetime(ts).strftime("%Y-%m-%d")
    except Exception:
        return ts[:10]


def _filter_completed_daily_bars_strict(bars: List[Bar], trading_date_et: str) -> List[Bar]:
    """Strictly retain ONLY completed daily bars with ET date < trading_date_et (zero look-ahead)."""
    if not bars:
        return []
    out: List[Tuple[str, Bar]] = []
    for b in bars:
        d = _bar_et_date(b)
        if not trading_date_et or (d and d < trading_date_et):
            out.append((d, b))
    out.sort(key=lambda x: x[0])
    # Deduplicate by date keeping latest
    by_date: Dict[str, Bar] = {}
    for d, b in out:
        by_date[d] = b
    return [by_date[d] for d in sorted(by_date.keys())]


def _parse_hhmm_minutes(hhmm: str, default_min: int) -> int:
    try:
        h, m = (int(x) for x in str(hhmm).strip().split(":")[:2])
        return h * 60 + m
    except Exception:
        return default_min


def _sample_cov_var_corr(xs: List[float], ys: List[float]) -> Tuple[float, float, float, float]:
    """Return (cov_xy, var_x, var_y, pearson_corr) using ddof=1."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0, 0.0, 0.0, 0.0
    mx = sum(xs[:n]) / n
    my = sum(ys[:n]) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)
    vx = sum((xs[i] - mx) ** 2 for i in range(n)) / (n - 1)
    vy = sum((ys[i] - my) ** 2 for i in range(n)) / (n - 1)
    denom = math.sqrt(max(0.0, vx * vy))
    corr = (cov / denom) if denom > 1e-12 else 0.0
    return cov, vx, vy, corr


def _sample_std(vals: List[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    mean_v = sum(vals) / n
    var_v = sum((v - mean_v) ** 2 for v in vals) / (n - 1)
    return math.sqrt(max(0.0, var_v))


def compute_daily_pair_cache(
    daily_bars_by_ticker: Dict[str, List[Bar]],
    trading_date_et: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Compute once-per-day pair statistics and SPY regime inputs strictly from D-1 completed daily bars."""
    stats_cfg = params["stats"]
    reg_cfg = params["regime"]
    lookback = int(stats_cfg["lookback_days"])       # 60
    spread_win = int(stats_cfg["spread_window_days"]) # 10
    beta_lo, beta_hi = float(stats_cfg["beta_clip"][0]), float(stats_cfg["beta_clip"][1])
    min_var_b = float(stats_cfg["min_var_b"])
    shock_lb = int(stats_cfg["shock_lookback_days"])  # 10
    atr_p = int(stats_cfg["atr_period"])              # 14
    adv_p = int(stats_cfg["adv_period"])              # 20
    regime_ticker = str(params.get("regime_ticker", "SPY"))

    # 1. Regime ticker (SPY) strictly < trading_date_et
    spy_bars = _filter_completed_daily_bars_strict(daily_bars_by_ticker.get(regime_ticker) or [], trading_date_et)
    spy_closes = [float(b.close) for b in spy_bars if b.close > 0]
    if len(spy_closes) < 21:
        return {
            "valid": False,
            "reason": f"Insufficient {regime_ticker} completed daily bars ({len(spy_closes)} < 21)",
            "spy": {},
            "stocks": {},
            "pairs": {},
        }

    spy_prev_close = spy_closes[-1]
    spy_sma20 = compute_sma(spy_closes, int(reg_cfg["riskoff_spy_sma"])) or spy_prev_close
    spy_rv20 = compute_realized_vol_annualized(spy_closes, 20)
    spy_ret5 = ((spy_closes[-1] / spy_closes[-6]) - 1.0) if len(spy_closes) >= 6 and spy_closes[-6] > 0 else 0.0

    # 2. Per-stock completed daily stats (prev_close, ATR14_d, ADV20)
    pairs_raw: List[List[str]] = params["pairs"]
    unique_tickers = sorted({sym.upper() for pair in pairs_raw for sym in pair})
    filtered_by_sym: Dict[str, List[Bar]] = {}
    stock_stats: Dict[str, Dict[str, Any]] = {}

    for sym in unique_tickers:
        bars = _filter_completed_daily_bars_strict(daily_bars_by_ticker.get(sym) or [], trading_date_et)
        filtered_by_sym[sym] = bars
        if not bars:
            continue
        prev_close = float(bars[-1].close)
        atr14 = compute_wilder_atr(bars, atr_p)
        recent_adv = bars[-adv_p:] if len(bars) >= adv_p else bars
        adv20 = (
            sum(float(b.close) * float(b.volume) for b in recent_adv) / len(recent_adv)
            if recent_adv
            else 0.0
        )
        stock_stats[sym] = {
            "prev_close": round(prev_close, 4),
            "atr14_d": round(float(atr14), 4) if atr14 is not None else None,
            "adv20": round(adv20, 2),
            "bars_count": len(bars),
        }

    # 3. Per-pair stats (P01..P12) aligned by common completed dates strictly < trading_date_et
    pairs_cache: Dict[str, Dict[str, Any]] = {}
    for idx, pair in enumerate(pairs_raw, start=1):
        pair_id = f"P{idx:02d}"
        sym_a, sym_b = pair[0].upper(), pair[1].upper()
        bars_a = filtered_by_sym.get(sym_a) or []
        bars_b = filtered_by_sym.get(sym_b) or []

        map_a = {_bar_et_date(b): float(b.close) for b in bars_a if b.close > 0}
        map_b = {_bar_et_date(b): float(b.close) for b in bars_b if b.close > 0}
        common_dates = sorted(set(map_a.keys()) & set(map_b.keys()))

        if len(common_dates) < lookback + 1:
            pairs_cache[pair_id] = {
                "pair_id": pair_id,
                "a": sym_a,
                "b": sym_b,
                "active": False,
                "inactive_reason": f"Insufficient aligned daily bars ({len(common_dates)} < {lookback + 1})",
            }
            continue

        win_dates = common_dates[-(lookback + 1) :]
        r_a: List[float] = []
        r_b: List[float] = []
        for i in range(1, len(win_dates)):
            ca0, ca1 = map_a[win_dates[i - 1]], map_a[win_dates[i]]
            cb0, cb1 = map_b[win_dates[i - 1]], map_b[win_dates[i]]
            if ca0 <= 0 or ca1 <= 0 or cb0 <= 0 or cb1 <= 0:
                continue
            r_a.append(math.log(ca1 / ca0))
            r_b.append(math.log(cb1 / cb0))

        if len(r_a) < lookback:
            pairs_cache[pair_id] = {
                "pair_id": pair_id,
                "a": sym_a,
                "b": sym_b,
                "active": False,
                "inactive_reason": f"Insufficient valid log returns ({len(r_a)} < {lookback})",
            }
            continue

        cov_ab, var_a, var_b, rho60 = _sample_cov_var_corr(r_a, r_b)
        if var_b < min_var_b or var_a < min_var_b:
            pairs_cache[pair_id] = {
                "pair_id": pair_id,
                "a": sym_a,
                "b": sym_b,
                "active": False,
                "inactive_reason": "var_b_below_min",
            }
            continue

        beta_raw = cov_ab / var_b
        beta = max(beta_lo, min(beta_hi, beta_raw))
        residuals = [r_a[i] - beta * r_b[i] for i in range(len(r_a))]
        sigma_e = _sample_std(residuals)
        if sigma_e <= 1e-12:
            pairs_cache[pair_id] = {
                "pair_id": pair_id,
                "a": sym_a,
                "b": sym_b,
                "active": False,
                "inactive_reason": "sigma_e_zero",
            }
            continue

        s_9 = sum(residuals[-(spread_win - 1) :])
        shock_slice = residuals[-shock_lb:]
        shock_max = max(abs(e) / sigma_e for e in shock_slice)

        pairs_cache[pair_id] = {
            "pair_id": pair_id,
            "a": sym_a,
            "b": sym_b,
            "active": True,
            "beta": round(beta, 6),
            "sigma_e": round(sigma_e, 8),
            "rho60": round(rho60, 6),
            "s_9": round(s_9, 8),
            "shock_max": round(shock_max, 4),
            "last_bar_date": win_dates[-1],
        }

    return {
        "valid": True,
        "trading_date_et": trading_date_et,
        "spy": {
            "prev_close": round(spy_prev_close, 4),
            "sma20": round(float(spy_sma20), 4),
            "rv20": round(float(spy_rv20), 6) if spy_rv20 is not None else 0.0,
            "ret5": round(spy_ret5, 6),
            "last_bar_date": _bar_et_date(spy_bars[-1]),
        },
        "stocks": stock_stats,
        "pairs": pairs_cache,
    }


def classify_psr_regime(
    spy_intraday: float,
    spy_daily: Dict[str, Any],
    params: Dict[str, Any],
) -> Tuple[str, Dict[str, float]]:
    """Classify market regime strictly top-to-bottom (1: CRASH -> 2: RISK_OFF -> 3: NORMAL)."""
    reg_cfg = params["regime"]
    if spy_intraday <= float(reg_cfg["crash_spy_intraday"]):
        return "CRASH", dict(reg_cfg["CRASH"])

    spy_prev = float(spy_daily.get("prev_close", 0.0))
    spy_sma20 = float(spy_daily.get("sma20", 0.0))
    spy_ret5 = float(spy_daily.get("ret5", 0.0))
    spy_rv20 = float(spy_daily.get("rv20", 0.0))

    is_risk_off = (
        (spy_prev < spy_sma20 and spy_ret5 <= float(reg_cfg["riskoff_spy_ret5"]))
        or (spy_rv20 >= float(reg_cfg["riskoff_rv20_ann"]))
    )
    if is_risk_off:
        return "RISK_OFF", dict(reg_cfg["RISK_OFF"])
    return "NORMAL", dict(reg_cfg["NORMAL"])


def compute_dd_multiplier(peak_equity: float, equity: float, params: Dict[str, Any]) -> float:
    """Compute drawdown risk multiplier m_dd from A7 table."""
    if peak_equity <= 0:
        return 1.0
    dd = max(0.0, (peak_equity - equity) / peak_equity)
    for tier in params["risk"]["dd_scaling"]:
        if dd < float(tier["dd_lt"]):
            return float(tier["mult"])
    return 0.0


class KiroPairReversionAgent(BaseAgent):
    """Agent 15 — Kiro Pair-Spread Reversion (`kiro_pair_reversion`, PSR)."""

    DEFAULT_AGENT_ID = KIRO_PSR_ID

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
        self.params = load_kiro_psr_params(self.params_path)
        self.universe_fundamentals_path = Path(universe_fundamentals_path)
        self.psr_state_path = self.state_dir / STATE_FILENAME
        self.cycle_logs_path = self.state_dir / CYCLE_LOGS_FILENAME
        self._sector_map: Optional[Dict[str, str]] = None
        self._last_regime_base_risk_pct: float = float(self.params["regime"]["NORMAL"]["base_risk"]) * 100.0
        self._last_regime_max_slots: int = int(self.params["risk"]["max_positions"])

    def get_effective_stricter_limits(self) -> Dict[str, Any]:
        rk = self.params["risk"]
        return {
            "max_risk_per_trade_pct": round(self._last_regime_base_risk_pct, 4),
            "max_position_equity_pct": round(float(rk["max_weight"]) * 100.0, 2),
            "max_open_positions": min(int(rk["max_positions"]), max(1, int(self._last_regime_max_slots))),
            "daily_loss_limit_pct": round(float(rk["self_daily_halt"]) * 100.0, 2),
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

    def load_psr_state(self) -> Dict[str, Any]:
        if self.psr_state_path.exists():
            try:
                data = json.loads(self.psr_state_path.read_text(encoding="utf-8"))
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
            "position_meta": {},          # ticker -> {pair_id, leg, partner, entry_price, stop_pct, cost_bps, entry_day_idx}
            "ticker_cooldown_until": {},  # ticker -> day_idx (blocked while day_idx < until)
            "pair_cooldown_until": {},    # pair_id -> day_idx (blocked while day_idx < until)
            "seen_sell_trade_ids": [],
        }

    def save_psr_state(self, psr_state: Dict[str, Any]) -> None:
        self.psr_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.psr_state_path.write_text(json.dumps(psr_state, indent=2), encoding="utf-8")

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
        logs = logs[-250:]
        self.cycle_logs_path.parent.mkdir(parents=True, exist_ok=True)
        self.cycle_logs_path.write_text(json.dumps(logs, indent=2), encoding="utf-8")

    def reset_internal_state(self) -> None:
        if self.psr_state_path.exists():
            self.psr_state_path.unlink()
        if self.cycle_logs_path.exists():
            self.cycle_logs_path.unlink()

    def record_risk_rejection(self, signal: OrderSignal, reason: str) -> None:
        """Record a RiskManager rejection as SKIP_RISK_MANAGER in telemetry and roll back tentative entry state."""
        psr_state = self.load_psr_state()
        if signal.action == "BUY" and signal.ticker:
            sym = signal.ticker.upper()
            psr_state["position_meta"].pop(sym, None)
            psr_state["entries_today_count"] = max(0, int(psr_state.get("entries_today_count", 0)) - 1)
            self.save_psr_state(psr_state)

        logs: List[Dict[str, Any]] = []
        if self.cycle_logs_path.exists():
            try:
                loaded = json.loads(self.cycle_logs_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    logs = loaded
            except Exception:
                logs = []
        skip_evt = {
            "code": "SKIP_RISK_MANAGER",
            "ticker": signal.ticker,
            "action": signal.action,
            "detail": reason,
        }
        if logs:
            logs[-1].setdefault("actions", []).append(skip_evt)
        else:
            logs.append({"timestamp": signal.decision_timestamp, "actions": [skip_evt]})
        self.cycle_logs_path.parent.mkdir(parents=True, exist_ok=True)
        self.cycle_logs_path.write_text(json.dumps(logs[-250:], indent=2), encoding="utf-8")

    def _get_last_price(self, snapshot: MarketSnapshot, ticker: str, fallback: float = 0.0) -> float:
        ind = snapshot.indicators.get(ticker)
        if ind is not None and ind.last_price > 0:
            return float(ind.last_price)
        q = snapshot.quotes_by_ticker.get(ticker)
        if q is not None and q.mid_price > 0:
            return float(q.mid_price)
        return fallback

    def _get_spread_and_ask(self, snapshot: MarketSnapshot, ticker: str, last_px: float) -> Tuple[float, float]:
        q = snapshot.quotes_by_ticker.get(ticker)
        if q is not None and q.mid_price > 0:
            spread_bps = float(q.spread_bps) if q.spread_bps > 0 else DEFAULT_SPREAD_BPS
            ask = float(q.ask_price) if q.ask_price > 0 else last_px * (1.0 + (spread_bps / 2.0) / 10_000.0)
            return spread_bps, ask
        spread_bps = DEFAULT_SPREAD_BPS
        ask = last_px * (1.0 + (spread_bps / 2.0) / 10_000.0)
        return spread_bps, ask

    def _check_bar_confirm(self, snapshot: MarketSnapshot, ticker: str) -> bool:
        """E7: 직전 완료 30분봉(t-1)이 close > open AND close > 봉(t-2).close."""
        bars = snapshot.bars_by_ticker.get(ticker) or []
        if len(bars) < 2:
            return False
        dec_dt = parse_iso_utc(snapshot.decision_timestamp)
        valid_bars = [b for b in bars if parse_iso_utc(b.timestamp) <= dec_dt]
        if len(valid_bars) < 2:
            return False
        b_prev2 = valid_bars[-2]
        b_prev1 = valid_bars[-1]
        cfg = self.params["entry"]["bar_confirm"]
        ok_green = (b_prev1.close > b_prev1.open) if cfg.get("require_green", True) else True
        ok_higher = (b_prev1.close > b_prev2.close) if cfg.get("require_higher_close", True) else True
        return bool(ok_green and ok_higher)

    def _check_news_hit(self, snapshot: MarketSnapshot, sym_a: str, sym_b: str) -> bool:
        """Check 24h Alpaca headlines for veto_keywords on either leg of the pair."""
        ncfg = self.params["news"]
        keywords = [k.lower() for k in ncfg.get("veto_keywords", [])]
        lookback_h = float(ncfg.get("lookback_hours", 24))
        check_both = bool(ncfg.get("check_both_legs", True))
        target_syms = {sym_a.upper(), sym_b.upper()} if check_both else {sym_a.upper()}

        dec_dt = parse_iso_utc(snapshot.decision_timestamp)
        cutoff = dec_dt - timedelta(hours=lookback_h)

        for item in snapshot.news or []:
            item_syms = {s.upper() for s in (item.symbols or [])}
            if not (item_syms & target_syms):
                continue
            try:
                cdt = parse_iso_utc(item.created_at)
                if cdt < cutoff or cdt > dec_dt:
                    continue
            except Exception:
                pass
            hl = (item.headline or "").lower()
            if any(kw in hl for kw in keywords):
                return True
        return False

    def _sync_closed_positions_and_cooldowns(
        self,
        state: AgentLedgerState,
        psr_state: Dict[str, Any],
        day_idx: int,
    ) -> None:
        """Detect simulator-executed exits (STOP_LOSS / TAKE_PROFIT) to apply exact 3d/5d cooldowns."""
        pos_meta: Dict[str, Dict[str, Any]] = psr_state.setdefault("position_meta", {})
        t_cd: Dict[str, int] = psr_state.setdefault("ticker_cooldown_until", {})
        p_cd: Dict[str, int] = psr_state.setdefault("pair_cooldown_until", {})
        seen_ids = set(psr_state.get("seen_sell_trade_ids") or [])

        t_cd_days = int(self.params["entry"]["ticker_cooldown_days"])
        p_cd_days = int(self.params["entry"]["pair_cooldown_days_after_loss"])

        for tr in state.trades:
            if tr.side != "SELL" or tr.trade_id in seen_ids:
                continue
            seen_ids.add(tr.trade_id)
            sym = tr.ticker.upper()
            t_cd[sym] = max(int(t_cd.get(sym, -1)), day_idx + t_cd_days)
            meta = pos_meta.get(sym)
            if meta:
                pair_id = str(meta.get("pair_id", ""))
                er = (tr.exit_reason or "").upper()
                if pair_id and ("STOP" in er or er in ("EXIT_STOP_LOSS", "EXIT_Z_STOP")):
                    p_cd[pair_id] = max(int(p_cd.get(pair_id, -1)), day_idx + p_cd_days)

        psr_state["seen_sell_trade_ids"] = sorted(seen_ids)[-300:]

        # Clean up position_meta for any position no longer open
        open_syms = {s for s, p in state.positions.items() if p.shares > 0}
        for sym in list(pos_meta.keys()):
            if sym not in open_syms:
                t_cd[sym] = max(int(t_cd.get(sym, -1)), day_idx + t_cd_days)
                pos_meta.pop(sym, None)

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        batch = self.decide_batch(snapshot, state)
        return batch[0] if batch else self.hold_signal(snapshot, reasoning="HOLD_NO_ACTION")

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        """Run complete deterministic PSR cycle: daily cache -> regime -> exits/breakeven -> entries."""
        tdate = snapshot.trading_date_et
        psr_state = self.load_psr_state()

        # Track trading day index deterministically (0, 1, 2, ...)
        dates_seen: List[str] = list(psr_state.get("trading_dates_seen") or [])
        if tdate and tdate not in dates_seen:
            dates_seen.append(tdate)
            dates_seen.sort()
        psr_state["trading_dates_seen"] = dates_seen
        day_idx = dates_seen.index(tdate) if tdate in dates_seen else 0

        if psr_state.get("entries_today_date") != tdate:
            psr_state["entries_today_date"] = tdate
            psr_state["entries_today_count"] = 0

        # Sync cooldowns for positions closed by simulator stop/TP since last cycle
        self._sync_closed_positions_and_cooldowns(state, psr_state, day_idx)

        # 1. Once-per-day cache strictly from completed D-1 daily bars
        if psr_state.get("daily_cache_date") != tdate or not psr_state.get("daily_cache"):
            daily_cache = compute_daily_pair_cache(
                daily_bars_by_ticker=snapshot.daily_bars_by_ticker or {},
                trading_date_et=tdate,
                params=self.params,
            )
            psr_state["daily_cache_date"] = tdate
            psr_state["daily_cache"] = daily_cache
        else:
            daily_cache = psr_state["daily_cache"]

        if not daily_cache.get("valid"):
            self.save_psr_state(psr_state)
            self.append_cycle_log(
                {
                    "timestamp": snapshot.decision_timestamp,
                    "trading_date_et": tdate,
                    "regime": "UNKNOWN",
                    "active_pairs_count": 0,
                    "pair_z_live": {},
                    "top_candidates": [],
                    "actions": [{"code": "SKIP_PAIR_INACTIVE", "detail": daily_cache.get("reason", "no_daily_data")}],
                }
            )
            return [self.hold_signal(snapshot, reasoning=f"HOLD_NO_ACTION: {daily_cache.get('reason')}")]

        # 2. Compute live regime & intraday z_live for all 12 pairs
        spy_daily = daily_cache["spy"]
        stock_stats = daily_cache["stocks"]
        pairs_cache = daily_cache["pairs"]

        spy_prev = float(spy_daily["prev_close"])
        spy_last = self._get_last_price(snapshot, self.params["regime_ticker"], fallback=spy_prev)
        spy_intraday = ((spy_last / spy_prev) - 1.0) if spy_prev > 0 else 0.0

        regime_name, regime_cfg = classify_psr_regime(spy_intraday, spy_daily, self.params)
        self._last_regime_base_risk_pct = float(regime_cfg["base_risk"]) * 100.0
        self._last_regime_max_slots = int(regime_cfg["max_slots"]) if regime_cfg["max_slots"] > 0 else 1

        sqrt_w = math.sqrt(float(self.params["stats"]["spread_window_days"]))
        pair_z_live: Dict[str, Optional[float]] = {}
        active_pairs_count = 0

        for pair_id, pinfo in sorted(pairs_cache.items()):
            if not pinfo.get("active"):
                pair_z_live[pair_id] = None
                continue
            active_pairs_count += 1
            sym_a, sym_b = pinfo["a"], pinfo["b"]
            prev_a = float((stock_stats.get(sym_a) or {}).get("prev_close") or 0.0)
            prev_b = float((stock_stats.get(sym_b) or {}).get("prev_close") or 0.0)
            last_a = self._get_last_price(snapshot, sym_a, fallback=prev_a)
            last_b = self._get_last_price(snapshot, sym_b, fallback=prev_b)
            if prev_a <= 0 or prev_b <= 0 or last_a <= 0 or last_b <= 0:
                pair_z_live[pair_id] = None
                continue
            beta = float(pinfo["beta"])
            sigma_e = float(pinfo["sigma_e"])
            s_9 = float(pinfo["s_9"])
            e_today = math.log(last_a / prev_a) - beta * math.log(last_b / prev_b)
            z_live = (s_9 + e_today) / (sigma_e * sqrt_w)
            pair_z_live[pair_id] = round(z_live, 4)

        et_dt = to_et_datetime(snapshot.decision_timestamp)
        et_mins = et_dt.hour * 60 + et_dt.minute
        is_early_close = str(snapshot.session_close_et or "16:00") != "16:00"

        signals: List[OrderSignal] = []
        action_logs: List[Dict[str, Any]] = [{"code": f"REGIME_{regime_name}", "spy_intraday_pct": round(spy_intraday * 100.0, 2)}]
        exiting_tickers: set[str] = set()

        pos_meta: Dict[str, Dict[str, Any]] = psr_state.setdefault("position_meta", {})
        t_cd: Dict[str, int] = psr_state.setdefault("ticker_cooldown_until", {})
        p_cd: Dict[str, int] = psr_state.setdefault("pair_cooldown_until", {})

        exit_cfg = self.params["exit"]
        flt_cfg = self.params["filters"]
        rk_cfg = self.params["risk"]
        ent_cfg = self.params["entry"]

        # 3. EXITS (Strict Priority 1 -> 10 per open position) & Breakeven Stop Ratchet
        for sym in sorted(state.positions.keys()):
            pos = state.positions[sym]
            if pos.shares <= 0:
                continue

            meta = pos_meta.get(sym)
            if not meta:
                # Reconstruct minimal fallback metadata if position exists without meta
                meta = {
                    "pair_id": "",
                    "leg": "A",
                    "partner": "",
                    "entry_price": float(pos.avg_entry_price),
                    "stop_pct": max(0.015, (pos.avg_entry_price - pos.stop_loss) / pos.avg_entry_price)
                    if pos.avg_entry_price > 0 and pos.stop_loss > 0
                    else 0.03,
                    "cost_bps": DEFAULT_SPREAD_BPS + 10.0,
                    "entry_day_idx": day_idx,
                }
                pos_meta[sym] = meta

            last_px = self._get_last_price(snapshot, sym, fallback=float(pos.current_price))
            pair_id = str(meta.get("pair_id", ""))
            leg = str(meta.get("leg", "A"))
            partner = str(meta.get("partner", ""))
            pinfo = pairs_cache.get(pair_id) or {}
            raw_z = pair_z_live.get(pair_id)
            lz_now: Optional[float] = None
            if raw_z is not None:
                lz_now = raw_z if leg == "A" else -raw_z

            # Separate Breakeven Stop Ratchet (+1R): entry * (1 + stop_pct) -> entry * (1 + cost_bps/10000)
            entry_px = float(meta.get("entry_price") or pos.avg_entry_price)
            stop_pct = float(meta.get("stop_pct") or 0.03)
            cost_bps = float(meta.get("cost_bps") or (DEFAULT_SPREAD_BPS + 10.0))
            be_trigger = entry_px * (1.0 + float(rk_cfg["breakeven_trigger_R"]) * stop_pct)
            if last_px >= be_trigger and entry_px > 0:
                be_stop = round(entry_px * (1.0 + cost_bps / 10_000.0), 4)
                if be_stop > pos.stop_loss:
                    old_stop = pos.stop_loss
                    pos.stop_loss = be_stop
                    action_logs.append(
                        {
                            "code": "ADJ_STOP_BREAKEVEN",
                            "ticker": sym,
                            "old_stop": round(old_stop, 4),
                            "new_stop": be_stop,
                        }
                    )

            # Evaluate Exits strictly in Priority 1 -> 10 order (first match only)
            # (Priority 1: EXIT_SYSTEM on BLOWN_UP; Priority 2/3: EXIT_STOP_LOSS / EXIT_TP_PRICE checked by FillSimulator)
            exit_code: Optional[str] = None
            exit_detail = ""

            final_day_min = _parse_hhmm_minutes(str(exit_cfg["final_day_exit_et"]), 15 * 60)
            time_exit_min = _parse_hhmm_minutes(
                str(exit_cfg["time_exit_et_early_close"] if is_early_close else exit_cfg["time_exit_et_normal"]),
                12 * 60 + 30 if is_early_close else 15 * 60 + 30,
            )
            held_days = day_idx - int(meta.get("entry_day_idx", day_idx))
            unrealized_pnl = (last_px - float(pos.avg_entry_price)) * float(pos.shares)

            # Priority 1: System BLOWN_UP
            if state.status == "BLOWN_UP":
                exit_code = "EXIT_SYSTEM"
                exit_detail = "Account status BLOWN_UP"
            # Priority 4: Final Day (Day 30, >= 15:00 ET or near close)
            elif snapshot.trading_day_number >= int(exit_cfg["final_day"]) and (
                et_mins >= final_day_min or snapshot.is_near_market_close
            ):
                exit_code = "EXIT_FINAL_DAY"
                exit_detail = f"Day {snapshot.trading_day_number} final-day liquidation window"
            # Priority 5: News Veto on either leg while holding
            elif partner and self._check_news_hit(snapshot, sym, partner):
                exit_code = "EXIT_NEWS_VETO"
                exit_detail = f"24h veto keyword headline hit on {sym}/{partner}"
            # Priority 6: Spread Z-Stop (lz <= -3.5)
            elif lz_now is not None and lz_now <= float(exit_cfg["z_stop"]):
                exit_code = "EXIT_Z_STOP"
                exit_detail = f"Pair {pair_id} spread widened to lz={lz_now:.2f} <= {exit_cfg['z_stop']}"
            # Priority 7: CRASH regime with losing position
            elif regime_name == "CRASH" and unrealized_pnl < 0:
                exit_code = "EXIT_CRASH"
                exit_detail = f"Regime CRASH (SPY {spy_intraday*100:.2f}%) with unrealized PnL ${unrealized_pnl:.2f} < 0"
            # Priority 8: Mean-reversion target hit (lz >= -0.5)
            elif lz_now is not None and lz_now >= float(exit_cfg["z_take_profit"]):
                exit_code = "EXIT_TP_REVERT"
                exit_detail = f"Pair {pair_id} reverted to lz={lz_now:.2f} >= {exit_cfg['z_take_profit']}"
            # Priority 9: Correlation breakdown (rho60 < 0.40)
            elif pinfo and pinfo.get("active") and float(pinfo.get("rho60", 1.0)) < float(flt_cfg["rho_exit_break"]):
                exit_code = "EXIT_CORR_BREAK"
                exit_detail = f"Pair {pair_id} rho60={pinfo['rho60']:.2f} < {flt_cfg['rho_exit_break']}"
            # Priority 10: Max holding time (>= 7 trading days at >= 15:30 / 12:30 ET)
            elif held_days >= int(exit_cfg["max_hold_days"]) and et_mins >= time_exit_min:
                exit_code = "EXIT_TIME"
                exit_detail = f"Held {held_days} trading days >= {exit_cfg['max_hold_days']}"

            if exit_code:
                exiting_tickers.add(sym)
                t_cd[sym] = max(int(t_cd.get(sym, -1)), day_idx + int(ent_cfg["ticker_cooldown_days"]))
                if exit_code == "EXIT_Z_STOP" and pair_id:
                    p_cd[pair_id] = max(int(p_cd.get(pair_id, -1)), day_idx + int(ent_cfg["pair_cooldown_days_after_loss"]))
                pos_meta.pop(sym, None)
                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="SELL",
                        ticker=sym,
                        stop_loss=None,
                        take_profit=None,
                        confidence=0.95,
                        reasoning=f"{exit_code}: {exit_detail}",
                        requested_shares=pos.shares,
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )
                action_logs.append({"code": exit_code, "ticker": sym, "pair_id": pair_id, "detail": exit_detail})

        # 4. ENTRIES (E1..E9 + F1..F7 + A7 Sizing)
        win_bounds = ent_cfg["window_early_close_et"] if is_early_close else ent_cfg["window_normal_et"]
        win_start = _parse_hhmm_minutes(str(win_bounds[0]), 10 * 60 + 30)
        win_end = _parse_hhmm_minutes(str(win_bounds[1]), 12 * 60 if is_early_close else 15 * 60)

        m_dd = compute_dd_multiplier(state.peak_equity, state.equity, self.params)
        open_after_exits = {
            s: p for s, p in state.positions.items() if p.shares > 0 and s not in exiting_tickers
        }
        held_pair_ids = {
            str((pos_meta.get(s) or {}).get("pair_id", ""))
            for s in open_after_exits
            if (pos_meta.get(s) or {}).get("pair_id")
        }
        max_slots_eff = min(int(regime_cfg["max_slots"]), int(rk_cfg["max_positions"]))
        entries_today = int(psr_state.get("entries_today_count", 0))

        cycle_block: Optional[str] = None
        if not (win_start <= et_mins <= win_end):
            cycle_block = "SKIP_OUT_OF_WINDOW"
        elif snapshot.trading_day_number > int(ent_cfg["last_entry_trading_day"]):
            cycle_block = "SKIP_LATE_TOURNAMENT"
        elif regime_name == "CRASH":
            cycle_block = "SKIP_REGIME_CRASH"
        elif state.status in ("DAILY_HALTED", "BLOWN_UP"):
            cycle_block = "SKIP_HALTED"
        elif float(state.daily_pnl_pct) <= float(rk_cfg["self_daily_halt"]) * 100.0:
            cycle_block = "SKIP_SELF_DAILY_HALT"
        elif m_dd <= 0.0:
            cycle_block = "SKIP_DD_ZERO"
        elif len(open_after_exits) >= max_slots_eff:
            cycle_block = "SKIP_SLOTS_FULL"
        elif entries_today >= int(ent_cfg["max_entries_per_day"]):
            cycle_block = "SKIP_DAILY_ENTRY_CAP"

        candidate_audit: List[Dict[str, Any]] = []

        if cycle_block:
            action_logs.append({"code": cycle_block})
        else:
            passing_raw: List[Dict[str, Any]] = []
            z_min = float(flt_cfg["z_entry_min"])
            z_max = float(flt_cfg["z_entry_max"])
            rho_min = float(flt_cfg["rho_min_entry"])
            shock_lim = float(flt_cfg["shock_max"])
            max_spr = float(flt_cfg["max_spread_bps"])
            slip_side = float(flt_cfg["slippage_bps_per_side"])
            lag_share = float(flt_cfg["laggard_share_of_reversion"])
            min_edge_ratio = float(flt_cfg["min_edge_to_cost_ratio"])
            z_tp = abs(float(exit_cfg["z_take_profit"]))

            for pair_id, pinfo in sorted(pairs_cache.items()):
                if not pinfo.get("active") or pair_z_live.get(pair_id) is None:
                    candidate_audit.append({"pair_id": pair_id, "ticker": pinfo.get("a", ""), "score": 0.0, "result": "SKIP_PAIR_INACTIVE"})
                    continue

                rho60 = float(pinfo["rho60"])
                z_live = float(pair_z_live[pair_id])  # type: ignore[arg-type]
                if rho60 < rho_min:
                    candidate_audit.append({"pair_id": pair_id, "ticker": pinfo["a"], "z_live": z_live, "score": 0.0, "result": "SKIP_LOW_CORR"})
                    continue

                if abs(z_live) < z_min:
                    candidate_audit.append({"pair_id": pair_id, "ticker": pinfo["a"], "z_live": z_live, "score": round(abs(z_live) * rho60, 4), "result": "SKIP_Z_WEAK"})
                    continue
                if abs(z_live) > z_max:
                    candidate_audit.append({"pair_id": pair_id, "ticker": pinfo["a"], "z_live": z_live, "score": round(abs(z_live) * rho60, 4), "result": "SKIP_Z_EXTREME"})
                    continue

                if z_live <= -z_min:
                    laggard, partner, leg, lz = pinfo["a"], pinfo["b"], "A", z_live
                else:
                    laggard, partner, leg, lz = pinfo["b"], pinfo["a"], "B", -z_live

                score = round(abs(lz) * rho60, 6)
                shock_max = float(pinfo["shock_max"])
                if shock_max >= shock_lim:
                    candidate_audit.append({"pair_id": pair_id, "ticker": laggard, "lz": round(lz, 4), "score": score, "result": "SKIP_SHOCK"})
                    continue

                if self._check_news_hit(snapshot, laggard, partner):
                    candidate_audit.append({"pair_id": pair_id, "ticker": laggard, "lz": round(lz, 4), "score": score, "result": "SKIP_NEWS"})
                    continue

                l_stat = stock_stats.get(laggard) or {}
                last_px = self._get_last_price(snapshot, laggard, fallback=float(l_stat.get("prev_close") or 0.0))
                if last_px <= 0 or not l_stat.get("atr14_d"):
                    candidate_audit.append({"pair_id": pair_id, "ticker": laggard, "lz": round(lz, 4), "score": score, "result": "SKIP_PAIR_INACTIVE"})
                    continue

                spread_bps, ask_px = self._get_spread_and_ask(snapshot, laggard, last_px)
                if spread_bps > max_spr:
                    candidate_audit.append({"pair_id": pair_id, "ticker": laggard, "lz": round(lz, 4), "score": score, "result": "SKIP_SPREAD"})
                    continue

                cost_bps = spread_bps + 2.0 * slip_side
                sigma_e = float(pinfo["sigma_e"])
                g_bps = 10_000.0 * lag_share * (abs(lz) - z_tp) * sigma_e * sqrt_w
                if g_bps < min_edge_ratio * cost_bps:
                    candidate_audit.append({"pair_id": pair_id, "ticker": laggard, "lz": round(lz, 4), "score": score, "result": "SKIP_EDGE_COST"})
                    continue

                if laggard in open_after_exits or pair_id in held_pair_ids:
                    candidate_audit.append({"pair_id": pair_id, "ticker": laggard, "lz": round(lz, 4), "score": score, "result": "SKIP_ALREADY_HELD"})
                    continue

                if day_idx < int(t_cd.get(laggard, -1)) or day_idx < int(p_cd.get(pair_id, -1)):
                    candidate_audit.append({"pair_id": pair_id, "ticker": laggard, "lz": round(lz, 4), "score": score, "result": "SKIP_COOLDOWN"})
                    continue

                passing_raw.append(
                    {
                        "pair_id": pair_id,
                        "laggard": laggard,
                        "partner": partner,
                        "leg": leg,
                        "lz": lz,
                        "rho60": rho60,
                        "beta": float(pinfo["beta"]),
                        "sigma_e": sigma_e,
                        "score": score,
                        "spread_bps": spread_bps,
                        "cost_bps": cost_bps,
                        "g_bps": g_bps,
                        "ask_px": ask_px,
                        "atr14_d": float(l_stat["atr14_d"]),
                        "adv20": float(l_stat.get("adv20") or 0.0),
                    }
                )

            # Deduplicate same laggard keeping higher score (tiebreak: spread asc, ADV20 desc, pair_id asc)
            best_by_ticker: Dict[str, Dict[str, Any]] = {}
            for cand in passing_raw:
                key = cand["laggard"]
                sort_tuple = (-cand["score"], cand["spread_bps"], -cand["adv20"], cand["pair_id"])
                if key not in best_by_ticker or sort_tuple < best_by_ticker[key]["_sort"]:
                    cand["_sort"] = sort_tuple
                    best_by_ticker[key] = cand

            ranked = sorted(
                best_by_ticker.values(),
                key=lambda c: (-c["score"], c["spread_bps"], -c["adv20"], c["laggard"]),
            )

            # Compute current gross exposure, open risk, and sector counts
            sector_counts: Dict[str, int] = {}
            cur_gross = 0.0
            cur_open_risk = 0.0
            for osym, opos in open_after_exits.items():
                sec = self._get_sector(osym)
                sector_counts[sec] = sector_counts.get(sec, 0) + 1
                opx = self._get_last_price(snapshot, osym, fallback=float(opos.current_price))
                cur_gross += float(opos.shares) * opx
                cur_open_risk += max(0.0, opx - float(opos.stop_loss)) * float(opos.shares)

            equity = float(state.equity) if state.equity > 0 else 10_000.0
            max_gross_dollars = equity * float(regime_cfg["max_gross"])
            max_open_risk_dollars = equity * float(rk_cfg["max_open_risk"])
            max_per_sec = int(ent_cfg["max_positions_per_sector"])
            share_dec = int(ent_cfg["share_decimals"])
            scale_pow = 10.0 ** share_dec
            min_usd = float(ent_cfg["min_order_usd"])

            entered = False
            for cand in ranked:
                sym = cand["laggard"]
                sec = self._get_sector(sym)

                # E9: Sector cap < 2
                if sector_counts.get(sec, 0) >= max_per_sec:
                    candidate_audit.append({"pair_id": cand["pair_id"], "ticker": sym, "lz": round(cand["lz"], 4), "score": cand["score"], "result": "SKIP_SECTOR_CAP"})
                    continue

                # E7: 30-min bar confirmation
                if not self._check_bar_confirm(snapshot, sym):
                    candidate_audit.append({"pair_id": cand["pair_id"], "ticker": sym, "lz": round(cand["lz"], 4), "score": cand["score"], "result": "SKIP_NO_BAR_CONFIRM"})
                    continue

                # A7 Sizing & E8 exposure/open-risk caps
                p_ask = float(cand["ask_px"])
                stop_pct = max(
                    float(rk_cfg["stop_pct_min"]),
                    min(float(rk_cfg["stop_pct_max"]), float(rk_cfg["stop_atr_mult"]) * cand["atr14_d"] / p_ask),
                )
                stop_price = round(p_ask * (1.0 - stop_pct), 4)

                tp_pct_raw = (abs(cand["lz"]) - z_tp) * cand["sigma_e"] * sqrt_w
                tp_pct = max(float(rk_cfg["tp_pct_min"]), min(float(rk_cfg["tp_pct_max"]), tp_pct_raw))
                tp_price = round(p_ask * (1.0 + tp_pct), 4)

                rem_gross = max_gross_dollars - cur_gross
                if rem_gross < min_usd:
                    candidate_audit.append({"pair_id": cand["pair_id"], "ticker": sym, "lz": round(cand["lz"], 4), "score": cand["score"], "result": "SKIP_GROSS_CAP"})
                    continue

                risk_pct = float(regime_cfg["base_risk"]) * m_dd
                raw_shares = (equity * risk_pct) / (p_ask * stop_pct)
                raw_shares = min(raw_shares, (equity * float(rk_cfg["max_weight"])) / p_ask)
                raw_shares = min(raw_shares, rem_gross / p_ask)
                shares = math.floor(max(0.0, raw_shares) * scale_pow) / scale_pow

                if shares * p_ask < min_usd:
                    candidate_audit.append({"pair_id": cand["pair_id"], "ticker": sym, "lz": round(cand["lz"], 4), "score": cand["score"], "result": "SKIP_TOO_SMALL"})
                    continue

                new_open_risk = max(0.0, p_ask - stop_price) * shares
                if cur_open_risk + new_open_risk > max_open_risk_dollars + 1e-6:
                    candidate_audit.append({"pair_id": cand["pair_id"], "ticker": sym, "lz": round(cand["lz"], 4), "score": cand["score"], "result": "SKIP_OPEN_RISK_CAP"})
                    continue

                # Approved by PSR -> emit BUY OrderSignal (still evaluated by shared RiskManager)
                self._last_regime_base_risk_pct = round(risk_pct * 100.0, 4)
                reasoning = (
                    f"ENTRY_PAIR_LAGGARD pair={cand['pair_id']} leg={cand['leg']} "
                    f"lz={cand['lz']:.2f} rho60={cand['rho60']:.2f} beta={cand['beta']:.2f} "
                    f"g_bps={cand['g_bps']:.1f} cost_bps={cand['cost_bps']:.1f} "
                    f"stop_pct={stop_pct*100:.2f}% tp_pct={tp_pct*100:.2f}%"
                )
                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="BUY",
                        ticker=sym,
                        stop_loss=stop_price,
                        take_profit=tp_price,
                        confidence=round(min(0.95, max(0.55, 0.55 + (abs(cand["lz"]) - 2.0) * 0.15)), 2),
                        reasoning=reasoning,
                        requested_shares=shares,
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )
                pos_meta[sym] = {
                    "pair_id": cand["pair_id"],
                    "leg": cand["leg"],
                    "partner": cand["partner"],
                    "entry_price": round(p_ask, 4),
                    "stop_pct": round(stop_pct, 6),
                    "cost_bps": round(cand["cost_bps"], 2),
                    "entry_day_idx": day_idx,
                }
                psr_state["entries_today_count"] = entries_today + 1
                action_logs.append(
                    {
                        "code": "ENTRY_PAIR_LAGGARD",
                        "ticker": sym,
                        "pair_id": cand["pair_id"],
                        "lz": round(cand["lz"], 4),
                        "rho60": round(cand["rho60"], 4),
                        "beta": round(cand["beta"], 4),
                        "g_bps": round(cand["g_bps"], 2),
                        "cost_bps": round(cand["cost_bps"], 2),
                        "stop_pct": round(stop_pct, 4),
                        "tp_pct": round(tp_pct, 4),
                        "shares": shares,
                    }
                )
                candidate_audit.append({"pair_id": cand["pair_id"], "ticker": sym, "lz": round(cand["lz"], 4), "score": cand["score"], "result": "ENTRY_PAIR_LAGGARD"})
                entered = True
                break  # max_entries_per_cycle = 1

            if not entered:
                action_logs.append({"code": "SKIP_NO_CANDIDATE"})

        # Persist internal state & telemetry (top 3 candidates + all 12 pair z_live)
        self.save_psr_state(psr_state)
        top_cands = sorted(candidate_audit, key=lambda c: float(c.get("score") or 0.0), reverse=True)[:3]
        self.append_cycle_log(
            {
                "timestamp": snapshot.decision_timestamp,
                "trading_date_et": tdate,
                "regime": regime_name,
                "spy_intraday_pct": round(spy_intraday * 100.0, 2),
                "m_dd": m_dd,
                "active_pairs_count": active_pairs_count,
                "pair_z_live": pair_z_live,
                "top_candidates": top_cands,
                "actions": action_logs,
            }
        )

        if not signals:
            top_code = action_logs[-1]["code"] if action_logs else "HOLD_NO_ACTION"
            return [
                self.hold_signal(
                    snapshot,
                    reasoning=f"{top_code}: regime={regime_name}, active_pairs={active_pairs_count}",
                )
            ]
        return signals
