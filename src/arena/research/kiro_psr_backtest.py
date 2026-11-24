"""Locked-Parameter 2-Year Historical Pre-Validation Backtest for Agent #15 (`kiro_pair_reversion`, PSR).

Hard rules enforced (Handoff Section A11):
- Uses ONLY the locked parameters in `kiro_pair_reversion_params.json`. Zero parameter tuning or grid search.
- Strict No Look-Ahead: daily pair stats & regime computed strictly on completed D-1 bars; fills execute at next
  trading day's open + half-spread + 5 bps slippage.
- Reports total return, SPY benchmark return, MDD, annualized Sharpe, closed trades count, win rate, average R,
  exact exit-reason distribution, rolling 30-trading-day window statistics, and honest disclosure of daily-bar
  approximation caveats (no 30-min bar confirmation, no historical Alpaca news feed, survivorship bias).
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arena.agents.kiro_pair_reversion_agent import (
    PARAMS_PATH,
    classify_psr_regime,
    compute_daily_pair_cache,
    compute_dd_multiplier,
    load_kiro_psr_params,
)
from arena.config import DEFAULT_SPREAD_BPS, INITIAL_CASH, SLIPPAGE_RATE, STATE_DIR
from arena.models import Bar
from arena.research.claude_rmp_backtest import _build_daily_bar_objects, _load_sector_map, _quantile
from arena.research.market_history import MarketHistoryCache

PREVALIDATION_OUTPUT_PATH = STATE_DIR / "kiro_psr_prevalidation.json"


def _precompute_daily_caches(
    eval_indices: List[int],
    spy_dates: List[str],
    daily_bars_by_sym: Dict[str, List[Bar]],
    params: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Pre-compute D-1 daily pair caches ONCE per date (strictly < d_str, zero look-ahead)."""
    # Build fast date-index pointers per symbol
    date_lists = {sym: [b.timestamp[:10] for b in bars] for sym, bars in daily_bars_by_sym.items()}
    ptrs = {sym: 0 for sym in daily_bars_by_sym}
    caches: Dict[str, Dict[str, Any]] = {}

    for d_idx in eval_indices:
        d_str = spy_dates[d_idx]
        prior_bars: Dict[str, List[Bar]] = {}
        for sym, bars in daily_bars_by_sym.items():
            dl = date_lists[sym]
            p = ptrs[sym]
            while p < len(dl) and dl[p] < d_str:
                p += 1
            ptrs[sym] = p
            # Only pass the trailing 80 completed bars (< d_str) needed for 60d cov/corr & 14d Wilder ATR
            start_p = max(0, p - 80)
            prior_bars[sym] = bars[start_p:p]
        caches[d_str] = compute_daily_pair_cache(prior_bars, trading_date_et=d_str, params=params)
    return caches


