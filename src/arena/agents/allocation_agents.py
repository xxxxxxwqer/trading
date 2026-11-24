"""Part B-B: Asset Allocation & Risk Premia Agents (#21–#25).

- #21 dual_momentum_macro : 60-day absolute + relative momentum across SPY/QQQ/IWM/TLT/GLD (weekly)
- #22 risk_parity_3       : Inverse-volatility weighted SPY/TLT/GLD scaled to 8% annualized vol target (weekly)
- #23 min_variance_etf    : 60-day covariance minimum-variance long-only portfolio across 18 ETFs (top 5, weekly)
- #24 low_vol_anomaly     : 60-day lowest realized volatility top 5 stocks from 53 large-caps (weekly)
- #25 megacap5_hold       : Concentrated benchmark control — buys Top 5 market-cap stocks 19% each and holds
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from ..config import INDEX_AND_MACRO_ETFS, LIQUID_LARGE_CAP_STOCKS, SECTOR_ETFS
from ..models import AgentLedgerState, MarketSnapshot, OrderSignal
from .claude_rmp_agent import compute_realized_vol_annualized
from .part_b_common import (
    BasePartBAgent,
    compute_return_n,
    load_fundamentals_map,
    strict_prior_daily_bars,
)


def _should_weekly_rebalance(mem: Dict, snapshot: MarketSnapshot, interval_days: int = 5) -> Tuple[bool, int]:
    """Deterministic weekly cadence: rebalance on first cycle seen, then every `interval_days` trading days."""
    tdate = snapshot.trading_date_et
    dates: List[str] = list(mem.get("dates_seen") or [])
    if tdate and tdate not in dates:
        dates.append(tdate)
        dates.sort()
    mem["dates_seen"] = dates
    day_idx = dates.index(tdate) if tdate in dates else 0

    last_reb_date = str(mem.get("last_rebalance_date") or "")
    last_reb_idx = mem.get("last_rebalance_idx")
    if last_reb_date == tdate:
        return False, day_idx
    if last_reb_idx is None or (day_idx - int(last_reb_idx)) >= interval_days:
        return True, day_idx
    return False, day_idx


def _solve_long_only_min_variance(
    cov: List[List[float]],
    iterations: int = 60,
    ridge: float = 1e-5,
) -> List[float]:
    """Deterministic projected gradient descent onto the probability simplex to minimize w^T (Cov + ridge*I) w."""
    n = len(cov)
    if n == 0:
        return []
    reg = [[cov[i][j] + (ridge if i == j else 0.0) for j in range(n)] for i in range(n)]
    # Step size bounded by 1 / (2 * Frobenius norm)
    frob = math.sqrt(sum(reg[i][j] ** 2 for i in range(n) for j in range(n)))
    lr = 0.4 / max(frob, 1e-6)
    w = [1.0 / n] * n

    def _project_simplex(v: List[float]) -> List[float]:
        u = sorted(v, reverse=True)
        cssv = 0.0
        rho = 0
        for j, val in enumerate(u):
            cssv += val
            if val - (cssv - 1.0) / (j + 1) > 0:
                rho = j + 1
        theta = (sum(u[:rho]) - 1.0) / max(1, rho)
        return [max(0.0, x - theta) for x in v]

    for _ in range(iterations):
        grad = [2.0 * sum(reg[i][j] * w[j] for j in range(n)) for i in range(n)]
        w_next = [w[i] - lr * grad[i] for i in range(n)]
        w = _project_simplex(w_next)
    return w


class DualMomentumMacroAgent(BasePartBAgent):
    """#21 — Antonacci-style Dual Momentum across SPY, QQQ, IWM, TLT, GLD (weekly rebalance)."""

    DEFAULT_AGENT_ID = "dual_momentum_macro"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        interval = int(self.cfg.get("rebalance_interval_days", 5))
        due, day_idx = _should_weekly_rebalance(mem, snapshot, interval)
        if not due:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_DUAL_MOM: between weekly rebalance dates")]

        lb = int(self.cfg.get("lookback_days", 60))
        min_abs = float(self.cfg.get("abs_momentum_min", 0.0))
        top_n = int(self.cfg.get("top_n", 2))
        w = float(self.cfg.get("weight_per_ticker", 0.195))

        scored = []
        for sym in self.cfg.get("tickers", ["SPY", "QQQ", "IWM", "TLT", "GLD"]):
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            ret60 = compute_return_n(closes, lb)
            if ret60 is not None and ret60 > min_abs:
                scored.append((ret60, sym))

        scored.sort(key=lambda x: (-x[0], x[1]))
        targets = {sym: w for _ret, sym in scored[:top_n]}

        mem["last_rebalance_date"] = snapshot.trading_date_et
        mem["last_rebalance_idx"] = day_idx
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.05)),
            reason_prefix="DUAL_MOM",
        )


