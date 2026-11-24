"""Shared No Look-Ahead utilities, state persistence, and deterministic portfolio rebalancer
for Part B quantitative agents (#16–#35)."""

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

PART_B_PARAMS_PATH = Path(__file__).resolve().parent / "part_b_params.json"
UNIVERSE_FUNDAMENTALS_PATH = PROJECT_ROOT / "data_cache" / "universe_fundamentals.json"
PART_B_STATE_FILENAME = "part_b_agents_state.json"

_PARAMS_CACHE: Optional[Dict[str, Any]] = None
_FUNDAMENTALS_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def load_part_b_params(path: Path = PART_B_PARAMS_PATH) -> Dict[str, Any]:
    global _PARAMS_CACHE
    if path == PART_B_PARAMS_PATH and _PARAMS_CACHE is not None:
        return _PARAMS_CACHE
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if path == PART_B_PARAMS_PATH:
        _PARAMS_CACHE = data
    return data


def load_fundamentals_map() -> Dict[str, Dict[str, Any]]:
    global _FUNDAMENTALS_CACHE
    if _FUNDAMENTALS_CACHE is not None:
        return _FUNDAMENTALS_CACHE
    out: Dict[str, Dict[str, Any]] = {}
    if UNIVERSE_FUNDAMENTALS_PATH.exists():
        try:
            raw = json.loads(UNIVERSE_FUNDAMENTALS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                out = {k.upper(): v for k, v in raw.items() if isinstance(v, dict)}
        except Exception:
            out = {}
    _FUNDAMENTALS_CACHE = out
    return out


def parse_hhmm_minutes(hhmm: str, default_min: int) -> int:
    try:
        h, m = (int(x) for x in str(hhmm).strip().split(":")[:2])
        return h * 60 + m
    except Exception:
        return default_min


def snapshot_et_minutes(snapshot: MarketSnapshot) -> int:
    et_dt = to_et_datetime(snapshot.decision_timestamp)
    return et_dt.hour * 60 + et_dt.minute


def is_early_close(snapshot: MarketSnapshot) -> bool:
    return str(snapshot.session_close_et or "16:00") != "16:00"


def bar_et_date(bar: Bar) -> str:
    ts = (bar.timestamp or "").strip()
    if not ts:
        return ""
    if len(ts) == 10 and ts[4] == "-" and ts[7] == "-":
        return ts
    try:
        return to_et_datetime(ts).strftime("%Y-%m-%d")
    except Exception:
        return ts[:10]


def strict_prior_daily_bars(snapshot: MarketSnapshot, ticker: str) -> List[Bar]:
    """Return completed daily bars strictly BEFORE snapshot.trading_date_et (zero look-ahead)."""
    raw = (snapshot.daily_bars_by_ticker or {}).get(ticker) or []
    tdate = snapshot.trading_date_et
    by_date: Dict[str, Bar] = {}
    for b in raw:
        d = bar_et_date(b)
        if not tdate or (d and d < tdate):
            by_date[d] = b
    return [by_date[d] for d in sorted(by_date.keys())]


def intraday_bars_today(snapshot: MarketSnapshot, ticker: str) -> List[Bar]:
    """Return today's 30-min bars with timestamp <= snapshot.decision_timestamp."""
    raw = (snapshot.bars_by_ticker or {}).get(ticker) or []
    tdate = snapshot.trading_date_et
    dec_dt = parse_iso_utc(snapshot.decision_timestamp)
    out: List[Bar] = []
    for b in raw:
        try:
            bdt = parse_iso_utc(b.timestamp)
        except Exception:
            continue
        if bdt <= dec_dt and (not tdate or to_et_datetime(bdt).strftime("%Y-%m-%d") == tdate):
            out.append(b)
    out.sort(key=lambda b: b.timestamp)
    return out


def get_last_price(snapshot: MarketSnapshot, ticker: str, fallback: float = 0.0) -> float:
    ind = (snapshot.indicators or {}).get(ticker)
    if ind is not None and ind.last_price > 0:
        return float(ind.last_price)
    q = (snapshot.quotes_by_ticker or {}).get(ticker)
    if q is not None and q.mid_price > 0:
        return float(q.mid_price)
    return fallback


def get_spread_bps(snapshot: MarketSnapshot, ticker: str) -> float:
    q = (snapshot.quotes_by_ticker or {}).get(ticker)
    if q is not None and q.spread_bps > 0:
        return float(q.spread_bps)
    return DEFAULT_SPREAD_BPS


def compute_return_n(closes: List[float], n: int) -> Optional[float]:
    if len(closes) <= n or closes[-(n + 1)] <= 0:
        return None
    return (closes[-1] / closes[-(n + 1)]) - 1.0


def compute_beta_n(stock_closes: List[float], spy_closes: List[float], n: int = 63) -> Optional[float]:
    m = min(len(stock_closes), len(spy_closes))
    if m < n + 1:
        return None
    sc = stock_closes[-(n + 1) :]
    mc = spy_closes[-(n + 1) :]
    rs = [math.log(sc[i] / sc[i - 1]) for i in range(1, len(sc)) if sc[i] > 0 and sc[i - 1] > 0]
    rm = [math.log(mc[i] / mc[i - 1]) for i in range(1, len(mc)) if mc[i] > 0 and mc[i - 1] > 0]
    k = min(len(rs), len(rm))
    if k < max(10, n - 5):
        return None
    rs, rm = rs[-k:], rm[-k:]
    ms = sum(rs) / k
    mm = sum(rm) / k
    cov = sum((rs[i] - ms) * (rm[i] - mm) for i in range(k)) / (k - 1)
    var_m = sum((rm[i] - mm) ** 2 for i in range(k)) / (k - 1)
    if var_m <= 1e-12:
        return None
    return cov / var_m


def compute_bb_width(closes: List[float], period: int = 20) -> Optional[float]:
    if len(closes) < period:
        return None
    w = closes[-period:]
    mid = sum(w) / period
    if mid <= 0:
        return None
    var = sum((x - mid) ** 2 for x in w) / period
    sd = math.sqrt(max(0.0, var))
    return (4.0 * sd) / mid  # (upper - lower) / mid where upper/lower = mid +/- 2*sd


def compute_session_vwap_and_std(bars_today: List[Bar]) -> Tuple[Optional[float], Optional[float]]:
    """Compute intraday VWAP and volume-weighted price standard deviation from today's 30-min bars."""
    if not bars_today:
        return None, None
    tps: List[float] = []
    vols: List[float] = []
    for b in bars_today:
        tp = (float(b.high) + float(b.low) + float(b.close)) / 3.0
        v = max(1.0, float(b.volume))
        if tp > 0:
            tps.append(tp)
            vols.append(v)
    tot_v = sum(vols)
    if tot_v <= 0 or not tps:
        return None, None
    vwap = sum(tp * v for tp, v in zip(tps, vols)) / tot_v
    var = sum(v * ((tp - vwap) ** 2) for tp, v in zip(tps, vols)) / tot_v
    return vwap, math.sqrt(max(0.0, var))


def has_recent_news_veto(
    snapshot: MarketSnapshot,
    ticker: str,
    keywords: Optional[List[str]] = None,
    lookback_hours: float = 24.0,
) -> bool:
    kws = [k.lower() for k in (keywords or load_part_b_params().get("common_news_veto_keywords", []))]
    sym_u = ticker.upper()
    dec_dt = parse_iso_utc(snapshot.decision_timestamp)
    cutoff = dec_dt - timedelta(hours=lookback_hours)
    for item in snapshot.news or []:
        if sym_u not in {s.upper() for s in (item.symbols or [])}:
            continue
        try:
            cdt = parse_iso_utc(item.created_at)
            if cdt < cutoff or cdt > dec_dt:
                continue
        except Exception:
            pass
        hl = (item.headline or "").lower()
        if any(kw in hl for kw in kws):
            return True
    return False


def reset_all_part_b_states(state_dir: Path = STATE_DIR) -> None:
    p = Path(state_dir) / PART_B_STATE_FILENAME
    if p.exists():
        p.unlink()


class BasePartBAgent(BaseAgent):
    """Base class for deterministic Part B quantitative strategies (#16–#35)."""

    def __init__(
        self,
        spec: Optional[AgentSpec] = None,
        state_dir: Path = STATE_DIR,
        params_path: Path = PART_B_PARAMS_PATH,
    ) -> None:
        super().__init__(spec)
        self.state_dir = Path(state_dir)
        self.params_path = Path(params_path)
        all_params = load_part_b_params(self.params_path)
        self.cfg: Dict[str, Any] = dict(all_params.get(self.agent_id, {}))
        self.common_veto_keywords: List[str] = list(all_params.get("common_news_veto_keywords", []))

    def _state_file(self) -> Path:
        return self.state_dir / PART_B_STATE_FILENAME

    def load_mem(self) -> Dict[str, Any]:
        p = self._state_file()
        if p.exists():
            try:
                all_data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(all_data, dict) and isinstance(all_data.get(self.agent_id), dict):
                    return dict(all_data[self.agent_id])
            except Exception:
                pass
        return {}

    def save_mem(self, mem: Dict[str, Any]) -> None:
        p = self._state_file()
        all_data: Dict[str, Any] = {}
        if p.exists():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    all_data = loaded
            except Exception:
                all_data = {}
        all_data[self.agent_id] = mem
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(all_data, indent=2), encoding="utf-8")

    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        batch = self.decide_batch(snapshot, state)
        return batch[0] if batch else self.hold_signal(snapshot, reasoning="HOLD_NO_ACTION")

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        raise NotImplementedError

    def build_rebalance_signals(
        self,
        snapshot: MarketSnapshot,
        state: AgentLedgerState,
        target_weights: Dict[str, float],
        stop_pct: float,
        reason_prefix: str,
        take_profit_pct: Optional[float] = None,
    ) -> List[OrderSignal]:
        """Emit deterministic SELL (non-target) followed by BUY (new/underweight targets) orders."""
        signals: List[OrderSignal] = []
        equity = float(state.equity) if state.equity > 0 else 10_000.0
        target_set = {k.upper(): float(v) for k, v in target_weights.items() if float(v) > 0}

        # 1. Sell any held ticker not in target_set
        for sym in sorted(state.positions.keys()):
            pos = state.positions[sym]
            if pos.shares > 0 and sym not in target_set:
                signals.append(
                    OrderSignal(
                        agent_id=self.agent_id,
                        action="SELL",
                        ticker=sym,
                        stop_loss=None,
                        take_profit=None,
                        confidence=0.80,
                        reasoning=f"EXIT_{reason_prefix}: rotated out of target portfolio",
                        requested_shares=pos.shares,
                        decision_timestamp=snapshot.decision_timestamp,
                    )
                )

        # 2. Buy target tickers not currently held (or materially underweight)
        for sym, w in sorted(target_set.items(), key=lambda kv: (-kv[1], kv[0])):
            px = get_last_price(snapshot, sym)
            if px <= 0:
                continue
            existing = state.positions.get(sym)
            cur_val = float(existing.shares) * px if (existing and existing.shares > 0) else 0.0
            desired_val = equity * min(0.195, w)
            deficit = desired_val - cur_val
            if deficit < max(50.0, equity * 0.03):
                continue
            shares = math.floor((deficit / px) * 10000.0) / 10000.0
            if shares <= 0:
                continue
            stop_px = round(px * (1.0 - max(0.005, stop_pct)), 4)
            tp_px = round(px * (1.0 + take_profit_pct), 4) if take_profit_pct else None
            signals.append(
                OrderSignal(
                    agent_id=self.agent_id,
                    action="BUY",
                    ticker=sym,
                    stop_loss=stop_px,
                    take_profit=tp_px,
                    confidence=0.75,
                    reasoning=f"ENTRY_{reason_prefix}: target_weight={w*100:.1f}%, stop=-{stop_pct*100:.1f}%",
                    requested_shares=shares,
                    decision_timestamp=snapshot.decision_timestamp,
                )
            )

        if not signals:
            return [self.hold_signal(snapshot, reasoning=f"HOLD_{reason_prefix}: portfolio aligned")]
        return signals