def _simulate_psr_span(
    day_indices: List[int],
    spy_dates: List[str],
    bar_by_sym_date: Dict[str, Dict[str, Bar]],
    daily_cache_by_date: Dict[str, Dict[str, Any]],
    sector_map: Dict[str, str],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """Simulate locked PSR rules over a sequence of trading day indices using daily OHLCV bars."""
    if len(day_indices) < 2:
        return {}

    half_spread = (DEFAULT_SPREAD_BPS / 2.0) / 10_000.0
    cost_bps = DEFAULT_SPREAD_BPS + 2.0 * float(params["filters"]["slippage_bps_per_side"])
    sqrt_w = math.sqrt(float(params["stats"]["spread_window_days"]))
    flt = params["filters"]
    rk = params["risk"]
    ent = params["entry"]
    ex = params["exit"]

    cash = INITIAL_CASH
    peak_eq = INITIAL_CASH
    max_dd = 0.0
    blown_up = False

    # symbol -> {shares, entry_px, stop, tp, stop_pct, pair_id, leg, entry_day_pos}
    positions: Dict[str, Dict[str, Any]] = {}
    t_cd: Dict[str, int] = {}
    p_cd: Dict[str, int] = {}
    closed_trades: List[Dict[str, Any]] = []
    exit_counts: Dict[str, int] = {}
    daily_equities: List[float] = [INITIAL_CASH]
    daily_exposures: List[float] = []

    is_30d_window = len(day_indices) == 30

    for pos_in_span, d_idx in enumerate(day_indices):
        d_str = spy_dates[d_idx]
        day_num = pos_in_span + 1

        cache = daily_cache_by_date.get(d_str) or {}
        if not cache.get("valid"):
            continue

        spy_bar = bar_by_sym_date.get("SPY", {}).get(d_str)
        if spy_bar is None or spy_bar.close <= 0:
            continue

        spy_prev = float(cache["spy"]["prev_close"])
        spy_intraday = (spy_bar.close / spy_prev - 1.0) if spy_prev > 0 else 0.0
        regime_name, reg_cfg = classify_psr_regime(spy_intraday, cache["spy"], params)

        # 1. Check intrabar Stop-Loss & Take-Profit against day's OHLC (next-day bar realism)
        for sym in list(positions.keys()):
            p = positions[sym]
            b = bar_by_sym_date.get(sym, {}).get(d_str)
            if b is None:
                continue
            hit_code: Optional[str] = None
            raw_fill = 0.0
            if b.low <= p["stop"]:
                hit_code = "EXIT_STOP_LOSS"
                raw_fill = min(b.open, p["stop"])
            elif b.high >= p["tp"]:
                hit_code = "EXIT_TP_PRICE"
                raw_fill = max(b.open, p["tp"])

            if hit_code:
                fill_px = raw_fill * (1.0 - half_spread) * (1.0 - SLIPPAGE_RATE)
                pnl = (fill_px - p["entry_px"]) * p["shares"]
                cash += fill_px * p["shares"]
                risk_d = max(1e-6, p["entry_px"] * p["stop_pct"] * p["shares"])
                closed_trades.append({"ticker": sym, "pnl": pnl, "r_mult": pnl / risk_d, "reason": hit_code})
                exit_counts[hit_code] = exit_counts.get(hit_code, 0) + 1
                t_cd[sym] = pos_in_span + int(ent["ticker_cooldown_days"])
                if hit_code == "EXIT_STOP_LOSS":
                    p_cd[p["pair_id"]] = pos_in_span + int(ent["pair_cooldown_days_after_loss"])
                positions.pop(sym, None)

        # Compute live z at day close for signal exits & new entries (fills execute at next-day open!)
        pairs_cache = cache["pairs"]
        stock_stats = cache["stocks"]
        pair_z_live: Dict[str, Optional[float]] = {}
        for pid, pinfo in pairs_cache.items():
            if not pinfo.get("active"):
                pair_z_live[pid] = None
                continue
            ba = bar_by_sym_date.get(pinfo["a"], {}).get(d_str)
            bb = bar_by_sym_date.get(pinfo["b"], {}).get(d_str)
            pa0 = float((stock_stats.get(pinfo["a"]) or {}).get("prev_close") or 0.0)
            pb0 = float((stock_stats.get(pinfo["b"]) or {}).get("prev_close") or 0.0)
            if not ba or not bb or pa0 <= 0 or pb0 <= 0:
                pair_z_live[pid] = None
                continue
            e_today = math.log(ba.close / pa0) - float(pinfo["beta"]) * math.log(bb.close / pb0)
            pair_z_live[pid] = (float(pinfo["s_9"]) + e_today) / (float(pinfo["sigma_e"]) * sqrt_w)

        # Next trading day open for realistic no-look-ahead signal fill
        next_d_str = spy_dates[day_indices[pos_in_span + 1]] if pos_in_span + 1 < len(day_indices) else d_str

        # 2. Breakeven stop ratchet & Signal exits (Priority 4..10)
        for sym in list(positions.keys()):
            p = positions[sym]
            b = bar_by_sym_date.get(sym, {}).get(d_str)
            if b is None:
                continue
            if b.high >= p["entry_px"] * (1.0 + p["stop_pct"]):
                p["stop"] = max(p["stop"], p["entry_px"] * (1.0 + cost_bps / 10_000.0))

            pinfo = pairs_cache.get(p["pair_id"]) or {}
            raw_z = pair_z_live.get(p["pair_id"])
            lz_now = (raw_z if p["leg"] == "A" else -raw_z) if raw_z is not None else None
            held_days = pos_in_span - p["entry_day_pos"]
            unrealized = (b.close - p["entry_px"]) * p["shares"]

            exit_code: Optional[str] = None
            if is_30d_window and day_num >= int(ex["final_day"]):
                exit_code = "EXIT_FINAL_DAY"
            elif lz_now is not None and lz_now <= float(ex["z_stop"]):
                exit_code = "EXIT_Z_STOP"
            elif regime_name == "CRASH" and unrealized < 0:
                exit_code = "EXIT_CRASH"
            elif lz_now is not None and lz_now >= float(ex["z_take_profit"]):
                exit_code = "EXIT_TP_REVERT"
            elif pinfo and pinfo.get("active") and float(pinfo.get("rho60", 1.0)) < float(flt["rho_exit_break"]):
                exit_code = "EXIT_CORR_BREAK"
            elif held_days >= int(ex["max_hold_days"]):
                exit_code = "EXIT_TIME"

            if exit_code:
                nb = bar_by_sym_date.get(sym, {}).get(next_d_str) or b
                raw_px = nb.open if nb.open > 0 else b.close
                fill_px = raw_px * (1.0 - half_spread) * (1.0 - SLIPPAGE_RATE)
                pnl = (fill_px - p["entry_px"]) * p["shares"]
                cash += fill_px * p["shares"]
                risk_d = max(1e-6, p["entry_px"] * p["stop_pct"] * p["shares"])
                closed_trades.append({"ticker": sym, "pnl": pnl, "r_mult": pnl / risk_d, "reason": exit_code})
                exit_counts[exit_code] = exit_counts.get(exit_code, 0) + 1
                t_cd[sym] = pos_in_span + int(ent["ticker_cooldown_days"])
                if exit_code == "EXIT_Z_STOP":
                    p_cd[p["pair_id"]] = pos_in_span + int(ent["pair_cooldown_days_after_loss"])
                positions.pop(sym, None)

        # Mark-to-market equity
        pos_val = sum(
            p["shares"] * (bar_by_sym_date.get(sym, {}).get(d_str).close if bar_by_sym_date.get(sym, {}).get(d_str) else p["entry_px"])
            for sym, p in positions.items()
        )
        equity = cash + pos_val
        peak_eq = max(peak_eq, equity)
        dd_pct = ((equity - peak_eq) / peak_eq) * 100.0 if peak_eq > 0 else 0.0
        max_dd = min(max_dd, dd_pct)
        if dd_pct <= -10.0:
            blown_up = True
        daily_equities.append(equity)
        daily_exposures.append((pos_val / equity * 100.0) if equity > 0 else 0.0)

        # 3. Entries (skip on last day of span or blown up or late 30d tournament)
        if blown_up or pos_in_span + 1 >= len(day_indices):
            continue
        if is_30d_window and day_num > int(ent["last_entry_trading_day"]):
            continue
        if regime_name == "CRASH":
            continue

        m_dd = compute_dd_multiplier(peak_eq, equity, params)
        max_slots = min(int(reg_cfg["max_slots"]), int(rk["max_positions"]))
        if m_dd <= 0 or len(positions) >= max_slots:
            continue

        held_pairs = {p["pair_id"] for p in positions.values()}
        cands: List[Dict[str, Any]] = []
        z_tp = abs(float(ex["z_take_profit"]))
        for pid, pinfo in sorted(pairs_cache.items()):
            if not pinfo.get("active") or pair_z_live.get(pid) is None:
                continue
            rho60 = float(pinfo["rho60"])
            z_val = float(pair_z_live[pid])  # type: ignore[arg-type]
            if rho60 < float(flt["rho_min_entry"]):
                continue
            if not (float(flt["z_entry_min"]) <= abs(z_val) <= float(flt["z_entry_max"])):
                continue
            if float(pinfo["shock_max"]) >= float(flt["shock_max"]):
                continue
            laggard, leg, lz = (pinfo["a"], "A", z_val) if z_val <= -float(flt["z_entry_min"]) else (pinfo["b"], "B", -z_val)
            if laggard in positions or pid in held_pairs:
                continue
            if pos_in_span < int(t_cd.get(laggard, -1)) or pos_in_span < int(p_cd.get(pid, -1)):
                continue
            sigma_e = float(pinfo["sigma_e"])
            g_bps = 10_000.0 * float(flt["laggard_share_of_reversion"]) * (abs(lz) - z_tp) * sigma_e * sqrt_w
            if g_bps < float(flt["min_edge_to_cost_ratio"]) * cost_bps:
                continue
            st_stat = stock_stats.get(laggard) or {}
            if not st_stat.get("atr14_d"):
                continue
            cands.append(
                {
                    "pair_id": pid,
                    "laggard": laggard,
                    "leg": leg,
                    "lz": lz,
                    "sigma_e": sigma_e,
                    "score": abs(lz) * rho60,
                    "adv20": float(st_stat.get("adv20") or 0.0),
                    "atr14_d": float(st_stat["atr14_d"]),
                }
            )

        best_by_sym: Dict[str, Dict[str, Any]] = {}
        for c in cands:
            s = c["laggard"]
            if s not in best_by_sym or c["score"] > best_by_sym[s]["score"]:
                best_by_sym[s] = c
        ranked = sorted(best_by_sym.values(), key=lambda c: (-c["score"], -c["adv20"], c["laggard"]))

        sec_counts: Dict[str, int] = {}
        cur_open_risk = 0.0
        for osym, opos in positions.items():
            sec = sector_map.get(osym, "Unknown")
            sec_counts[sec] = sec_counts.get(sec, 0) + 1
            ob = bar_by_sym_date.get(osym, {}).get(d_str)
            opx = ob.close if ob else opos["entry_px"]
            cur_open_risk += max(0.0, opx - opos["stop"]) * opos["shares"]

        for cand in ranked:
            sym = cand["laggard"]
            sec = sector_map.get(sym, "Unknown")
            if sec_counts.get(sec, 0) >= int(ent["max_positions_per_sector"]):
                continue
            nb = bar_by_sym_date.get(sym, {}).get(next_d_str)
            if nb is None or nb.open <= 0:
                continue
            fill_px = nb.open * (1.0 + half_spread) * (1.0 + SLIPPAGE_RATE)
            stop_pct = max(float(rk["stop_pct_min"]), min(float(rk["stop_pct_max"]), float(rk["stop_atr_mult"]) * cand["atr14_d"] / fill_px))
            tp_pct = max(float(rk["tp_pct_min"]), min(float(rk["tp_pct_max"]), (abs(cand["lz"]) - z_tp) * cand["sigma_e"] * sqrt_w))
            stop_px = fill_px * (1.0 - stop_pct)
            tp_px = fill_px * (1.0 + tp_pct)

            rem_gross = max(0.0, equity * float(reg_cfg["max_gross"]) - pos_val)
            risk_pct = float(reg_cfg["base_risk"]) * m_dd
            raw_sh = (equity * risk_pct) / (fill_px * stop_pct)
            raw_sh = min(raw_sh, (equity * float(rk["max_weight"])) / fill_px, rem_gross / fill_px, cash / fill_px)
            shares = math.floor(max(0.0, raw_sh) * 10000.0) / 10000.0
            if shares * fill_px < float(ent["min_order_usd"]):
                continue
            if cur_open_risk + (fill_px - stop_px) * shares > equity * float(rk["max_open_risk"]):
                continue

            cash -= shares * fill_px
            positions[sym] = {
                "shares": shares,
                "entry_px": fill_px,
                "stop": stop_px,
                "tp": tp_px,
                "stop_pct": stop_pct,
                "pair_id": cand["pair_id"],
                "leg": cand["leg"],
                "entry_day_pos": pos_in_span + 1,
            }
            break  # 1 entry per day approximation (matches conservative cycle pacing)

    # Final mark-to-market return & Sharpe
    final_eq = daily_equities[-1]
    ret_pct = ((final_eq - INITIAL_CASH) / INITIAL_CASH) * 100.0
    spy_start = bar_by_sym_date["SPY"][spy_dates[day_indices[0]]].open
    spy_end = bar_by_sym_date["SPY"][spy_dates[day_indices[-1]]].close
    spy_ret_pct = ((spy_end - spy_start) / spy_start) * 100.0 if spy_start > 0 else 0.0

    daily_rets = [
        (daily_equities[i] / daily_equities[i - 1]) - 1.0
        for i in range(1, len(daily_equities))
        if daily_equities[i - 1] > 0
    ]
    if len(daily_rets) >= 2:
        mean_r = sum(daily_rets) / len(daily_rets)
        sd_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1))
        sharpe = (mean_r / sd_r) * math.sqrt(252.0) if sd_r > 1e-9 else 0.0
    else:
        sharpe = 0.0

    wins = [t for t in closed_trades if t["pnl"] > 0]
    avg_r = (sum(t["r_mult"] for t in closed_trades) / len(closed_trades)) if closed_trades else 0.0
    win_rate = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0

    return {
        "return_pct": round(ret_pct, 2),
        "spy_return_pct": round(spy_ret_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_annualized": round(sharpe, 2),
        "blown_up": blown_up,
        "closed_trades_count": len(closed_trades),
        "win_rate_pct": round(win_rate, 1),
        "avg_r_per_closed_trade": round(avg_r, 3),
        "avg_exposure_pct": round(sum(daily_exposures) / len(daily_exposures), 1) if daily_exposures else 0.0,
        "exit_reason_counts": exit_counts,
    }


def run_kiro_psr_prevalidation(
    offline: bool = True,
    output_path: Path = PREVALIDATION_OUTPUT_PATH,
) -> Dict[str, Any]:
    """Run locked-param 2-year continuous + rolling 30-day window pre-validation backtest."""
    params = load_kiro_psr_params()
    pair_tickers = sorted({sym.upper() for pair in params["pairs"] for sym in pair} | {params["regime_ticker"]})

    market = MarketHistoryCache(offline=offline)
    daily_bars_by_sym = _build_daily_bar_objects(market, pair_tickers)
    sector_map = _load_sector_map()

    spy_bars = daily_bars_by_sym.get("SPY") or []
    spy_dates = [b.timestamp[:10] for b in spy_bars]
    bar_by_sym_date: Dict[str, Dict[str, Bar]] = {
        sym: {b.timestamp[:10]: b for b in bars} for sym, bars in daily_bars_by_sym.items()
    }

    # Evaluate over the last ~2 years (~504 trading days) with 75-day warmup before start
    total_days = len(spy_dates)
    start_idx = max(75, total_days - 504)
    eval_indices = list(range(start_idx, total_days))

    daily_cache_by_date = _precompute_daily_caches(
        eval_indices=eval_indices,
        spy_dates=spy_dates,
        daily_bars_by_sym=daily_bars_by_sym,
        params=params,
    )

    continuous = _simulate_psr_span(
        day_indices=eval_indices,
        spy_dates=spy_dates,
        bar_by_sym_date=bar_by_sym_date,
        daily_cache_by_date=daily_cache_by_date,
        sector_map=sector_map,
        params=params,
    )

    # Rolling 30-trading-day windows (stride 5 days for fast, comprehensive coverage)
    win_results: List[Dict[str, Any]] = []
    for s in range(start_idx, total_days - 30 + 1, 5):
        res = _simulate_psr_span(
            day_indices=list(range(s, s + 30)),
            spy_dates=spy_dates,
            bar_by_sym_date=bar_by_sym_date,
            daily_cache_by_date=daily_cache_by_date,
            sector_map=sector_map,
            params=params,
        )
        if res:
            win_results.append(res)

    w_rets = [w["return_pct"] for w in win_results]
    beat_spy_cnt = sum(1 for w in win_results if w["return_pct"] > w["spy_return_pct"])
    blown_cnt = sum(1 for w in win_results if w["blown_up"])
    worst_dd = min((w["max_drawdown_pct"] for w in win_results), default=0.0)

    report = {
        "agent_id": "kiro_pair_reversion",
        "params_sha256": hashlib.sha256(PARAMS_PATH.read_bytes()).hexdigest(),
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "backtest_period": [spy_dates[start_idx], spy_dates[-1]] if eval_indices else ["?", "?"],
        "total_trading_days": len(eval_indices),
        "continuous_summary": continuous,
        "rolling_30d_summary": {
            "windows_count": len(win_results),
            "beat_spy_windows_count": beat_spy_cnt,
            "beat_spy_windows_pct": round((beat_spy_cnt / len(win_results)) * 100.0, 1) if win_results else 0.0,
            "blown_up_count": blown_cnt,
            "worst_window_max_drawdown_pct": round(worst_dd, 2),
            "avg_exposure_pct": round(sum(w["avg_exposure_pct"] for w in win_results) / len(win_results), 1) if win_results else 0.0,
            "return_distribution_pct": {
                "min": round(min(w_rets), 2) if w_rets else 0.0,
                "p25": _quantile(w_rets, 0.25),
                "median": round(float(statistics.median(w_rets)), 2) if w_rets else 0.0,
                "mean": round(sum(w_rets) / len(w_rets), 2) if w_rets else 0.0,
                "p75": _quantile(w_rets, 0.75),
                "max": round(max(w_rets), 2) if w_rets else 0.0,
            },
        },
        "caveats": (
            "Daily-bar approximation: 30-min bar confirmation (E7) and 24h Alpaca news veto (F4 / EXIT_NEWS_VETO) "
            "are unavailable in historical daily price caches, and the 12 pairs use current large-cap constituents "
            "(survivorship bias). Parameters are 100% locked with zero optimization."
        ),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    res = run_kiro_psr_prevalidation(offline=True)
    print(json.dumps(res, indent=2))