class RiskParity3Agent(BasePartBAgent):
    """#22 — Inverse-volatility risk parity across SPY, TLT, GLD scaled to 8% target annualized volatility."""

    DEFAULT_AGENT_ID = "risk_parity_3"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        interval = int(self.cfg.get("rebalance_interval_days", 5))
        due, day_idx = _should_weekly_rebalance(mem, snapshot, interval)
        if not due:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_RISK_PARITY: between weekly rebalance dates")]

        vol_lb = int(self.cfg.get("vol_lookback_days", 20))
        target_vol = float(self.cfg.get("target_vol_ann", 0.08))
        max_w = float(self.cfg.get("max_weight_per_ticker", 0.195))

        inv_vols: Dict[str, float] = {}
        vols: Dict[str, float] = {}
        for sym in self.cfg.get("tickers", ["SPY", "TLT", "GLD"]):
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            rv = compute_realized_vol_annualized(closes, vol_lb)
            if rv is not None and rv > 0.01:
                vols[sym] = rv
                inv_vols[sym] = 1.0 / rv

        if not inv_vols:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_RISK_PARITY: insufficient daily history")]

        tot_inv = sum(inv_vols.values())
        base_w = {sym: iv / tot_inv for sym, iv in inv_vols.items()}
        # Conservative upper-bound portfolio vol estimate (weighted average vol)
        est_vol = sum(base_w[s] * vols[s] for s in base_w)
        vol_scale = min(1.0, target_vol / max(est_vol, 0.01))
        targets = {sym: min(max_w, round(base_w[sym] * vol_scale, 4)) for sym in base_w}

        mem["last_rebalance_date"] = snapshot.trading_date_et
        mem["last_rebalance_idx"] = day_idx
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.045)),
            reason_prefix="RISK_PARITY",
        )


class MinVarianceETFAgent(BasePartBAgent):
    """#23 — 60-day covariance minimum-variance long-only portfolio across 18 ETFs (top 5, weekly)."""

    DEFAULT_AGENT_ID = "min_variance_etf"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        interval = int(self.cfg.get("rebalance_interval_days", 5))
        due, day_idx = _should_weekly_rebalance(mem, snapshot, interval)
        if not due:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_MIN_VAR_ETF: between weekly rebalance dates")]

        lb = int(self.cfg.get("lookback_days", 60))
        top_n = int(self.cfg.get("top_n", 5))
        ridge = float(self.cfg.get("ridge_alpha", 1e-5))
        iters = int(self.cfg.get("gd_iterations", 60))
        max_w = float(self.cfg.get("max_weight_per_ticker", 0.195))

        etfs = sorted(set(SECTOR_ETFS + INDEX_AND_MACRO_ETFS))
        rets_by_etf: Dict[str, List[float]] = {}
        for sym in etfs:
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            if len(closes) >= lb + 1:
                sl = closes[-(lb + 1) :]
                rets_by_etf[sym] = [math.log(sl[i] / sl[i - 1]) for i in range(1, len(sl))]

        valid_etfs = sorted(rets_by_etf.keys())
        if len(valid_etfs) < 3:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_MIN_VAR_ETF: insufficient ETF history")]

        m = len(valid_etfs)
        means = [sum(rets_by_etf[s]) / lb for s in valid_etfs]
        cov = [[0.0] * m for _ in range(m)]
        for i in range(m):
            ri, mi = rets_by_etf[valid_etfs[i]], means[i]
            for j in range(i, m):
                rj, mj = rets_by_etf[valid_etfs[j]], means[j]
                c_ij = sum((ri[t] - mi) * (rj[t] - mj) for t in range(lb)) / max(1, lb - 1)
                cov[i][j] = c_ij
                cov[j][i] = c_ij

        raw_w = _solve_long_only_min_variance(cov, iterations=iters, ridge=ridge)
        ranked = sorted([(raw_w[i], valid_etfs[i]) for i in range(m) if raw_w[i] > 1e-4], key=lambda x: (-x[0], x[1]))
        top_pairs = ranked[:top_n]
        tot_top = sum(x[0] for x in top_pairs) or 1.0
        targets = {sym: min(max_w, round((w / tot_top) * 0.95, 4)) for w, sym in top_pairs}

        mem["last_rebalance_date"] = snapshot.trading_date_et
        mem["last_rebalance_idx"] = day_idx
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.045)),
            reason_prefix="MIN_VAR_ETF",
        )


class LowVolAnomalyAgent(BasePartBAgent):
    """#24 — Buys the 5 lowest 60-day realized-volatility large-cap stocks (max 2 per sector, weekly)."""

    DEFAULT_AGENT_ID = "low_vol_anomaly"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        interval = int(self.cfg.get("rebalance_interval_days", 5))
        due, day_idx = _should_weekly_rebalance(mem, snapshot, interval)
        if not due:
            self.save_mem(mem)
            return [self.hold_signal(snapshot, reasoning="HOLD_LOW_VOL: between weekly rebalance dates")]

        vol_lb = int(self.cfg.get("vol_lookback_days", 60))
        top_n = int(self.cfg.get("top_n", 5))
        max_sec = int(self.cfg.get("max_per_sector", 2))
        w = float(self.cfg.get("weight_per_ticker", 0.19))
        fmap = load_fundamentals_map()

        scored = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            bars = strict_prior_daily_bars(snapshot, sym)
            closes = [float(b.close) for b in bars if b.close > 0]
            rv = compute_realized_vol_annualized(closes, vol_lb)
            if rv is not None and rv > 0.01:
                sec = str((fmap.get(sym) or {}).get("sector", "Unknown"))
                scored.append((rv, sym, sec))

        scored.sort(key=lambda x: (x[0], x[1]))
        sec_cnt: Dict[str, int] = {}
        targets: Dict[str, float] = {}
        for _rv, sym, sec in scored:
            if len(targets) >= top_n:
                break
            if sec_cnt.get(sec, 0) >= max_sec:
                continue
            targets[sym] = w
            sec_cnt[sec] = sec_cnt.get(sec, 0) + 1

        mem["last_rebalance_date"] = snapshot.trading_date_et
        mem["last_rebalance_idx"] = day_idx
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=float(self.cfg.get("stop_pct", 0.05)),
            reason_prefix="LOW_VOL",
        )


class MegaCap5HoldAgent(BasePartBAgent):
    """#25 — Control Benchmark: buys Top 5 market-cap stocks (19% each, -5% stop <= 1% risk) and holds."""

    DEFAULT_AGENT_ID = "megacap5_hold"

    def decide_batch(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> List[OrderSignal]:
        mem = self.load_mem()
        top_n = int(self.cfg.get("top_n", 5))
        w = float(self.cfg.get("weight_per_ticker", 0.19))
        stop_pct = float(self.cfg.get("stop_pct", 0.05))

        if mem.get("initialized") and len(state.positions) > 0:
            return [self.hold_signal(snapshot, reasoning="HOLD_MEGACAP5: passive buy-and-hold benchmark")]

        fmap = load_fundamentals_map()
        ranked = []
        for sym in sorted(LIQUID_LARGE_CAP_STOCKS):
            mc = (fmap.get(sym) or {}).get("market_cap")
            if mc is not None and float(mc) > 0:
                ranked.append((float(mc), sym))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        chosen = [sym for _mc, sym in ranked[:top_n]] or list(self.cfg.get("fallback_tickers", ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL"]))

        targets = {sym: w for sym in chosen[:top_n]}
        mem["initialized"] = True
        mem["tickers"] = list(targets.keys())
        self.save_mem(mem)
        return self.build_rebalance_signals(
            snapshot,
            state,
            target_weights=targets,
            stop_pct=stop_pct,
            reason_prefix="MEGACAP5",
        )
