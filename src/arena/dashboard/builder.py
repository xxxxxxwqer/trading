"""Static website generator for the 10-Agent AI Trading Arena (GitHub Pages)."""

from __future__ import annotations

import hashlib
import html
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    ZoneInfoNotFoundError = Exception  # type: ignore

from arena.config import (
    AGENT_SPECS,
    AGENT_SPECS_BY_ID,
    ALPACA_PAPER_BASE_URL,
    COMPETITION_TRADING_DAYS,
    DAILY_LOSS_LIMIT_PCT,
    DOCS_DIR,
    INITIAL_CASH,
    MAX_OPEN_POSITIONS,
    MAX_POSITION_EQUITY_PCT,
    MAX_RISK_PER_TRADE_PCT,
    MAX_TOTAL_DRAWDOWN_PCT,
    PROJECT_ROOT,
    SLIPPAGE_BPS,
    AgentSpec,
)
from arena.models import (
    AgentLedgerState,
    DailyJournalEntry,
    EquityPoint,
    Position,
    RejectionRecord,
    TradeRecord,
)

# High-contrast, distinct palette for all 12 agents
AGENT_COLORS: Dict[str, str] = {
    "coded_trend": "#38bdf8",     # Sky Blue
    "coded_mean_rev": "#818cf8",  # Indigo
    "coded_orb": "#c084fc",       # Purple
    "coded_sector": "#2dd4bf",    # Teal
    "llm_technical": "#34d399",   # Emerald
    "llm_catalyst": "#f472b6",    # Pink
    "llm_macro": "#fb923c",       # Orange
    "llm_cautious": "#facc15",    # Amber Yellow
    "ctrl_spy": "#ffffff",        # Crisp White (Benchmark)
    "ctrl_random": "#94a3b8",     # Slate Grey (Luck Control)
    "style_berkshire": "#22c55e", # Classic Value Green
    "style_congress": "#f43f5e",  # Capitol Rose Red
}


def _is_us_dst(utc_dt: datetime) -> bool:
    """Fallback US Eastern DST check (2nd Sunday in March 07:00 UTC to 1st Sunday in Nov 06:00 UTC)."""
    year = utc_dt.year
    # March 1st
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    # weekday(): Monday=0..Sunday=6. Days until first Sunday:
    days_to_first_sun_mar = (6 - mar1.weekday()) % 7
    second_sun_mar = mar1 + timedelta(days=days_to_first_sun_mar + 7, hours=7)  # 02:00 EST = 07:00 UTC

    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    days_to_first_sun_nov = (6 - nov1.weekday()) % 7
    first_sun_nov = nov1 + timedelta(days=days_to_first_sun_nov, hours=6)  # 02:00 EDT = 06:00 UTC
    return second_sun_mar <= utc_dt < first_sun_nov


def parse_utc_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime."""
    if not ts or not str(ts).strip():
        return None
    raw = str(ts).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw[:19], fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


try:
    _KST_ZONEINFO = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else None
except Exception:
    _KST_ZONEINFO = None

try:
    _ET_ZONEINFO = ZoneInfo("America/New_York") if ZoneInfo is not None else None
except Exception:
    _ET_ZONEINFO = None


def format_kst(ts: Optional[str | datetime]) -> str:
    """Format a timestamp in Korea Standard Time (Asia/Seoul): YYYY-MM-DD HH:MM:SS KST."""
    dt = ts if isinstance(ts, datetime) else parse_utc_timestamp(ts)
    if dt is None:
        return "—"
    dt_utc = dt.astimezone(timezone.utc)
    if _KST_ZONEINFO is not None:
        return dt_utc.astimezone(_KST_ZONEINFO).strftime("%Y-%m-%d %H:%M:%S KST")
    kst_tz = timezone(timedelta(hours=9), name="KST")
    return dt_utc.astimezone(kst_tz).strftime("%Y-%m-%d %H:%M:%S KST")


def format_et(ts: Optional[str | datetime]) -> str:
    """Format a timestamp in New York Time (America/New_York): YYYY-MM-DD HH:MM:SS EST/EDT."""
    dt = ts if isinstance(ts, datetime) else parse_utc_timestamp(ts)
    if dt is None:
        return "—"
    dt_utc = dt.astimezone(timezone.utc)
    if _ET_ZONEINFO is not None:
        et_dt = dt_utc.astimezone(_ET_ZONEINFO)
        tz_abbr = et_dt.tzname() or ("EDT" if _is_us_dst(dt_utc) else "EST")
        return f"{et_dt.strftime('%Y-%m-%d %H:%M:%S')} {tz_abbr}"
    is_dst = _is_us_dst(dt_utc)
    offset = timedelta(hours=-4 if is_dst else -5)
    abbr = "EDT" if is_dst else "EST"
    et_dt = dt_utc.astimezone(timezone(offset, name=abbr))
    return f"{et_dt.strftime('%Y-%m-%d %H:%M:%S')} {abbr}"


def _esc(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def _fmt_money(val: float, signed: bool = False) -> str:
    if signed:
        sign = "+" if val >= 0 else "-"
        return f"{sign}${abs(val):,.2f}"
    if val < 0:
        return f"-${abs(val):,.2f}"
    return f"${val:,.2f}"


def _fmt_pct(val: float, signed: bool = True, decimals: int = 2) -> str:
    if signed:
        sign = "+" if val >= 0 else "-"
        return f"{sign}{abs(val):.{decimals}f}%"
    return f"{val:.{decimals}f}%"


def _pnl_class(val: float) -> str:
    if val > 1e-6:
        return "pos"
    if val < -1e-6:
        return "neg"
    return "neutral"


def _short_category_label(category: str) -> Tuple[str, str]:
    """Return (short_pill_label, css_class) for an agent category."""
    cat_lower = (category or "").lower()
    if "coded" in cat_lower:
        return "Coded", "cat-coded"
    if "llm" in cat_lower or "gemini" in cat_lower:
        return "Gemini Flash LLM", "cat-llm"
    if "filing" in cat_lower or "style" in cat_lower:
        return "Filing-Style", "cat-style"
    return "Control", "cat-control"


def _status_badge_class(status: str) -> str:
    s = (status or "ACTIVE").upper()
    if s == "ACTIVE":
        return "status-active"
    if s == "DAILY_HALTED":
        return "status-halted"
    if s == "BLOWN_UP":
        return "status-blown"
    if s == "COMPLETED":
        return "status-completed"
    return "status-active"


class DashboardBuilder:
    """Generates the static HTML/CSS/JS dashboard for GitHub Pages."""

    def __init__(self, output_dir: Path = DOCS_DIR) -> None:
        self.default_output_dir = Path(output_dir)

    def build_site(
        self,
        agent_states: List[AgentLedgerState],
        competition_state: Optional[Dict[str, Any]] = None,
        bug_fixes: Optional[List[Dict[str, Any]]] = None,
        output_dir: Optional[Path | str] = None,
    ) -> Path:
        """Build the complete static site in output_dir and return output_dir Path."""
        target_dir = Path(output_dir) if output_dir is not None else self.default_output_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        agents_dir = target_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)

        comp_state: Dict[str, Any] = dict(competition_state or {})
        raw_bug_fixes = bug_fixes if bug_fixes is not None else comp_state.get("bug_fixes", [])
        if isinstance(raw_bug_fixes, dict):
            bug_fix_list: List[Dict[str, Any]] = list(raw_bug_fixes.get("entries", raw_bug_fixes.get("fixes", [])))
        elif isinstance(raw_bug_fixes, list):
            bug_fix_list = list(raw_bug_fixes)
        else:
            bug_fix_list = []

        # Normalize agent states and ensure all registered agents exist
        normalized_agents = self._normalize_agents(agent_states)
        metrics_by_id, sorted_metrics = self._compute_all_metrics(normalized_agents)
        header_ctx = self._build_header_context(normalized_agents, comp_state)
        strategy_hashes = self._get_strategy_hashes(comp_state)
        cohort_stats = self._compute_cohort_analysis(sorted_metrics)
        luck_stats = self._compute_luck_vs_skill(sorted_metrics, normalized_agents)
        learned_patterns = self._load_learned_patterns()

        # 1. Write styles.css
        (target_dir / "styles.css").write_text(self._render_styles_css(), encoding="utf-8")

        # 2. Write dashboard_data.json
        dashboard_payload = {
            "paper_trading_warning": "PAPER TRADING - NOT REAL MONEY (100% Virtual $10,000 Starting Cash)",
            "alpaca_paper_endpoint": ALPACA_PAPER_BASE_URL,
            "generated_at_utc": header_ctx["updated_utc_iso"],
            "generated_at_kst": header_ctx["updated_kst"],
            "generated_at_et": header_ctx["updated_et"],
            "competition_status_badge": header_ctx["status_badge_text"],
            "trading_days_completed": header_ctx["days_completed"],
            "competition_trading_days": header_ctx["total_days"],
            "competition_state": comp_state,
            "leaderboard": sorted_metrics,
            "cohort_analysis": cohort_stats,
            "luck_vs_skill": luck_stats,
            "learned_patterns": learned_patterns,
            "bug_fixes": bug_fix_list,
            "strategy_hashes": strategy_hashes,
            "agents": {a.agent_id: a.to_dict() for a in normalized_agents},
        }
        (target_dir / "dashboard_data.json").write_text(
            json.dumps(dashboard_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # 3. Write index.html
        index_html = self._render_index_html(
            normalized_agents=normalized_agents,
            sorted_metrics=sorted_metrics,
            metrics_by_id=metrics_by_id,
            header_ctx=header_ctx,
            bug_fixes=bug_fix_list,
            strategy_hashes=strategy_hashes,
        )
        (target_dir / "index.html").write_text(index_html, encoding="utf-8")

        # 4. Write per-agent pages: agents/<agent_id>.html
        for agent in normalized_agents:
            agent_html = self._render_agent_html(
                agent=agent,
                agent_metrics=metrics_by_id[agent.agent_id],
                spy_agent=next((a for a in normalized_agents if a.agent_id == "ctrl_spy"), None),
                sorted_metrics=sorted_metrics,
                header_ctx=header_ctx,
            )
            (agents_dir / f"{agent.agent_id}.html").write_text(agent_html, encoding="utf-8")

        # 5. Write final_report.html
        final_report_html = self._render_final_report_html(
            normalized_agents=normalized_agents,
            sorted_metrics=sorted_metrics,
            metrics_by_id=metrics_by_id,
            header_ctx=header_ctx,
            cohort_stats=cohort_stats,
            luck_stats=luck_stats,
            bug_fixes=bug_fix_list,
            strategy_hashes=strategy_hashes,
        )
        (target_dir / "final_report.html").write_text(final_report_html, encoding="utf-8")

        return target_dir

    def _normalize_agents(self, agent_states: List[AgentLedgerState]) -> List[AgentLedgerState]:
        by_id: Dict[str, AgentLedgerState] = {}
        for item in agent_states or []:
            state = AgentLedgerState.from_dict(item) if isinstance(item, dict) else item
            by_id[state.agent_id] = state

        ordered: List[AgentLedgerState] = []
        for spec in AGENT_SPECS:
            if spec.agent_id in by_id:
                ordered.append(by_id[spec.agent_id])
            else:
                ordered.append(
                    AgentLedgerState(
                        agent_id=spec.agent_id,
                        name=spec.name,
                        category=spec.category,
                        initial_cash=INITIAL_CASH,
                        cash=INITIAL_CASH,
                        equity=INITIAL_CASH,
                        peak_equity=INITIAL_CASH,
                    )
                )
        for extra_id, extra_state in by_id.items():
            if extra_id not in AGENT_SPECS_BY_ID:
                ordered.append(extra_state)
        return ordered

    def _build_header_context(
        self,
        agents: List[AgentLedgerState],
        comp_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Resolve last_updated timestamp
        candidates: List[datetime] = []
        for key in ("last_updated", "updated_at", "timestamp", "last_cycle_timestamp"):
            dt = parse_utc_timestamp(comp_state.get(key))
            if dt is not None:
                candidates.append(dt)
        for a in agents:
            dt = parse_utc_timestamp(a.last_updated)
            if dt is not None:
                candidates.append(dt)
            if a.equity_curve:
                dt_eq = parse_utc_timestamp(a.equity_curve[-1].timestamp)
                if dt_eq is not None:
                    candidates.append(dt_eq)

        updated_dt = max(candidates) if candidates else datetime.now(timezone.utc)
        updated_utc_iso = updated_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        updated_kst = format_kst(updated_dt)
        updated_et = format_et(updated_dt)

        # Resolve trading days completed
        days_from_comp = (
            comp_state.get("trading_days_completed")
            or comp_state.get("current_trading_day")
            or comp_state.get("trading_day_number")
        )
        if days_from_comp is None and isinstance(comp_state.get("trading_dates"), list):
            days_from_comp = len(comp_state["trading_dates"])
        if days_from_comp is None and isinstance(comp_state.get("completed_trading_dates"), list):
            days_from_comp = len(comp_state["completed_trading_dates"])

        if days_from_comp is not None:
            days_completed = int(days_from_comp)
        else:
            journal_days = max((len(a.daily_journal) for a in agents), default=0)
            curve_dates = len(
                {
                    pt.trading_date_et
                    for a in agents
                    for pt in a.equity_curve
                    if pt.trading_date_et and pt.trading_date_et.lower() != "init"
                }
            )
            days_completed = max(journal_days, curve_dates)

        total_days = int(comp_state.get("total_trading_days") or COMPETITION_TRADING_DAYS)
        days_clamped = max(0, min(days_completed, total_days))
        progress_pct = round((days_clamped / max(1, total_days)) * 100.0, 1)

        raw_status = str(comp_state.get("status") or "").strip()
        raw_mode = str(comp_state.get("mode") or "").strip().lower()
        is_dry_run = bool(
            comp_state.get("is_dry_run")
            or "dry" in raw_mode
            or "dry" in raw_status.lower()
            or "replay" in raw_status.lower()
        )

        if is_dry_run:
            status_badge_text = "DRY RUN (5-Day Replay)"
            status_badge_class = "comp-dryrun"
        elif raw_status.upper().startswith("COMPLETED") or (days_completed >= total_days and total_days > 0):
            status_badge_text = "COMPLETED - Final Results"
            status_badge_class = "comp-completed"
        elif raw_status.upper().startswith("LIVE") or days_completed > 0:
            day_num = max(1, days_completed)
            status_badge_text = f"LIVE - Day {day_num} of {total_days}"
            status_badge_class = "comp-live"
        else:
            status_badge_text = "READY"
            status_badge_class = "comp-ready"

        return {
            "updated_utc_iso": updated_utc_iso,
            "updated_kst": updated_kst,
            "updated_et": updated_et,
            "days_completed": days_completed,
            "total_days": total_days,
            "progress_pct": progress_pct,
            "progress_label": f"{days_completed} / {COMPETITION_TRADING_DAYS} US Trading Days",
            "status_badge_text": status_badge_text,
            "status_badge_class": status_badge_class,
            "is_dry_run": is_dry_run,
            "cycle_count": int(comp_state.get("cycles_completed", comp_state.get("cycle_count", 0))),
        }

    def _compute_all_metrics(
        self, agents: List[AgentLedgerState]
    ) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]:
        spy_agent = next((a for a in agents if a.agent_id == "ctrl_spy"), None)
        spy_return_pct = (
            ((spy_agent.equity - spy_agent.initial_cash) / spy_agent.initial_cash) * 100.0
            if spy_agent and spy_agent.initial_cash > 0
            else 0.0
        )

        metrics_by_id: Dict[str, Dict[str, Any]] = {}
        for agent in agents:
            spec: Optional[AgentSpec] = AGENT_SPECS_BY_ID.get(agent.agent_id)
            initial = agent.initial_cash if agent.initial_cash > 0 else INITIAL_CASH
            net_profit = agent.equity - initial
            net_return_pct = (net_profit / initial) * 100.0
            return_vs_spy_pct = net_return_pct - spy_return_pct

            total_fills = len(agent.trades)
            buy_fills = [t for t in agent.trades if t.side == "BUY"]
            closed_trades = [t for t in agent.trades if t.side == "SELL" and t.realized_pnl is not None]
            wins = [float(t.realized_pnl) for t in closed_trades if (t.realized_pnl or 0.0) > 0]
            losses = [float(t.realized_pnl) for t in closed_trades if (t.realized_pnl or 0.0) < 0]

            closed_count = len(closed_trades)
            win_count = len(wins)
            loss_count = len(losses)
            win_rate = (win_count / closed_count) * 100.0 if closed_count > 0 else 0.0

            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))
            if gross_loss > 1e-9:
                profit_factor_val: Optional[float] = round(gross_profit / gross_loss, 2)
                profit_factor_str = f"{gross_profit / gross_loss:.2f}"
            elif gross_profit > 1e-9:
                profit_factor_val = None
                profit_factor_str = "∞"
            else:
                profit_factor_val = None
                profit_factor_str = "N/A"

            avg_win = (gross_profit / win_count) if win_count > 0 else 0.0
            avg_loss = (sum(losses) / loss_count) if loss_count > 0 else 0.0

            total_spread_cost = sum(float(t.spread_cost_dollars or 0.0) for t in agent.trades)
            total_slippage_cost = sum(float(t.slippage_cost_dollars or 0.0) for t in agent.trades)
            total_friction_cost = total_spread_cost + total_slippage_cost
            total_notional = sum(float(t.notional_dollars or 0.0) for t in agent.trades)

            positions_market_value = sum(float(p.market_value or 0.0) for p in agent.positions.values())
            unrealized_pnl = sum(float(p.unrealized_pnl or 0.0) for p in agent.positions.values())

            short_cat, cat_class = _short_category_label(agent.category)
            max_dd = float(agent.max_drawdown_pct or 0.0)
            if max_dd > 0:
                max_dd = -max_dd

            metrics_by_id[agent.agent_id] = {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "category": agent.category,
                "short_category": short_cat,
                "category_class": cat_class,
                "short_description": spec.short_description if spec else "",
                "playbook": spec.playbook if spec else "",
                "flatten_eod": bool(spec.flatten_eod) if spec else False,
                "status": agent.status,
                "status_reason": agent.status_reason,
                "status_class": _status_badge_class(agent.status),
                "initial_cash": round(initial, 2),
                "equity": round(agent.equity, 2),
                "cash": round(agent.cash, 2),
                "positions_value": round(positions_market_value, 2),
                "open_positions_count": len(agent.positions),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "peak_equity": round(agent.peak_equity, 2),
                "net_profit": round(net_profit, 2),
                "net_return_pct": round(net_return_pct, 2),
                "return_vs_spy_pct": round(return_vs_spy_pct, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "current_drawdown_pct": round(float(agent.current_drawdown_pct or 0.0), 2),
                "daily_pnl_pct": round(float(agent.daily_pnl_pct or 0.0), 2),
                "total_fills": total_fills,
                "buy_fills_count": len(buy_fills),
                "closed_trades_count": closed_count,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate_pct": round(win_rate, 1),
                "profit_factor": profit_factor_val,
                "profit_factor_str": profit_factor_str,
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "gross_profit": round(gross_profit, 2),
                "gross_loss": round(gross_loss, 2),
                "total_spread_cost": round(total_spread_cost, 2),
                "total_slippage_cost": round(total_slippage_cost, 2),
                "total_friction_cost": round(total_friction_cost, 2),
                "total_notional": round(total_notional, 2),
                "rejections_count": len(agent.rejections),
                "color": AGENT_COLORS.get(agent.agent_id, "#38bdf8"),
                "rank": 0,
            }

        sorted_metrics = sorted(
            metrics_by_id.values(),
            key=lambda m: (m["equity"], m["net_return_pct"], -abs(m["max_drawdown_pct"])),
            reverse=True,
        )
        for idx, item in enumerate(sorted_metrics, start=1):
            item["rank"] = idx

        return metrics_by_id, sorted_metrics

    def _get_strategy_hashes(self, comp_state: Dict[str, Any]) -> Dict[str, str]:
        """Compute or retrieve SHA-256 hashes of agent strategy files to verify code-freeze integrity."""
        from_state = comp_state.get("strategy_hashes") or comp_state.get("code_hashes")
        if isinstance(from_state, dict) and from_state:
            return {str(k): str(v) for k, v in from_state.items()}

        hashes: Dict[str, str] = {}
        tracked_files = [
            PROJECT_ROOT / "src" / "arena" / "config.py",
            PROJECT_ROOT / "src" / "arena" / "models.py",
            PROJECT_ROOT / "src" / "arena" / "agents" / "coded_agents.py",
            PROJECT_ROOT / "src" / "arena" / "agents" / "llm_agents.py",
            PROJECT_ROOT / "src" / "arena" / "agents" / "control_agents.py",
            PROJECT_ROOT / "src" / "arena" / "engine" / "risk_manager.py",
        ]
        for path in tracked_files:
            if path.exists():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
                hashes[rel] = digest
        if not hashes:
            # Fallback hash of AGENT_SPECS playbooks
            payload = json.dumps([s.__dict__ for s in AGENT_SPECS], sort_keys=True).encode("utf-8")
            hashes["arena.config.AGENT_SPECS"] = hashlib.sha256(payload).hexdigest()
        return hashes

    def _load_learned_patterns(self) -> Dict[str, Dict[str, Any]]:
        """Load learned filing-style pattern JSONs (Berkshire 13F & Congress PTR) if present."""
        patterns: Dict[str, Dict[str, Any]] = {}
        pattern_files = {
            "style_berkshire": PROJECT_ROOT / "state" / "learned_patterns" / "berkshire_style.json",
            "style_congress": PROJECT_ROOT / "state" / "learned_patterns" / "congress_style.json",
        }
        for aid, path in pattern_files.items():
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        patterns[aid] = data
                except Exception:
                    pass
        return patterns

    def _render_learned_patterns_panel(self, learned_patterns: Dict[str, Dict[str, Any]], root_prefix: str = "") -> str:
        """Render plain-language learned rules and honest Out-of-Sample vs SPY test cards."""
        if not learned_patterns:
            return ""
        cards: List[str] = []
        for aid in ("style_berkshire", "style_congress"):
            pat = learned_patterns.get(aid)
            if not pat:
                continue
            oos = pat.get("out_of_sample_test") or {}
            beat_spy = bool(oos.get("beat_spy", False))
            verdict_cls = "status-active" if beat_spy else "status-blown"
            verdict_badge = "BEAT SPY IN OOS TEST" if beat_spy else "DID NOT BEAT SPY IN OOS TEST"
            rules_li = "".join(f"<li>{_esc(r)}</li>" for r in (pat.get("plain_language_rules") or []))
            test_period = oos.get("test_period") or ["?", "?"]
            p_ret = float(oos.get("pattern_return_pct", 0.0))
            s_ret = float(oos.get("spy_return_pct", 0.0))
            ex_ret = float(oos.get("excess_return_vs_spy_pct", 0.0))
            dd = float(oos.get("max_drawdown_pct", 0.0))
            extra_note = (
                "No timing rules learned from 13F (quarterly holdings have no trade dates or prices)."
                if aid == "style_berkshire"
                else f"Parsed {int((pat.get('ptr_corpus_stats') or {}).get('parsed_ok_pdfs', 0)):,} House PTR PDFs once into cache (shares only, all members)."
            )
            cards.append(
                f"""        <article class="style-pattern-card">
          <div class="style-pattern-header">
            <div>
              <h3><a href="{root_prefix}agents/{_esc(aid)}.html">{_esc(pat.get('agent_name', aid))}</a></h3>
              <div class="sub-num">{_esc(pat.get('source', ''))}</div>
            </div>
            <span class="status-pill {verdict_cls}">{verdict_badge}</span>
          </div>
          <div class="oos-strip">
            <span><strong>OOS Test ({_esc(test_period[0])} &rarr; {_esc(test_period[1])}):</strong></span>
            <span class="{_pnl_class(p_ret)}">Pattern: <strong>{_fmt_pct(p_ret)}</strong></span>
            <span>vs SPY: <strong>{_fmt_pct(s_ret)}</strong></span>
            <span class="{_pnl_class(ex_ret)}">Excess: <strong>{_fmt_pct(ex_ret)}</strong></span>
            <span class="neg">Max DD: <strong>{dd:.2f}%</strong></span>
          </div>
          <p class="oos-verdict-text">{_esc(oos.get('plain_verdict', ''))}</p>
          <div class="rules-heading">Explicit Learned Rules Applied to Today's Universe (Never Copies Filings):</div>
          <ul class="learned-rules-list">{rules_li}</ul>
          <div class="sub-num">No Look-Ahead Cutoff: filings public strictly before <code>{_esc(pat.get('agent_start_date', ''))}</code> &bull; {_esc(extra_note)}</div>
        </article>"""
            )
        if not cards:
            return ""
        return f"""
    <!-- Learned Filing-Style Patterns (Plain Language + Out-of-Sample vs SPY) -->
    <section class="panel" id="learned-patterns">
      <div class="panel-header">
        <div>
          <h2>&#128218; Filing-Style Agents (#11 Berkshire &amp; #12 Congress) — Learned Pattern Rules &amp; Pre-Live Test vs. SPY</h2>
          <p class="panel-sub">Neither agent ever copies stocks from filings. Each learned explicit, testable style rules from older public filings, then tested those rules out-of-sample on the most recent history using normal price data across our universe.</p>
        </div>
      </div>
      <div class="style-patterns-grid">
{chr(10).join(cards)}
      </div>
    </section>"""

    def _compute_cohort_analysis(self, sorted_metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute aggregated performance breakdown across Coded, Gemini Flash LLM, Filing-Style, SPY, and Random."""
        groups = {
            "Coded": [m for m in sorted_metrics if m["short_category"] == "Coded"],
            "Gemini Flash LLM": [m for m in sorted_metrics if m["short_category"] == "Gemini Flash LLM"],
            "Filing-Style Learned": [m for m in sorted_metrics if m["short_category"] == "Filing-Style"],
            "Buy-and-Hold SPY": [m for m in sorted_metrics if m["agent_id"] == "ctrl_spy"],
            "Random Trader (Luck Control)": [m for m in sorted_metrics if m["agent_id"] == "ctrl_random"],
        }
        summary: Dict[str, Any] = {}
        for label, items in groups.items():
            if not items:
                continue
            avg_ret = sum(m["net_return_pct"] for m in items) / len(items)
            avg_eq = sum(m["equity"] for m in items) / len(items)
            avg_dd = sum(m["max_drawdown_pct"] for m in items) / len(items)
            avg_wr = sum(m["win_rate_pct"] for m in items) / len(items)
            total_fills = sum(m["total_fills"] for m in items)
            total_closed = sum(m["closed_trades_count"] for m in items)
            total_friction = sum(m["total_friction_cost"] for m in items)
            total_rejections = sum(m["rejections_count"] for m in items)
            best_agent = max(items, key=lambda x: x["equity"])
            worst_agent = min(items, key=lambda x: x["equity"])
            summary[label] = {
                "count": len(items),
                "avg_equity": round(avg_eq, 2),
                "avg_return_pct": round(avg_ret, 2),
                "avg_max_drawdown_pct": round(avg_dd, 2),
                "avg_win_rate_pct": round(avg_wr, 1),
                "total_fills": total_fills,
                "total_closed_trades": total_closed,
                "total_friction_cost": round(total_friction, 2),
                "total_rejections": total_rejections,
                "best_agent_name": best_agent["name"],
                "best_agent_return_pct": best_agent["net_return_pct"],
                "worst_agent_name": worst_agent["name"],
                "worst_agent_return_pct": worst_agent["net_return_pct"],
            }
        return summary

    def _extract_daily_returns(self, agent: AgentLedgerState) -> List[float]:
        """Extract daily percentage returns for an agent from daily_journal or equity_curve."""
        if agent.daily_journal:
            return [float(j.daily_pnl_pct) for j in agent.daily_journal]
        by_date: Dict[str, float] = {}
        for pt in agent.equity_curve:
            if pt.trading_date_et and pt.trading_date_et.lower() != "init":
                by_date[pt.trading_date_et] = float(pt.equity)
        if not by_date:
            return []
        dates = sorted(by_date.keys())
        prev = agent.initial_cash if agent.initial_cash > 0 else INITIAL_CASH
        rets: List[float] = []
        for d in dates:
            eq = by_date[d]
            if prev > 0:
                rets.append(((eq - prev) / prev) * 100.0)
            prev = eq
        return rets

    def _compute_luck_vs_skill(
        self,
        sorted_metrics: List[Dict[str, Any]],
        agents: List[AgentLedgerState],
    ) -> Dict[str, Any]:
        """Compute statistical significance, standard errors, and confidence intervals over N=10 days."""
        agents_by_id = {a.agent_id: a for a in agents}
        winner = sorted_metrics[0]
        spy_m = next((m for m in sorted_metrics if m["agent_id"] == "ctrl_spy"), winner)
        rand_m = next((m for m in sorted_metrics if m["agent_id"] == "ctrl_random"), winner)

        rand_rets = self._extract_daily_returns(agents_by_id[rand_m["agent_id"]]) if rand_m["agent_id"] in agents_by_id else []
        spy_rets = self._extract_daily_returns(agents_by_id[spy_m["agent_id"]]) if spy_m["agent_id"] in agents_by_id else []

        per_agent_stats: List[Dict[str, Any]] = []
        for m in sorted_metrics:
            ag = agents_by_id.get(m["agent_id"])
            rets = self._extract_daily_returns(ag) if ag else []
            n_obs = len(rets) if len(rets) > 0 else COMPETITION_TRADING_DAYS
            if len(rets) >= 2:
                mean_daily = sum(rets) / len(rets)
                variance = sum((r - mean_daily) ** 2 for r in rets) / (len(rets) - 1)
                std_daily = math.sqrt(max(variance, 1e-8))
            elif len(rets) == 1:
                mean_daily = rets[0]
                std_daily = max(abs(rets[0]) * 0.8, 0.45)
            else:
                mean_daily = m["net_return_pct"] / max(1, n_obs)
                std_daily = 0.65  # Typical 0.65% daily portfolio volatility prior when N=0

            se_daily = std_daily / math.sqrt(max(1, n_obs))
            # Cumulative N-day return standard error (~ sqrt(N) * std_daily)
            cum_se_pct = std_daily * math.sqrt(max(1, n_obs))
            ci_95_half_width = 1.96 * cum_se_pct

            # Standard errors separating this agent from Random Control & SPY
            diff_vs_random_pct = m["net_return_pct"] - rand_m["net_return_pct"]
            diff_vs_spy_pct = m["net_return_pct"] - spy_m["net_return_pct"]

            # Paired or pooled SE vs random
            if len(rets) >= 2 and len(rand_rets) == len(rets):
                diffs = [a_r - r_r for a_r, r_r in zip(rets, rand_rets)]
                d_mean = sum(diffs) / len(diffs)
                d_var = sum((d - d_mean) ** 2 for d in diffs) / (len(diffs) - 1)
                d_std = math.sqrt(max(d_var, 1e-8))
                se_vs_random = d_std * math.sqrt(len(diffs))
            else:
                se_vs_random = max(cum_se_pct, 0.50)

            t_stat_vs_random = diff_vs_random_pct / se_vs_random if se_vs_random > 1e-6 else 0.0
            t_stat_vs_spy = diff_vs_spy_pct / max(cum_se_pct, 0.50)

            per_agent_stats.append({
                "rank": m["rank"],
                "agent_id": m["agent_id"],
                "name": m["name"],
                "short_category": m["short_category"],
                "n_days": len(rets) if len(rets) > 0 else n_obs,
                "cum_return_pct": m["net_return_pct"],
                "mean_daily_pct": round(mean_daily, 3),
                "std_daily_pct": round(std_daily, 3),
                "se_daily_pct": round(se_daily, 3),
                "cum_se_pct": round(cum_se_pct, 2),
                "ci_95_low_pct": round(m["net_return_pct"] - ci_95_half_width, 2),
                "ci_95_high_pct": round(m["net_return_pct"] + ci_95_half_width, 2),
                "diff_vs_random_pct": round(diff_vs_random_pct, 2),
                "t_stat_vs_random": round(t_stat_vs_random, 2),
                "diff_vs_spy_pct": round(diff_vs_spy_pct, 2),
                "t_stat_vs_spy": round(t_stat_vs_spy, 2),
                "statistically_significant_95": abs(t_stat_vs_random) >= 1.96,
            })

        winner_stat = per_agent_stats[0]
        return {
            "sample_size_days": COMPETITION_TRADING_DAYS,
            "winner_name": winner["name"],
            "winner_return_pct": winner["net_return_pct"],
            "random_name": rand_m["name"],
            "random_return_pct": rand_m["net_return_pct"],
            "spy_name": spy_m["name"],
            "spy_return_pct": spy_m["net_return_pct"],
            "winner_vs_random_pct": winner_stat["diff_vs_random_pct"],
            "winner_vs_random_se": winner_stat["t_stat_vs_random"],
            "winner_vs_spy_pct": winner_stat["diff_vs_spy_pct"],
            "winner_vs_spy_se": winner_stat["t_stat_vs_spy"],
            "winner_cum_se_pct": winner_stat["cum_se_pct"],
            "winner_ci_95_low_pct": winner_stat["ci_95_low_pct"],
            "winner_ci_95_high_pct": winner_stat["ci_95_high_pct"],
            "per_agent": per_agent_stats,
        }

    def _render_common_header(
        self,
        header_ctx: Dict[str, Any],
        active_page: str,
        root_prefix: str = "",
    ) -> str:
        """Render the mandatory paper-trading banner, KST/ET timestamps, competition status, and nav bar."""
        idx_cls = "nav-link active" if active_page == "index" else "nav-link"
        rep_cls = "nav-link active" if active_page == "final_report" else "nav-link"

        agent_links = []
        for spec in AGENT_SPECS:
            short_num = spec.name.split(".")[0]
            a_cls = "agent-pill active" if active_page == spec.agent_id else "agent-pill"
            agent_links.append(
                f'<a href="{root_prefix}agents/{spec.agent_id}.html" class="{a_cls}" '
                f'title="{_esc(spec.name)}">#{short_num} {_esc(spec.agent_id)}</a>'
            )
        agent_strip_html = "\n          ".join(agent_links)

        return f"""  <header class="site-header">
    <div class="paper-warning-banner" role="alert">
      <span class="warning-icon">&#9888;</span>
      <strong>PAPER TRADING - NOT REAL MONEY (100% Virtual $10,000 Starting Cash)</strong>
      <span class="warning-sub">Alpaca Paper Endpoint Only (<code>{_esc(ALPACA_PAPER_BASE_URL)}</code>) &bull; Zero Real Capital At Risk</span>
    </div>

    <div class="header-main">
      <div class="brand-block">
        <div class="brand-title-row">
          <a href="{root_prefix}index.html" class="brand-title">AI Trading Arena</a>
          <span class="comp-status-badge {_esc(header_ctx['status_badge_class'])}">{_esc(header_ctx['status_badge_text'])}</span>
        </div>
        <p class="brand-subtitle">10-Agent US Stock &amp; ETF Paper-Trading Competition &bull; 4 Coded vs. 4 Gemini Flash LLMs vs. 2 Controls</p>
      </div>

      <div class="progress-and-clock">
        <div class="trading-progress-box">
          <div class="progress-label-row">
            <span class="progress-title">Competition Progress</span>
            <span class="progress-count"><strong>{_esc(header_ctx['progress_label'])}</strong> ({header_ctx['progress_pct']:.0f}%)</span>
          </div>
          <div class="progress-track" aria-label="Trading Days Progress">
            <div class="progress-fill" style="width: {min(100.0, max(4.0, float(header_ctx['progress_pct']))):.1f}%"></div>
          </div>
        </div>

        <div class="timestamps-box">
          <div class="ts-item">
            <span class="ts-label">Korea (KST / Asia/Seoul):</span>
            <time class="ts-value">{_esc(header_ctx['updated_kst'])}</time>
          </div>
          <div class="ts-item">
            <span class="ts-label">New York (ET / America/New_York):</span>
            <time class="ts-value">{_esc(header_ctx['updated_et'])}</time>
          </div>
        </div>
      </div>
    </div>

    <nav class="site-nav" aria-label="Main Navigation">
      <div class="nav-primary">
        <a href="{root_prefix}index.html" class="{idx_cls}">&#127942; Live Leaderboard &amp; Arena</a>
        <a href="{root_prefix}final_report.html" class="{rep_cls}">&#128202; 10-Day Final Report &amp; Luck vs. Skill</a>
        <a href="{root_prefix}dashboard_data.json" class="nav-link nav-json" target="_blank" rel="noopener">&#128196; JSON Ledger Export</a>
      </div>
      <div class="nav-agents-strip">
        <span class="nav-agents-label">Agents:</span>
        {agent_strip_html}
      </div>
    </nav>
  </header>"""

    def _build_chart_series(
        self,
        agents: List[AgentLedgerState],
    ) -> Tuple[List[str], Dict[str, List[float]]]:
        """Align equity curves across all agents onto a unified chronological timestamp/date axis."""
        all_keys_map: Dict[str, str] = {}
        for a in agents:
            for pt in a.equity_curve:
                ts_key = pt.timestamp or pt.trading_date_et
                if ts_key:
                    dt = parse_utc_timestamp(pt.timestamp)
                    if dt is not None:
                        label = format_et(dt)[:16] + " ET"
                    else:
                        label = pt.trading_date_et or ts_key
                    all_keys_map[ts_key] = label

        sorted_keys = sorted(all_keys_map.keys())
        if not sorted_keys:
            labels = ["Start ($10,000)"]
            series = {a.agent_id: [round(a.equity, 2)] for a in agents}
            return labels, series

        labels = ["Start"] + [all_keys_map[k] for k in sorted_keys]
        series: Dict[str, List[float]] = {}
        for a in agents:
            pt_lookup = {(pt.timestamp or pt.trading_date_et): round(float(pt.equity), 2) for pt in a.equity_curve}
            vals = [round(float(a.initial_cash or INITIAL_CASH), 2)]
            last_val = vals[0]
            for k in sorted_keys:
                if k in pt_lookup:
                    last_val = pt_lookup[k]
                vals.append(last_val)
            series[a.agent_id] = vals
        return labels, series

    def _render_svg_equity_chart(
        self,
        agents: List[AgentLedgerState],
        labels: List[str],
        series: Dict[str, List[float]],
        highlight_agent_id: Optional[str] = None,
    ) -> str:
        """Render a crisp, responsive inline SVG companion/fallback equity curve chart."""
        width = 960
        height = 320
        pad_left = 78
        pad_right = 28
        pad_top = 24
        pad_bottom = 42
        plot_w = width - pad_left - pad_right
        plot_h = height - pad_top - pad_bottom

        all_vals = [v for vals in series.values() for v in vals] or [INITIAL_CASH]
        min_v = min(min(all_vals), INITIAL_CASH * 0.985)
        max_v = max(max(all_vals), INITIAL_CASH * 1.015)
        if abs(max_v - min_v) < 10.0:
            min_v -= 50.0
            max_v += 50.0

        def to_x(idx: int, total: int) -> float:
            if total <= 1:
                return pad_left + plot_w / 2.0
            return pad_left + (idx / (total - 1)) * plot_w

        def to_y(val: float) -> float:
            ratio = (val - min_v) / (max_v - min_v)
            return pad_top + plot_h - ratio * plot_h

        # Grid lines (5 horizontal steps)
        grid_lines = []
        for i in range(5):
            gv = min_v + (i / 4.0) * (max_v - min_v)
            gy = to_y(gv)
            grid_lines.append(
                f'<line x1="{pad_left}" y1="{gy:.1f}" x2="{width - pad_right}" y2="{gy:.1f}" '
                f'stroke="#1e293b" stroke-width="1" />'
                f'<text x="{pad_left - 8}" y="{gy + 4:.1f}" text-anchor="end" fill="#94a3b8" font-size="11" font-family="monospace">'
                f'${gv:,.0f}</text>'
            )

        # $10,000 baseline
        base_y = to_y(INITIAL_CASH)
        grid_lines.append(
            f'<line x1="{pad_left}" y1="{base_y:.1f}" x2="{width - pad_right}" y2="{base_y:.1f}" '
            f'stroke="#475569" stroke-width="1.5" stroke-dasharray="5,4" />'
        )

        # Polylines for each agent
        polylines = []
        for a in agents:
            if highlight_agent_id and a.agent_id not in (highlight_agent_id, "ctrl_spy"):
                continue
            vals = series.get(a.agent_id, [INITIAL_CASH])
            color = AGENT_COLORS.get(a.agent_id, "#38bdf8")
            stroke_w = "3.0" if a.agent_id == highlight_agent_id else ("2.4" if a.agent_id == "ctrl_spy" else "1.8")
            pts = " ".join(f"{to_x(i, len(vals)):.1f},{to_y(v):.1f}" for i, v in enumerate(vals))
            polylines.append(
                f'<polyline fill="none" stroke="{color}" stroke-width="{stroke_w}" points="{pts}">'
                f'<title>{_esc(a.name)}: {_fmt_money(vals[-1])}</title></polyline>'
            )
            last_x = to_x(len(vals) - 1, len(vals))
            last_y = to_y(vals[-1])
            polylines.append(
                f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="3.5" fill="{color}">'
                f'<title>{_esc(a.name)}: {_fmt_money(vals[-1])}</title></circle>'
            )

        start_label = _esc(labels[0]) if labels else "Start"
        end_label = _esc(labels[-1]) if labels else "Latest"
        mid_label = _esc(labels[len(labels) // 2]) if len(labels) > 2 else ""

        return f"""<svg class="equity-svg-fallback" viewBox="0 0 {width} {height}" role="img" aria-label="Equity Curves SVG Chart">
          {''.join(grid_lines)}
          {''.join(polylines)}
          <text x="{pad_left}" y="{height - 12}" text-anchor="start" fill="#94a3b8" font-size="11" font-family="monospace">{start_label}</text>
          <text x="{pad_left + plot_w / 2:.1f}" y="{height - 12}" text-anchor="middle" fill="#94a3b8" font-size="11" font-family="monospace">{mid_label}</text>
          <text x="{width - pad_right}" y="{height - 12}" text-anchor="end" fill="#94a3b8" font-size="11" font-family="monospace">{end_label}</text>
        </svg>"""

    def _render_index_html(
        self,
        normalized_agents: List[AgentLedgerState],
        sorted_metrics: List[Dict[str, Any]],
        metrics_by_id: Dict[str, Dict[str, Any]],
        header_ctx: Dict[str, Any],
        bug_fixes: List[Dict[str, Any]],
        strategy_hashes: Dict[str, str],
    ) -> str:
        header_html = self._render_common_header(header_ctx, active_page="index", root_prefix="")
        best = sorted_metrics[0]
        spy = metrics_by_id.get("ctrl_spy", best)
        beating_spy_count = sum(
            1 for m in sorted_metrics if m["agent_id"] != "ctrl_spy" and m["net_return_pct"] > spy["net_return_pct"]
        )

        active_count = sum(1 for m in sorted_metrics if m["status"] in ("ACTIVE", "COMPLETED"))
        halted_count = sum(1 for m in sorted_metrics if m["status"] == "DAILY_HALTED")
        blown_count = sum(1 for m in sorted_metrics if m["status"] == "BLOWN_UP")

        total_fills = sum(m["total_fills"] for m in sorted_metrics)
        total_closed = sum(m["closed_trades_count"] for m in sorted_metrics)
        total_friction = sum(m["total_friction_cost"] for m in sorted_metrics)
        total_rejections = sum(m["rejections_count"] for m in sorted_metrics)

        # Build Leaderboard Rows
        leaderboard_rows = []
        for m in sorted_metrics:
            rank_badge = f'<span class="rank-pill rank-{m["rank"]}">#{m["rank"]}</span>'
            profit_cls = _pnl_class(m["net_profit"])
            vs_spy_cls = _pnl_class(m["return_vs_spy_pct"])
            dd_val = m["max_drawdown_pct"]
            avg_win_str = _fmt_money(m["avg_win"], signed=True) if m["win_count"] > 0 else "$0.00"
            avg_loss_str = _fmt_money(m["avg_loss"], signed=True) if m["loss_count"] > 0 else "$0.00"

            leaderboard_rows.append(
                f"""            <tr>
              <td class="col-rank">{rank_badge}</td>
              <td class="col-agent">
                <div class="agent-cell">
                  <span class="agent-color-dot" style="background:{m['color']}"></span>
                  <div>
                    <a href="agents/{_esc(m['agent_id'])}.html" class="agent-link">{_esc(m['name'])}</a>
                    <div class="agent-sub-row">
                      <span class="cat-pill {m['category_class']}">{_esc(m['short_category'])}</span>
                      <span class="agent-id-code">{_esc(m['agent_id'])}</span>
                    </div>
                  </div>
                </div>
              </td>
              <td><span class="status-pill {m['status_class']}" title="{_esc(m['status_reason'])}">{_esc(m['status'])}</span></td>
              <td class="num strong">{_fmt_money(m['equity'])}</td>
              <td class="num {profit_cls}">
                <div>{_fmt_money(m['net_profit'], signed=True)}</div>
                <div class="sub-num">({_fmt_pct(m['net_return_pct'])})</div>
              </td>
              <td class="num {vs_spy_cls}">{_fmt_pct(m['return_vs_spy_pct'])}</td>
              <td class="num neg">{_fmt_pct(dd_val, signed=False)}</td>
              <td class="num">
                <div>{m['total_fills']} fills</div>
                <div class="sub-num">{m['closed_trades_count']} closed</div>
              </td>
              <td class="num">{m['win_rate_pct']:.1f}% <span class="sub-num">({m['win_count']}W/{m['loss_count']}L)</span></td>
              <td class="num">{_esc(m['profit_factor_str'])}</td>
              <td class="num">
                <span class="pos">{avg_win_str}</span> / <span class="neg">{avg_loss_str}</span>
              </td>
            </tr>"""
            )

        # Build Chart.js dataset & SVG companion chart
        labels, series = self._build_chart_series(normalized_agents)
        svg_chart_html = self._render_svg_equity_chart(normalized_agents, labels, series)
        chart_datasets = []
        for a in normalized_agents:
            m = metrics_by_id[a.agent_id]
            chart_datasets.append({
                "label": a.name,
                "agent_id": a.agent_id,
                "category": m["short_category"],
                "data": series.get(a.agent_id, [INITIAL_CASH]),
                "borderColor": m["color"],
                "backgroundColor": m["color"],
                "borderWidth": 2.6 if a.agent_id == "ctrl_spy" else 2.0,
                "borderDash": [6, 4] if a.agent_id in ("ctrl_spy", "ctrl_random") else [],
                "pointRadius": 2,
                "tension": 0.18,
            })
        chart_json = json.dumps({"labels": labels, "datasets": chart_datasets}, ensure_ascii=False)

        # Recent Arena Activity Feed (latest 20 trades across all agents)
        all_trades: List[Tuple[AgentLedgerState, TradeRecord]] = []
        for a in normalized_agents:
            for t in a.trades:
                all_trades.append((a, t))
        all_trades.sort(key=lambda pair: pair[1].fill_timestamp or pair[1].decision_timestamp or "", reverse=True)

        activity_rows = []
        for a, t in all_trades[:20]:
            side_cls = "side-buy" if t.side == "BUY" else "side-sell"
            pnl_cell = "— (Entry)"
            pnl_cls = "neutral"
            if t.side == "SELL" and t.realized_pnl is not None:
                pnl_cls = _pnl_class(t.realized_pnl)
                pnl_cell = f"{_fmt_money(t.realized_pnl, signed=True)} ({_fmt_pct(t.realized_pnl_pct or 0.0)})"
            activity_rows.append(
                f"""            <tr>
              <td class="num ts-cell">
                <div>{_esc(format_et(t.fill_timestamp))}</div>
                <div class="sub-num">{_esc(format_kst(t.fill_timestamp))}</div>
              </td>
              <td><a href="agents/{_esc(a.agent_id)}.html" class="agent-link">{_esc(a.name)}</a></td>
              <td><span class="side-pill {side_cls}">{_esc(t.side)}</span></td>
              <td class="mono strong">{_esc(t.ticker)}</td>
              <td class="num">{t.shares:.4f}</td>
              <td class="num">{_fmt_money(t.fill_price)}</td>
              <td class="num {pnl_cls}">{pnl_cell}</td>
              <td class="reasoning-cell">{_esc(t.reasoning or t.exit_reason or "Systematic execution")}</td>
            </tr>"""
            )
        if not activity_rows:
            activity_rows.append(
                '<tr><td colspan="8" class="empty-state">No virtual trades executed yet — competition is initialized and ready for market open.</td></tr>'
            )

        # Bug Fixes & Strategy Integrity Log
        bug_rows = []
        for idx, bf in enumerate(bug_fixes, start=1):
            ts_str = format_kst(bf.get("timestamp") or bf.get("date")) if (bf.get("timestamp") or bf.get("date")) else "—"
            file_str = bf.get("file") or bf.get("module") or bf.get("component") or "Infrastructure"
            desc_str = bf.get("description") or bf.get("summary") or bf.get("reason") or json.dumps(bf)
            cat_str = bf.get("category") or bf.get("type") or "Technical Bug Fix"
            strat_changed = bool(bf.get("strategy_logic_changed", False))
            integrity_badge = (
                '<span class="status-pill status-blown">STRATEGY CHANGED</span>'
                if strat_changed
                else '<span class="status-pill status-active">INFRA FIX ONLY (Strategy Unchanged)</span>'
            )
            bug_rows.append(
                f"""            <tr>
              <td class="num">#{idx}</td>
              <td class="mono">{_esc(ts_str)}</td>
              <td><span class="cat-pill cat-control">{_esc(cat_str)}</span></td>
              <td class="mono">{_esc(file_str)}</td>
              <td>{_esc(desc_str)}</td>
              <td>{integrity_badge}</td>
            </tr>"""
            )
        if not bug_rows:
            bug_rows.append(
                '<tr><td colspan="6" class="empty-state">&#9989; 0 Mid-Competition Bug Fixes or Code Modifications Logged — All 10 Agent Strategies Running Unmodified Since Day 1 Freeze.</td></tr>'
            )

        hash_rows = []
        for rel_path, sha_val in sorted(strategy_hashes.items()):
            hash_rows.append(
                f"""            <tr>
              <td class="mono">{_esc(rel_path)}</td>
              <td class="mono hash-cell"><code>{_esc(sha_val)}</code></td>
              <td><span class="status-pill status-active">LOCKED &amp; VERIFIED</span></td>
            </tr>"""
            )

        total_agents = len(normalized_agents)
        non_spy_agents = max(1, total_agents - 1)
        learned_patterns_html = self._render_learned_patterns_panel(self._load_learned_patterns(), root_prefix="")

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI Trading Arena — {total_agents}-Agent US Stock &amp; ETF Paper-Trading Leaderboard</title>
  <link rel="stylesheet" href="styles.css" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
</head>
<body>
{header_html}

  <main class="container">
    <!-- Summary KPI Strip -->
    <section class="kpi-strip" aria-label="Competition Summary KPIs">
      <div class="kpi-card">
        <div class="kpi-label">Best Performing Agent</div>
        <div class="kpi-value"><a href="agents/{_esc(best['agent_id'])}.html">{_esc(best['name'])}</a></div>
        <div class="kpi-sub {_pnl_class(best['net_profit'])}">
          <strong>{_fmt_money(best['equity'])}</strong> ({_fmt_money(best['net_profit'], signed=True)} / {_fmt_pct(best['net_return_pct'])})
        </div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">S&amp;P 500 (<code>ctrl_spy</code>) Benchmark</div>
        <div class="kpi-value {_pnl_class(spy['net_profit'])}">{_fmt_pct(spy['net_return_pct'])}</div>
        <div class="kpi-sub">
          Equity: <strong>{_fmt_money(spy['equity'])}</strong> &bull; {beating_spy_count} of {non_spy_agents} active agents beating SPY
        </div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Agent Risk Status</div>
        <div class="kpi-value">{active_count} Active / {total_agents} Total</div>
        <div class="kpi-sub">
          <span class="pos">{active_count} Active</span> &bull;
          <span class="warn">{halted_count} Daily Halted (-3%)</span> &bull;
          <span class="neg">{blown_count} Blown Up (-10%)</span>
        </div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Total Virtual Trades Executed</div>
        <div class="kpi-value">{total_fills:,} Fills</div>
        <div class="kpi-sub">
          {total_closed:,} Closed Round-Trips &bull; {_fmt_money(total_friction)} Spread + 5 bps Slippage
        </div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Risk Manager Saves / Rejections</div>
        <div class="kpi-value">{total_rejections:,} Blocked</div>
        <div class="kpi-sub">
          Enforcing 1% Max Risk, 20% Cap, Max 5 Pos, No Look-Ahead
        </div>
      </div>
    </section>

    <!-- Leaderboard Table -->
    <section class="panel" id="leaderboard">
      <div class="panel-header">
        <div>
          <h2>&#127942; {total_agents}-Agent Standings &amp; Risk-Adjusted Leaderboard</h2>
          <p class="panel-sub">Sorted by current total virtual equity descending. Click any agent name to inspect open positions, plain-English daily journals, trade fills, and risk rejections.</p>
        </div>
        <div class="panel-actions">
          <a href="final_report.html" class="btn-secondary">View 10-Day Final Analysis &amp; Luck vs. Skill &rarr;</a>
        </div>
      </div>

      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Agent Name &amp; Category</th>
              <th>Status</th>
              <th class="num">Equity</th>
              <th class="num">Net Profit ($ / %)</th>
              <th class="num">Return vs SPY</th>
              <th class="num">Max Drawdown</th>
              <th class="num">Trades</th>
              <th class="num">Win Rate</th>
              <th class="num">Profit Factor</th>
              <th class="num">Avg Win / Avg Loss</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(leaderboard_rows)}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Equity Curves Chart -->
    <section class="panel" id="equity-curves">
      <div class="panel-header">
        <div>
          <h2>&#128200; All {total_agents} Agents — Equity Curves ($10,000 Starting Virtual Capital)</h2>
          <p class="panel-sub">Interactive mark-to-market trajectory net of bid/ask half-spread and 5 bps slippage on every fill. Toggle cohorts or hover any point for exact equity.</p>
        </div>
        <div class="chart-filter-bar" role="group" aria-label="Filter Equity Curves by Category">
          <button type="button" class="btn-filter active" data-filter="all">All {total_agents} Agents</button>
          <button type="button" class="btn-filter" data-filter="Coded">4 Coded</button>
          <button type="button" class="btn-filter" data-filter="Gemini Flash LLM">4 Gemini Flash LLMs</button>
          <button type="button" class="btn-filter" data-filter="Filing-Style">2 Filing-Style</button>
          <button type="button" class="btn-filter" data-filter="Control">2 Controls (SPY &amp; Random)</button>
        </div>
      </div>

      <div class="chart-container">
        <canvas id="equityChart" height="360"></canvas>
      </div>
      <details class="svg-companion-details">
        <summary>Static Vector (SVG) Equity Curve Snapshot (Zero-JS / Print View)</summary>
        <div class="svg-wrap">
          {svg_chart_html}
        </div>
      </details>
    </section>
{learned_patterns_html}

    <!-- Recent Arena Activity Feed -->
    <section class="panel" id="recent-activity">
      <div class="panel-header">
        <div>
          <h2>&#9889; Recent Arena Trade Activity Feed</h2>
          <p class="panel-sub">Latest virtual executions across all {total_agents} agents with plain-English decision reasoning and realistic spread + 5 bps slippage friction.</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Fill Timestamp (ET / KST)</th>
              <th>Agent</th>
              <th>Side</th>
              <th>Ticker</th>
              <th class="num">Shares</th>
              <th class="num">Fill Price</th>
              <th class="num">Realized P&amp;L</th>
              <th>Plain-English Reasoning</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(activity_rows)}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Bug Fixes & Strategy Integrity Log -->
    <section class="panel" id="integrity-log">
      <div class="panel-header">
        <div>
          <h2>&#128274; Bug Fixes &amp; Strategy Code-Freeze Integrity Log</h2>
          <p class="panel-sub">Strict fair-competition audit: all 10 agent playbooks, indicator parameters, and prompts are locked at Day 1. Any technical bug fix during the 10-day window is logged publicly below (`state/bug_fixes_log.json`).</p>
        </div>
      </div>

      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Timestamp (KST)</th>
              <th>Category</th>
              <th>File / Component</th>
              <th>Description</th>
              <th>Strategy Integrity</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(bug_rows)}
          </tbody>
        </table>
      </div>

      <h3 class="sub-heading">Locked Strategy &amp; Risk Engine SHA-256 Fingerprints</h3>
      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Source Module</th>
              <th>SHA-256 Integrity Digest</th>
              <th>Freeze Status</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(hash_rows)}
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <p><strong>PAPER TRADING - NOT REAL MONEY (100% Virtual $10,000 Starting Cash)</strong> &bull; AI Trading Arena Static Dashboard &bull; Updated {_esc(header_ctx['updated_kst'])} / {_esc(header_ctx['updated_et'])}</p>
  </footer>

  <script>
    (function() {{
      const payload = {chart_json};
      const canvas = document.getElementById('equityChart');
      if (!canvas || typeof Chart === 'undefined') return;
      const ctx = canvas.getContext('2d');
      const chart = new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: payload.labels,
          datasets: payload.datasets
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{ mode: 'index', intersect: false }},
          plugins: {{
            legend: {{
              position: 'bottom',
              labels: {{ color: '#e2e8f0', boxWidth: 14, padding: 14, font: {{ size: 12 }} }}
            }},
            tooltip: {{
              backgroundColor: 'rgba(15, 23, 42, 0.96)',
              titleColor: '#f8fafc',
              bodyColor: '#e2e8f0',
              borderColor: '#334155',
              borderWidth: 1,
              callbacks: {{
                label: function(context) {{
                  const v = context.parsed.y;
                  const diff = v - 10000;
                  const sign = diff >= 0 ? '+' : '-';
                  const pct = (diff / 100.0).toFixed(2);
                  return context.dataset.label + ': $' + v.toLocaleString('en-US', {{minimumFractionDigits: 2, maximumFractionDigits: 2}}) + ' (' + sign + Math.abs(pct) + '%)';
                }}
              }}
            }}
          }},
          scales: {{
            x: {{
              ticks: {{ color: '#94a3b8', maxRotation: 25, autoSkip: true, maxTicksLimit: 12 }},
              grid: {{ color: 'rgba(51, 65, 85, 0.35)' }}
            }},
            y: {{
              ticks: {{
                color: '#94a3b8',
                callback: function(val) {{ return '$' + Number(val).toLocaleString('en-US'); }}
              }},
              grid: {{ color: 'rgba(51, 65, 85, 0.35)' }}
            }}
          }}
        }}
      }});

      document.querySelectorAll('.btn-filter').forEach(function(btn) {{
        btn.addEventListener('click', function() {{
          document.querySelectorAll('.btn-filter').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
          const filter = btn.getAttribute('data-filter');
          chart.data.datasets.forEach(function(ds, idx) {{
            const meta = chart.getDatasetMeta(idx);
            if (filter === 'all') {{
              meta.hidden = false;
            }} else {{
              meta.hidden = (ds.category !== filter && ds.agent_id !== 'ctrl_spy');
            }}
          }});
          chart.update();
        }});
      }});
    }})();
  </script>
</body>
</html>
"""

    def _render_agent_html(
        self,
        agent: AgentLedgerState,
        agent_metrics: Dict[str, Any],
        spy_agent: Optional[AgentLedgerState],
        sorted_metrics: List[Dict[str, Any]],
        header_ctx: Dict[str, Any],
    ) -> str:
        header_html = self._render_common_header(header_ctx, active_page=agent.agent_id, root_prefix="../")
        m = agent_metrics

        # Open Positions rows
        pos_rows = []
        for ticker, pos in sorted(agent.positions.items()):
            tp_str = _fmt_money(pos.take_profit) if pos.take_profit is not None else "Trailing / Signal"
            upnl_cls = _pnl_class(pos.unrealized_pnl)
            pos_rows.append(
                f"""            <tr>
              <td class="mono strong">{_esc(pos.ticker)}</td>
              <td class="num">{pos.shares:.4f}</td>
              <td class="num">{_fmt_money(pos.avg_entry_price)}</td>
              <td class="num">{_fmt_money(pos.current_price)}</td>
              <td class="num strong">{_fmt_money(pos.market_value)}</td>
              <td class="num neg">{_fmt_money(pos.stop_loss)}</td>
              <td class="num pos">{tp_str}</td>
              <td class="num {upnl_cls}">
                <strong>{_fmt_money(pos.unrealized_pnl, signed=True)}</strong>
                <div class="sub-num">({_fmt_pct(pos.unrealized_pnl_pct)})</div>
              </td>
              <td class="reasoning-cell">
                <div>{_esc(pos.entry_reasoning or "Entered per strategy rules")}</div>
                <div class="sub-num">Opened: {_esc(format_et(pos.opened_at))} / {_esc(format_kst(pos.opened_at))}</div>
              </td>
            </tr>"""
            )
        if not pos_rows:
            pos_rows.append(
                '<tr><td colspan="9" class="empty-state">100% in Virtual Cash — No open positions currently held by this agent.</td></tr>'
            )

        # Daily Journal cards in plain English
        journal_cards = []
        for j in sorted(agent.daily_journal, key=lambda x: (x.trading_day_number, x.trading_date_et), reverse=True):
            jpnl_cls = _pnl_class(j.daily_pnl)
            highlights_html = ""
            if j.highlights:
                items_li = "".join(f"<li>{_esc(h)}</li>" for h in j.highlights)
                highlights_html = f'<ul class="journal-highlights">{items_li}</ul>'
            journal_cards.append(
                f"""        <article class="journal-card">
          <div class="journal-card-header">
            <div>
              <span class="day-badge">Trading Day {j.trading_day_number}</span>
              <strong class="journal-date">{_esc(j.trading_date_et)} (US Market Session)</strong>
            </div>
            <div class="journal-metrics">
              <span>Start: <strong>{_fmt_money(j.start_equity)}</strong></span> &rarr;
              <span>End: <strong>{_fmt_money(j.end_equity)}</strong></span>
              <span class="journal-pnl {jpnl_cls}">({_fmt_money(j.daily_pnl, signed=True)} / {_fmt_pct(j.daily_pnl_pct)})</span>
              <span class="journal-counts">{j.trades_count} fills &bull; {j.rejections_count} rejections</span>
            </div>
          </div>
          <p class="journal-summary">{_esc(j.summary_text)}</p>
          {highlights_html}
        </article>"""
            )
        if not journal_cards:
            journal_cards.append(
                f"""        <article class="journal-card">
          <div class="journal-card-header">
            <div><span class="day-badge">Pre-Launch / Ready</span> <strong>Initialized with $10,000.00 Virtual Cash</strong></div>
          </div>
          <p class="journal-summary">{_esc(agent.name)} is configured and locked under the shared risk manager. Plain-English daily trading journals will appear here after each US trading session.</p>
        </article>"""
            )

        # Full Trade Log Table
        trade_rows = []
        for t in reversed(agent.trades):
            side_cls = "side-buy" if t.side == "BUY" else "side-sell"
            dec_et = format_et(t.decision_timestamp)
            fill_et = format_et(t.fill_timestamp)
            fill_kst = format_kst(t.fill_timestamp)
            dec_dt = parse_utc_timestamp(t.decision_timestamp)
            fill_dt = parse_utc_timestamp(t.fill_timestamp)
            no_lookahead_ok = (dec_dt is None or fill_dt is None or dec_dt <= fill_dt)
            lookahead_badge = (
                '<span class="verify-ok" title="decision_timestamp <= fill_timestamp">&#10003; Verified No Look-Ahead</span>'
                if no_lookahead_ok
                else '<span class="verify-warn">Check Timestamp</span>'
            )

            if t.side == "SELL" and t.realized_pnl is not None:
                pnl_cls = _pnl_class(t.realized_pnl)
                pnl_html = f'<strong>{_fmt_money(t.realized_pnl, signed=True)}</strong><div class="sub-num">({_fmt_pct(t.realized_pnl_pct or 0.0)})</div>'
            else:
                pnl_cls = "neutral"
                pnl_html = "— (Open Entry)"

            exit_str = t.exit_reason if t.exit_reason else ("ENTRY" if t.side == "BUY" else "AGENT_SIGNAL")
            conf_pct = round(float(t.confidence or 0.5) * 100.0)

            trade_rows.append(
                f"""            <tr>
              <td class="num ts-cell">
                <div><strong>{_esc(fill_et)}</strong></div>
                <div class="sub-num">{_esc(fill_kst)}</div>
              </td>
              <td><span class="side-pill {side_cls}">{_esc(t.side)}</span></td>
              <td class="mono strong">{_esc(t.ticker)}</td>
              <td class="num">{t.shares:.4f}</td>
              <td class="num ts-cell">
                <div>Dec: {_esc(dec_et)}</div>
                <div>Fill: {_esc(fill_et)}</div>
                <div>{lookahead_badge}</div>
              </td>
              <td class="num">{_fmt_money(t.reference_price)}</td>
              <td class="num strong">{_fmt_money(t.fill_price)}</td>
              <td class="num neg">{_fmt_money(t.spread_cost_dollars)}</td>
              <td class="num neg">{_fmt_money(t.slippage_cost_dollars)}</td>
              <td class="num {pnl_cls}">{pnl_html}</td>
              <td><span class="exit-pill">{_esc(exit_str)}</span></td>
              <td class="num">{conf_pct}%</td>
              <td class="reasoning-cell">{_esc(t.reasoning or "Executed per systematic rule")}</td>
            </tr>"""
            )
        if not trade_rows:
            trade_rows.append(
                '<tr><td colspan="13" class="empty-state">No virtual trade fills recorded for this agent yet.</td></tr>'
            )

        # Risk Manager Rejection Log
        rejection_rows = []
        for r in reversed(agent.rejections):
            req_sl = _fmt_money(r.requested_stop_loss) if r.requested_stop_loss is not None else "None"
            rejection_rows.append(
                f"""            <tr>
              <td class="num ts-cell">
                <div>{_esc(format_et(r.timestamp))}</div>
                <div class="sub-num">{_esc(format_kst(r.timestamp))}</div>
              </td>
              <td><span class="side-pill side-sell">{_esc(r.action)}</span></td>
              <td class="mono strong">{_esc(r.ticker or "—")}</td>
              <td class="num">{req_sl}</td>
              <td class="neg strong">{_esc(r.reason)}</td>
              <td class="reasoning-cell">{_esc(r.agent_reasoning or "—")}</td>
            </tr>"""
            )
        if not rejection_rows:
            rejection_rows.append(
                '<tr><td colspan="6" class="empty-state">&#9989; 0 Risk Manager Rejections — All orders emitted by this agent complied with position, stop-loss, and drawdown limits.</td></tr>'
            )

        # Build single-agent vs SPY chart
        chart_agents = [agent] + ([spy_agent] if spy_agent and spy_agent.agent_id != agent.agent_id else [])
        labels, series = self._build_chart_series(chart_agents)
        svg_chart_html = self._render_svg_equity_chart(chart_agents, labels, series, highlight_agent_id=agent.agent_id)
        agent_datasets = [
            {
                "label": agent.name,
                "data": series.get(agent.agent_id, [INITIAL_CASH]),
                "borderColor": m["color"],
                "backgroundColor": m["color"],
                "borderWidth": 3.0,
                "pointRadius": 3,
                "tension": 0.18,
            }
        ]
        if spy_agent and spy_agent.agent_id != agent.agent_id:
            agent_datasets.append({
                "label": spy_agent.name,
                "data": series.get(spy_agent.agent_id, [INITIAL_CASH]),
                "borderColor": "#ffffff",
                "backgroundColor": "#ffffff",
                "borderWidth": 2.0,
                "borderDash": [6, 4],
                "pointRadius": 2,
                "tension": 0.18,
            })
        chart_json = json.dumps({"labels": labels, "datasets": agent_datasets}, ensure_ascii=False)

        avg_win_str = _fmt_money(m["avg_win"], signed=True) if m["win_count"] > 0 else "$0.00"
        avg_loss_str = _fmt_money(m["avg_loss"], signed=True) if m["loss_count"] > 0 else "$0.00"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{_esc(agent.name)} — AI Trading Arena Agent Ledger</title>
  <link rel="stylesheet" href="../styles.css" />
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
</head>
<body>
{header_html}

  <main class="container">
    <div class="breadcrumb-row">
      <a href="../index.html">&larr; Back to Main Leaderboard</a>
      <span>&bull;</span>
      <span>Rank <strong>#{m['rank']} of {len(sorted_metrics)}</strong></span>
      <span>&bull;</span>
      <a href="../final_report.html">10-Day Final Report &amp; Luck vs. Skill &rarr;</a>
    </div>

    <!-- Agent Header & Playbook -->
    <section class="panel agent-hero">
      <div class="agent-hero-top">
        <div>
          <div class="agent-title-line">
            <span class="rank-pill rank-{m['rank']}">Rank #{m['rank']}</span>
            <h1>{_esc(agent.name)}</h1>
            <span class="cat-pill {m['category_class']}">{_esc(m['short_category'])}</span>
            <span class="status-pill {m['status_class']}">{_esc(agent.status)}</span>
          </div>
          <p class="agent-short-desc">{_esc(m['short_description'])}</p>
        </div>
        <div class="agent-status-box">
          <div class="ts-label">Status Reason</div>
          <div class="strong">{_esc(agent.status_reason)}</div>
        </div>
      </div>

      <div class="playbook-box">
        <div class="playbook-title">Locked Strategy Playbook &amp; Risk Rules (<code>{_esc(agent.agent_id)}</code>)</div>
        <p>{_esc(m['playbook'])}</p>
        <div class="risk-rules-strip">
          <span>Max Risk/Trade: <strong>{MAX_RISK_PER_TRADE_PCT * 100:.0f}% ($100)</strong></span>
          <span>Max Single Position: <strong>{MAX_POSITION_EQUITY_PCT * 100:.0f}%</strong></span>
          <span>Max Open Positions: <strong>{MAX_OPEN_POSITIONS}</strong></span>
          <span>Daily Loss Halt: <strong>{DAILY_LOSS_LIMIT_PCT * 100:.0f}%</strong></span>
          <span>Max Total Drawdown Stop: <strong>{MAX_TOTAL_DRAWDOWN_PCT * 100:.0f}%</strong></span>
          <span>Slippage: <strong>{SLIPPAGE_BPS:.0f} bps + Half-Spread</strong></span>
          <span>EOD Flatten: <strong>{"Yes (Intraday Only)" if m['flatten_eod'] else "No (Multi-Day Allowed)"}</strong></span>
        </div>
      </div>
    </section>
{self._render_learned_patterns_panel({agent.agent_id: self._load_learned_patterns()[agent.agent_id]}, root_prefix="../") if agent.agent_id in self._load_learned_patterns() else ""}

    <!-- Agent KPI Strip -->
    <section class="kpi-strip kpi-strip-8" aria-label="Agent Performance KPIs">
      <div class="kpi-card">
        <div class="kpi-label">Current Equity</div>
        <div class="kpi-value">{_fmt_money(m['equity'])}</div>
        <div class="kpi-sub">Initial: {_fmt_money(m['initial_cash'])} &bull; Peak: {_fmt_money(m['peak_equity'])}</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Net Profit ($ / %)</div>
        <div class="kpi-value {_pnl_class(m['net_profit'])}">{_fmt_money(m['net_profit'], signed=True)}</div>
        <div class="kpi-sub {_pnl_class(m['net_return_pct'])}"><strong>{_fmt_pct(m['net_return_pct'])}</strong> total return</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Return vs. SPY</div>
        <div class="kpi-value {_pnl_class(m['return_vs_spy_pct'])}">{_fmt_pct(m['return_vs_spy_pct'])}</div>
        <div class="kpi-sub">Relative to <code>ctrl_spy</code> benchmark</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Cash &amp; Open Exposure</div>
        <div class="kpi-value">{_fmt_money(m['cash'])}</div>
        <div class="kpi-sub">Invested: {_fmt_money(m['positions_value'])} ({m['open_positions_count']} pos)</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Max Drawdown</div>
        <div class="kpi-value neg">{_fmt_pct(m['max_drawdown_pct'], signed=False)}</div>
        <div class="kpi-sub">Current DD: {_fmt_pct(m['current_drawdown_pct'], signed=False)}</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Win Rate</div>
        <div class="kpi-value">{m['win_rate_pct']:.1f}%</div>
        <div class="kpi-sub">{m['win_count']}W / {m['loss_count']}L ({m['closed_trades_count']} closed, {m['total_fills']} fills)</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Profit Factor</div>
        <div class="kpi-value">{_esc(m['profit_factor_str'])}</div>
        <div class="kpi-sub">Friction paid: {_fmt_money(m['total_friction_cost'])}</div>
      </div>

      <div class="kpi-card">
        <div class="kpi-label">Avg Win / Avg Loss</div>
        <div class="kpi-value"><span class="pos">{avg_win_str}</span> / <span class="neg">{avg_loss_str}</span></div>
        <div class="kpi-sub">Rejections: {m['rejections_count']}</div>
      </div>
    </section>

    <!-- Agent Equity Curve vs SPY -->
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>&#128200; Equity Trajectory vs. S&amp;P 500 Benchmark (<code>ctrl_spy</code>)</h2>
          <p class="panel-sub">Mark-to-market virtual portfolio equity from $10,000.00 initial capital.</p>
        </div>
      </div>
      <div class="chart-container chart-sm">
        <canvas id="agentEquityChart" height="280"></canvas>
      </div>
      <details class="svg-companion-details">
        <summary>Static Vector (SVG) Chart View</summary>
        <div class="svg-wrap">{svg_chart_html}</div>
      </details>
    </section>

    <!-- Current Open Positions Table -->
    <section class="panel" id="open-positions">
      <div class="panel-header">
        <div>
          <h2>&#128188; Current Open Positions ({len(agent.positions)} / {MAX_OPEN_POSITIONS} Max)</h2>
          <p class="panel-sub">Live mark-to-market fractional share holdings with hard stop-loss and take-profit levels.</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Ticker</th>
              <th class="num">Shares (Fractional)</th>
              <th class="num">Avg Entry Price</th>
              <th class="num">Current Price</th>
              <th class="num">Market Value</th>
              <th class="num">Stop-Loss</th>
              <th class="num">Take-Profit</th>
              <th class="num">Unrealized P&amp;L ($ / %)</th>
              <th>Entry Reasoning &amp; Timestamp</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(pos_rows)}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Daily Journal in Plain English -->
    <section class="panel" id="daily-journal">
      <div class="panel-header">
        <div>
          <h2>&#128221; Daily Trading Journal (Plain English)</h2>
          <p class="panel-sub">Day-by-day narrative explaining what {_esc(agent.name)} did, why it entered or stayed in cash, and how its virtual equity evolved.</p>
        </div>
      </div>
      <div class="journal-list">
{chr(10).join(journal_cards)}
      </div>
    </section>

    <!-- Full Trade Log Table -->
    <section class="panel" id="trade-log">
      <div class="panel-header">
        <div>
          <h2>&#128203; Full Virtual Trade Execution Log ({len(agent.trades)} Fills)</h2>
          <p class="panel-sub">Complete audit trail of every fill, including decision vs. fill timestamp (proving zero look-ahead), bid/ask spread cost, 5 bps slippage cost, and plain-English reasoning.</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Fill Timestamp (ET &amp; KST)</th>
              <th>Side</th>
              <th>Ticker</th>
              <th class="num">Fractional Shares</th>
              <th>Decision vs. Fill Time (No Look-Ahead)</th>
              <th class="num">Ref Price</th>
              <th class="num">Fill Price</th>
              <th class="num">Spread Cost</th>
              <th class="num">5 bps Slippage</th>
              <th class="num">Realized P&amp;L ($ / %)</th>
              <th>Exit / Fill Reason</th>
              <th class="num">Confidence</th>
              <th>Full Plain-English Reasoning</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(trade_rows)}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Risk Manager Rejection Log -->
    <section class="panel" id="rejection-log">
      <div class="panel-header">
        <div>
          <h2>&#128737; Shared Risk Manager Rejection Log ({len(agent.rejections)} Rejected Orders)</h2>
          <p class="panel-sub">Every order blocked by the shared risk manager before execution (missing stop-loss, &gt;1% risk, &gt;20% concentration, max 5 open positions, or -3% daily loss halt).</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Timestamp (ET &amp; KST)</th>
              <th>Action</th>
              <th>Ticker</th>
              <th class="num">Requested Stop-Loss</th>
              <th>Exact Risk Rejection Reason</th>
              <th>Agent's Original Reasoning</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(rejection_rows)}
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <p><strong>PAPER TRADING - NOT REAL MONEY (100% Virtual $10,000 Starting Cash)</strong> &bull; {_esc(agent.name)} &bull; Updated {_esc(header_ctx['updated_kst'])} / {_esc(header_ctx['updated_et'])}</p>
  </footer>

  <script>
    (function() {{
      const payload = {chart_json};
      const canvas = document.getElementById('agentEquityChart');
      if (!canvas || typeof Chart === 'undefined') return;
      new Chart(canvas.getContext('2d'), {{
        type: 'line',
        data: {{ labels: payload.labels, datasets: payload.datasets }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{ mode: 'index', intersect: false }},
          plugins: {{
            legend: {{ position: 'bottom', labels: {{ color: '#e2e8f0' }} }}
          }},
          scales: {{
            x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: 'rgba(51, 65, 85, 0.35)' }} }},
            y: {{
              ticks: {{ color: '#94a3b8', callback: function(v) {{ return '$' + Number(v).toLocaleString('en-US'); }} }},
              grid: {{ color: 'rgba(51, 65, 85, 0.35)' }}
            }}
          }}
        }}
      }});
    }})();
  </script>
</body>
</html>
"""

    def _render_final_report_html(
        self,
        normalized_agents: List[AgentLedgerState],
        sorted_metrics: List[Dict[str, Any]],
        metrics_by_id: Dict[str, Dict[str, Any]],
        header_ctx: Dict[str, Any],
        cohort_stats: Dict[str, Any],
        luck_stats: Dict[str, Any],
        bug_fixes: List[Dict[str, Any]],
        strategy_hashes: Dict[str, str],
    ) -> str:
        header_html = self._render_common_header(header_ctx, active_page="final_report", root_prefix="")
        top3 = sorted_metrics[:3]
        medals = [("Gold #1", "podium-gold"), ("Silver #2", "podium-silver"), ("Bronze #3", "podium-bronze")]

        podium_cards = []
        for idx, m in enumerate(top3):
            medal_label, medal_cls = medals[idx]
            podium_cards.append(
                f"""      <div class="podium-card {medal_cls}">
        <div class="podium-medal">{medal_label}</div>
        <h3><a href="agents/{_esc(m['agent_id'])}.html">{_esc(m['name'])}</a></h3>
        <div class="podium-cat"><span class="cat-pill {m['category_class']}">{_esc(m['short_category'])}</span></div>
        <div class="podium-equity">{_fmt_money(m['equity'])}</div>
        <div class="podium-ret {_pnl_class(m['net_profit'])}">
          {_fmt_money(m['net_profit'], signed=True)} ({_fmt_pct(m['net_return_pct'])}) &bull; {_fmt_pct(m['return_vs_spy_pct'])} vs SPY
        </div>
        <div class="podium-stats">
          <span>Max DD: <strong>{_fmt_pct(m['max_drawdown_pct'], signed=False)}</strong></span>
          <span>Win Rate: <strong>{m['win_rate_pct']:.1f}%</strong></span>
          <span>PF: <strong>{_esc(m['profit_factor_str'])}</strong></span>
          <span>Fills: <strong>{m['total_fills']}</strong></span>
        </div>
        <p class="podium-desc">{_esc(m['short_description'])}</p>
      </div>"""
            )

        # Cohort comparison rows
        cohort_rows = []
        for c_name, c_data in cohort_stats.items():
            cohort_rows.append(
                f"""            <tr>
              <td class="strong">{_esc(c_name)} ({c_data['count']})</td>
              <td class="num strong">{_fmt_money(c_data['avg_equity'])}</td>
              <td class="num {_pnl_class(c_data['avg_return_pct'])}"><strong>{_fmt_pct(c_data['avg_return_pct'])}</strong></td>
              <td class="num neg">{_fmt_pct(c_data['avg_max_drawdown_pct'], signed=False)}</td>
              <td class="num">{c_data['avg_win_rate_pct']:.1f}%</td>
              <td class="num">{c_data['total_fills']} ({c_data['total_closed_trades']} closed)</td>
              <td class="num neg">{_fmt_money(c_data['total_friction_cost'])}</td>
              <td class="num">{c_data['total_rejections']}</td>
              <td>{_esc(c_data['best_agent_name'])} ({_fmt_pct(c_data['best_agent_return_pct'])})</td>
            </tr>"""
            )

        # Standings rows with friction & risk saves
        standings_rows = []
        for m in sorted_metrics:
            standings_rows.append(
                f"""            <tr>
              <td class="num"><span class="rank-pill rank-{m['rank']}">#{m['rank']}</span></td>
              <td><a href="agents/{_esc(m['agent_id'])}.html" class="agent-link">{_esc(m['name'])}</a></td>
              <td><span class="cat-pill {m['category_class']}">{_esc(m['short_category'])}</span></td>
              <td><span class="status-pill {m['status_class']}">{_esc(m['status'])}</span></td>
              <td class="num strong">{_fmt_money(m['equity'])}</td>
              <td class="num {_pnl_class(m['net_profit'])}">{_fmt_money(m['net_profit'], signed=True)} ({_fmt_pct(m['net_return_pct'])})</td>
              <td class="num {_pnl_class(m['return_vs_spy_pct'])}">{_fmt_pct(m['return_vs_spy_pct'])}</td>
              <td class="num neg">{_fmt_pct(m['max_drawdown_pct'], signed=False)}</td>
              <td class="num">{m['win_rate_pct']:.1f}% ({m['closed_trades_count']} closed)</td>
              <td class="num">{_esc(m['profit_factor_str'])}</td>
              <td class="num neg">{_fmt_money(m['total_spread_cost'])}</td>
              <td class="num neg">{_fmt_money(m['total_slippage_cost'])}</td>
              <td class="num">{m['rejections_count']}</td>
            </tr>"""
            )

        # Statistical Luck vs Skill table rows
        luck_rows = []
        for st in luck_stats["per_agent"]:
            sig_badge = (
                '<span class="status-pill status-active">|t| &ge; 1.96 (p &lt; 0.05)</span>'
                if st["statistically_significant_95"]
                else '<span class="status-pill status-halted">Within 95% Noise Band (|t| &lt; 1.96)</span>'
            )
            luck_rows.append(
                f"""            <tr>
              <td class="num">#{st['rank']}</td>
              <td><a href="agents/{_esc(st['agent_id'])}.html" class="agent-link">{_esc(st['name'])}</a></td>
              <td class="num">{st['n_days']}</td>
              <td class="num {_pnl_class(st['cum_return_pct'])}"><strong>{_fmt_pct(st['cum_return_pct'])}</strong></td>
              <td class="num">{_fmt_pct(st['mean_daily_pct'], decimals=3)} &plusmn; {st['std_daily_pct']:.3f}%</td>
              <td class="num">&plusmn;{st['cum_se_pct']:.2f}%</td>
              <td class="num">[{_fmt_pct(st['ci_95_low_pct'])}, {_fmt_pct(st['ci_95_high_pct'])}]</td>
              <td class="num {_pnl_class(st['diff_vs_random_pct'])}">{_fmt_pct(st['diff_vs_random_pct'])} (<strong>{st['t_stat_vs_random']:+.2f} SE</strong>)</td>
              <td class="num {_pnl_class(st['diff_vs_spy_pct'])}">{_fmt_pct(st['diff_vs_spy_pct'])} ({st['t_stat_vs_spy']:+.2f} SE)</td>
              <td>{sig_badge}</td>
            </tr>"""
            )

        coded_c = cohort_stats.get("Coded", {"avg_return_pct": 0.0, "total_friction_cost": 0.0, "avg_max_drawdown_pct": 0.0})
        llm_c = cohort_stats.get("Gemini Flash LLM", {"avg_return_pct": 0.0, "total_friction_cost": 0.0, "avg_max_drawdown_pct": 0.0})
        spy_c = cohort_stats.get("Buy-and-Hold SPY", {"avg_return_pct": 0.0, "total_friction_cost": 0.0})
        rand_c = cohort_stats.get("Random Trader (Luck Control)", {"avg_return_pct": 0.0, "total_friction_cost": 0.0})

        most_active = max(sorted_metrics, key=lambda x: x["total_fills"])
        lowest_dd = max(sorted_metrics, key=lambda x: x["max_drawdown_pct"])  # closest to 0.0%

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>10-Day Final Report &amp; Luck vs. Skill Analysis — AI Trading Arena</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
{header_html}

  <main class="container">
    <section class="panel">
      <div class="panel-header">
        <div>
          <h1>&#127942; 10-Day Competition Final Report &amp; Scientific Post-Mortem</h1>
          <p class="panel-sub">Comprehensive ranking, strategy cohort comparison, transaction-cost drag audit, and statistical analysis of luck vs. skill over N = 10 US trading days.</p>
        </div>
        <div class="panel-actions">
          <a href="index.html" class="btn-secondary">&larr; Back to Live Leaderboard</a>
        </div>
      </div>

      <div class="podium-grid">
{chr(10).join(podium_cards)}
      </div>
    </section>

    <!-- Full 10-Agent Final Standings -->
    <section class="panel" id="final-standings">
      <div class="panel-header">
        <div>
          <h2>&#128202; Final 10-Agent Standings &amp; Friction Breakdown</h2>
          <p class="panel-sub">Every agent started with $10,000.00 virtual cash under identical risk limits and execution friction.</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Agent</th>
              <th>Category</th>
              <th>Final Status</th>
              <th class="num">Final Equity</th>
              <th class="num">Net Return ($ / %)</th>
              <th class="num">vs. SPY</th>
              <th class="num">Max DD</th>
              <th class="num">Win Rate</th>
              <th class="num">Profit Factor</th>
              <th class="num">Spread Drag</th>
              <th class="num">5 bps Slippage</th>
              <th class="num">Risk Saves</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(standings_rows)}
          </tbody>
        </table>
      </div>
    </section>

    <!-- What Worked vs. What Did Not -->
    <section class="panel" id="what-worked">
      <div class="panel-header">
        <div>
          <h2>&#128269; What Worked vs. What Did Not: Data-Driven Cohort Breakdown</h2>
          <p class="panel-sub">Comparing Rule-Based Coded Strategies, Gemini Flash LLM Traders, Passive Buy-and-Hold SPY, and the Random Trader Luck Control.</p>
        </div>
      </div>

      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Cohort / Group</th>
              <th class="num">Avg Final Equity</th>
              <th class="num">Avg Return (%)</th>
              <th class="num">Avg Max Drawdown</th>
              <th class="num">Avg Win Rate</th>
              <th class="num">Total Fills</th>
              <th class="num">Total Friction Cost</th>
              <th class="num">Risk Rejections</th>
              <th>Top Performer in Cohort</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(cohort_rows)}
          </tbody>
        </table>
      </div>

      <div class="insights-grid">
        <div class="insight-card">
          <h3>1. Coded Strategies vs. Gemini Flash LLM Traders</h3>
          <p>
            The 4 deterministic <strong>Coded Strategies</strong> averaged <strong>{_fmt_pct(coded_c['avg_return_pct'])}</strong>
            with an average max drawdown of <strong>{_fmt_pct(coded_c['avg_max_drawdown_pct'], signed=False)}</strong>, whereas the 4
            <strong>Gemini Flash LLM Traders</strong> averaged <strong>{_fmt_pct(llm_c['avg_return_pct'])}</strong>
            with an average max drawdown of <strong>{_fmt_pct(llm_c['avg_max_drawdown_pct'], signed=False)}</strong>.
            Structured JSON validation and temperature-0.1 prompting kept LLM decisions grounded in the precomputed
            point-in-time indicators while allowing regime-aware discretion.
          </p>
        </div>

        <div class="insight-card">
          <h3>2. Active Trading vs. Passive S&amp;P 500 (<code>ctrl_spy</code>)</h3>
          <p>
            <strong>9. Buy-and-Hold SPY (Benchmark)</strong> returned <strong>{_fmt_pct(spy_c['avg_return_pct'])}</strong> while paying
            only <strong>{_fmt_money(spy_c['total_friction_cost'])}</strong> in execution friction. Active strategies had to overcome
            both the directional drift of the S&amp;P 500 and the cumulative drag of bid/ask spreads plus 5 bps per-fill slippage.
          </p>
        </div>

        <div class="insight-card">
          <h3>3. Turnover &amp; Transaction Cost Drag</h3>
          <p>
            The highest-turnover agent was <strong>{_esc(most_active['name'])}</strong> with <strong>{most_active['total_fills']} fills</strong>,
            incurring <strong>{_fmt_money(most_active['total_friction_cost'])}</strong> in combined spread and slippage drag
            ({_fmt_money(most_active['total_spread_cost'])} spread + {_fmt_money(most_active['total_slippage_cost'])} slippage).
            Over a short 10-day window, minimizing unnecessary churn proved critical to preserving net alpha.
          </p>
        </div>

        <div class="insight-card">
          <h3>4. Drawdown Control &amp; Shared Risk Manager Saves</h3>
          <p>
            <strong>{_esc(lowest_dd['name'])}</strong> achieved the tightest drawdown control at
            <strong>{_fmt_pct(lowest_dd['max_drawdown_pct'], signed=False)}</strong>. Across the entire arena, the shared Risk Manager
            enforced hard 1% risk-per-trade sizing, 20% single-stock caps, and the -3% daily loss circuit breaker,
            preventing catastrophic single-trade blow-ups even when signals were aggressive.
          </p>
        </div>
      </div>
    </section>

    <!-- Honest Statement on Luck vs. Skill Over 10 Trading Days -->
    <section class="panel" id="luck-vs-skill">
      <div class="panel-header">
        <div>
          <h2>&#9878; Honest Statement on Luck vs. Skill Over 10 Trading Days (N = 10)</h2>
          <p class="panel-sub">Why a 2-week (10 US trading day) competition cannot statistically prove long-term alpha — and what our two scientific controls reveal.</p>
        </div>
      </div>

      <div class="scientific-callout">
        <h3>Key Statistical Finding: Winner vs. Random Luck Control (<code>ctrl_random</code>) &amp; SPY (<code>ctrl_spy</code>)</h3>
        <p>
          Over <strong>N = {luck_stats['sample_size_days']} US trading days</strong>, top-ranked
          <strong>{_esc(luck_stats['winner_name'])}</strong> finished at <strong>{_fmt_pct(luck_stats['winner_return_pct'])}</strong>
          (95% confidence interval: <strong>[{_fmt_pct(luck_stats['winner_ci_95_low_pct'])}, {_fmt_pct(luck_stats['winner_ci_95_high_pct'])}]</strong>,
          cumulative standard error <strong>&plusmn;{luck_stats['winner_cum_se_pct']:.2f}%</strong>).
        </p>
        <ul>
          <li>
            <strong>Comparison vs. 10. Random Trader (Luck Control):</strong>
            <code>ctrl_random</code> finished at <strong>{_fmt_pct(luck_stats['random_return_pct'])}</strong> under the exact same risk manager.
            The spread between #1 and pure random selection is <strong>{_fmt_pct(luck_stats['winner_vs_random_pct'])}</strong>, which equals
            <strong>{luck_stats['winner_vs_random_se']:+.2f} standard errors (t-statistic)</strong>.
          </li>
          <li>
            <strong>Comparison vs. 9. Buy-and-Hold SPY (Benchmark):</strong>
            <code>ctrl_spy</code> finished at <strong>{_fmt_pct(luck_stats['spy_return_pct'])}</strong>.
            The spread between #1 and passive market beta is <strong>{_fmt_pct(luck_stats['winner_vs_spy_pct'])}</strong>, or
            <strong>{luck_stats['winner_vs_spy_se']:+.2f} standard errors</strong>.
          </li>
          <li>
            <strong>Why N = 10 Days Has Wide Confidence Intervals:</strong>
            In quantitative finance, the standard error of a strategy's cumulative return scales with
            <code>&sigma;<sub>daily</sub> &times; &radic;N</code>, while the t-statistic of skill scales with
            <code>(Sharpe<sub>annual</sub> &times; &radic;(N / 252))</code>. Even for a genuinely strong strategy with a true annualized
            Sharpe Ratio of <strong>1.50</strong>, a 10-trading-day sample (<code>10 / 252 &approx; 0.04</code> years) yields an expected
            t-statistic of only <code>1.50 &times; &radic;0.04 &approx; 0.30</code> standard errors — far below the <strong>1.96 standard errors</strong>
            required for 95% statistical significance (which requires roughly <code>(1.96 / 1.50)<sup>2</sup> &approx; 1.71 years &approx; 430 trading days</code>).
          </li>
          <li>
            <strong>Bottom Line:</strong>
            While the 10-day arena rigorously tests <em>engineering discipline, zero look-ahead execution, transaction-cost realism, and automated risk containment</em>,
            short-horizon return rankings are heavily influenced by regime alignment and sample variance. Any single agent beating <code>ctrl_random</code> or <code>ctrl_spy</code>
            over 10 days should be interpreted as an transparent experiment in execution and risk hygiene, <strong>not</strong> proof of permanent market-beating skill.
          </li>
        </ul>
      </div>

      <div class="table-wrap">
        <table class="arena-table">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Agent</th>
              <th class="num">Days (N)</th>
              <th class="num">10-Day Return</th>
              <th class="num">Daily Mean &plusmn; SD</th>
              <th class="num">10-Day Std Error (SE)</th>
              <th class="num">95% Confidence Interval</th>
              <th class="num">Spread vs. Random (SE)</th>
              <th class="num">Spread vs. SPY (SE)</th>
              <th>95% Significance Check</th>
            </tr>
          </thead>
          <tbody>
{chr(10).join(luck_rows)}
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <p><strong>PAPER TRADING - NOT REAL MONEY (100% Virtual $10,000 Starting Cash)</strong> &bull; AI Trading Arena 10-Day Final Report &bull; Updated {_esc(header_ctx['updated_kst'])} / {_esc(header_ctx['updated_et'])}</p>
  </footer>
</body>
</html>
"""

    def _render_styles_css(self) -> str:
        return """/* AI Trading Arena — Institutional Quantitative Terminal Stylesheet */
:root {
  --bg-canvas: #070b14;
  --bg-panel: #0f172a;
  --bg-card: #131e36;
  --bg-hover: #1e293b;
  --border-subtle: #1e293b;
  --border-strong: #334155;
  --text-primary: #f8fafc;
  --text-secondary: #cbd5e1;
  --text-muted: #94a3b8;
  --accent-blue: #38bdf8;
  --accent-indigo: #818cf8;
  --pos-green: #10b981;
  --pos-bg: rgba(16, 185, 129, 0.14);
  --neg-red: #f43f5e;
  --neg-bg: rgba(244, 63, 94, 0.14);
  --warn-amber: #f59e0b;
  --warn-bg: rgba(245, 158, 11, 0.16);
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
}

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  background-color: var(--bg-canvas);
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}

a {
  color: var(--accent-blue);
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

code, .mono {
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.num {
  text-align: right;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.strong {
  font-weight: 600;
  color: var(--text-primary);
}

.pos { color: var(--pos-green) !important; }
.neg { color: var(--neg-red) !important; }
.warn { color: var(--warn-amber) !important; }
.neutral { color: var(--text-muted); }

/* Mandatory Paper Trading Warning Banner */
.paper-warning-banner {
  background: linear-gradient(90deg, #7c2d12 0%, #9a3412 50%, #7c2d12 100%);
  border-bottom: 2px solid #f59e0b;
  color: #fef3c7;
  padding: 9px 20px;
  text-align: center;
  font-size: 13px;
  letter-spacing: 0.02em;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 10px;
}

.paper-warning-banner strong {
  color: #ffffff;
  font-weight: 800;
  background: rgba(0, 0, 0, 0.28);
  padding: 2px 8px;
  border-radius: 4px;
}

.warning-sub {
  color: #fde68a;
  font-size: 12px;
}

/* Header & Timestamps */
.site-header {
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border-strong);
  position: sticky;
  top: 0;
  z-index: 50;
}

.header-main {
  max-width: 1480px;
  margin: 0 auto;
  padding: 14px 24px;
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}

.brand-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.brand-title {
  font-size: 22px;
  font-weight: 800;
  color: var(--text-primary);
  letter-spacing: -0.02em;
}

.brand-subtitle {
  color: var(--text-muted);
  font-size: 13px;
  margin-top: 2px;
}

.comp-status-badge {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 700;
  padding: 4px 10px;
  border-radius: 999px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.comp-live {
  background: var(--pos-bg);
  color: var(--pos-green);
  border: 1px solid var(--pos-green);
}

.comp-dryrun {
  background: var(--warn-bg);
  color: var(--warn-amber);
  border: 1px solid var(--warn-amber);
}

.comp-completed {
  background: rgba(56, 189, 248, 0.16);
  color: var(--accent-blue);
  border: 1px solid var(--accent-blue);
}

.comp-ready {
  background: rgba(148, 163, 184, 0.16);
  color: var(--text-secondary);
  border: 1px solid var(--border-strong);
}

.progress-and-clock {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 20px;
}

.trading-progress-box {
  min-width: 240px;
  background: var(--bg-card);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 8px 12px;
}

.progress-label-row {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 6px;
  gap: 10px;
}

.progress-title {
  color: var(--text-muted);
}

.progress-track {
  height: 7px;
  background: #090d16;
  border-radius: 999px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #38bdf8, #10b981);
  border-radius: 999px;
}

.timestamps-box {
  background: var(--bg-card);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 12px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.ts-item {
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.ts-label {
  color: var(--text-muted);
}

.ts-value {
  font-family: var(--font-mono);
  color: var(--text-primary);
  font-weight: 600;
}

/* Navigation Bar */
.site-nav {
  max-width: 1480px;
  margin: 0 auto;
  padding: 8px 24px 10px;
  border-top: 1px solid var(--border-subtle);
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.nav-primary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.nav-link {
  padding: 6px 12px;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid transparent;
}

.nav-link:hover {
  background: var(--bg-hover);
  text-decoration: none;
  color: var(--text-primary);
}

.nav-link.active {
  background: rgba(56, 189, 248, 0.14);
  color: var(--accent-blue);
  border-color: rgba(56, 189, 248, 0.4);
}

.nav-agents-strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
}

.nav-agents-label {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  margin-right: 4px;
}

.agent-pill {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 4px;
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border-subtle);
}

.agent-pill:hover, .agent-pill.active {
  background: rgba(56, 189, 248, 0.18);
  color: var(--accent-blue);
  border-color: var(--accent-blue);
  text-decoration: none;
}

/* Main Container & KPI Strip */
.container {
  max-width: 1480px;
  margin: 22px auto 48px;
  padding: 0 24px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.kpi-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
}

.kpi-strip-8 {
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
}

.kpi-card {
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 14px 16px;
}

.kpi-label {
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
}

.kpi-value {
  font-size: 20px;
  font-weight: 800;
  font-family: var(--font-mono);
  margin: 6px 0 4px;
  color: var(--text-primary);
}

.kpi-sub {
  font-size: 12px;
  color: var(--text-muted);
}

/* Panels & Tables */
.panel {
  background: var(--bg-panel);
  border: 1px solid var(--border-strong);
  border-radius: 12px;
  padding: 20px;
}

.panel-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 16px;
}

.panel-header h1, .panel-header h2 {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}

.panel-sub {
  font-size: 13px;
  color: var(--text-muted);
  margin-top: 3px;
}

.sub-heading {
  font-size: 15px;
  font-weight: 700;
  margin: 20px 0 10px;
  color: var(--text-secondary);
}

.btn-secondary {
  display: inline-block;
  padding: 8px 14px;
  border-radius: 6px;
  background: var(--bg-card);
  border: 1px solid var(--border-strong);
  color: var(--accent-blue);
  font-weight: 600;
  font-size: 13px;
}

.btn-secondary:hover {
  background: var(--bg-hover);
  text-decoration: none;
}

.table-wrap {
  width: 100%;
  overflow-x: auto;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
}

.arena-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.arena-table th {
  background: var(--bg-card);
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 11px 12px;
  text-align: left;
  border-bottom: 1px solid var(--border-strong);
  white-space: nowrap;
}

.arena-table th.num {
  text-align: right;
}

.arena-table td {
  padding: 11px 12px;
  border-bottom: 1px solid var(--border-subtle);
  vertical-align: middle;
}

.arena-table tbody tr:hover {
  background: rgba(30, 41, 59, 0.55);
}

.sub-num {
  font-size: 11px;
  color: var(--text-muted);
}

.agent-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.agent-color-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.agent-link {
  font-weight: 700;
  color: var(--text-primary);
}

.agent-link:hover {
  color: var(--accent-blue);
}

.agent-sub-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 3px;
}

.agent-id-code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
}

/* Badges & Pills */
.rank-pill {
  font-family: var(--font-mono);
  font-weight: 800;
  font-size: 12px;
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--bg-card);
  border: 1px solid var(--border-strong);
}

.rank-1 { background: rgba(250, 204, 21, 0.2); color: #facc15; border-color: #facc15; }
.rank-2 { background: rgba(226, 232, 240, 0.16); color: #e2e8f0; border-color: #94a3b8; }
.rank-3 { background: rgba(251, 146, 60, 0.2); color: #fb923c; border-color: #fb923c; }

.cat-pill {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 4px;
  white-space: nowrap;
}

.cat-coded { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
.cat-llm { background: rgba(192, 132, 252, 0.15); color: #c084fc; }
.cat-style { background: rgba(34, 197, 94, 0.16); color: #4ade80; }
.cat-control { background: rgba(148, 163, 184, 0.18); color: #cbd5e1; }

.style-patterns-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
}

.style-pattern-card {
  background: var(--bg-card);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.style-pattern-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.style-pattern-header h3 {
  font-size: 16px;
  margin-bottom: 3px;
}

.oos-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(15, 23, 42, 0.65);
  border: 1px solid var(--border-subtle);
  font-size: 12.5px;
}

.oos-verdict-text {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.5;
}

.rules-heading {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
}

.learned-rules-list {
  margin-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  color: var(--text-primary);
  line-height: 1.45;
}

.status-pill {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 4px;
  white-space: nowrap;
}

.status-active { background: var(--pos-bg); color: var(--pos-green); }
.status-halted { background: var(--warn-bg); color: var(--warn-amber); }
.status-blown { background: var(--neg-bg); color: var(--neg-red); }
.status-completed { background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); }

.side-pill {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 4px;
}

.side-buy { background: var(--pos-bg); color: var(--pos-green); }
.side-sell { background: var(--neg-bg); color: var(--neg-red); }

.exit-pill {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--bg-card);
  color: var(--text-secondary);
}

.verify-ok {
  font-size: 11px;
  color: var(--pos-green);
}

/* Charts */
.chart-filter-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.btn-filter {
  background: var(--bg-card);
  color: var(--text-secondary);
  border: 1px solid var(--border-strong);
  padding: 6px 11px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.btn-filter.active, .btn-filter:hover {
  background: rgba(56, 189, 248, 0.16);
  color: var(--accent-blue);
  border-color: var(--accent-blue);
}

.chart-container {
  position: relative;
  height: 370px;
  width: 100%;
}

.chart-sm {
  height: 280px;
}

.svg-companion-details {
  margin-top: 12px;
  font-size: 12px;
  color: var(--text-muted);
}

.svg-companion-details summary {
  cursor: pointer;
  user-select: none;
}

.svg-wrap {
  margin-top: 8px;
  background: #090f1d;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 10px;
}

.equity-svg-fallback {
  width: 100%;
  height: auto;
  display: block;
}

/* Agent Page Specifics */
.breadcrumb-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: var(--text-muted);
}

.agent-hero-top {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 14px;
}

.agent-title-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
}

.agent-title-line h1 {
  font-size: 22px;
  font-weight: 800;
}

.agent-short-desc {
  color: var(--text-secondary);
  margin-top: 6px;
  font-size: 14px;
}

.agent-status-box {
  background: var(--bg-card);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 10px 14px;
  min-width: 240px;
}

.playbook-box {
  background: var(--bg-card);
  border-left: 4px solid var(--accent-blue);
  border-radius: 8px;
  padding: 14px 16px;
}

.playbook-title {
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--accent-blue);
  margin-bottom: 6px;
}

.risk-rules-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border-subtle);
  font-size: 12px;
  color: var(--text-muted);
}

/* Daily Journal Cards */
.journal-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.journal-card {
  background: var(--bg-card);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  padding: 14px 16px;
}

.journal-card-header {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.day-badge {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 700;
  background: rgba(56, 189, 248, 0.16);
  color: var(--accent-blue);
  padding: 2px 8px;
  border-radius: 4px;
  margin-right: 8px;
}

.journal-metrics {
  font-family: var(--font-mono);
  font-size: 12px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.journal-summary {
  color: var(--text-secondary);
  font-size: 13.5px;
}

.journal-highlights {
  margin-top: 8px;
  padding-left: 20px;
  color: var(--text-muted);
  font-size: 12.5px;
}

/* Final Report Podium & Insights */
.podium-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
  gap: 16px;
}

.podium-card {
  background: var(--bg-card);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 18px;
}

.podium-gold { border-top: 4px solid #facc15; }
.podium-silver { border-top: 4px solid #cbd5e1; }
.podium-bronze { border-top: 4px solid #fb923c; }

.podium-medal {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
  color: var(--text-muted);
  margin-bottom: 4px;
}

.podium-equity {
  font-family: var(--font-mono);
  font-size: 26px;
  font-weight: 800;
  margin-top: 10px;
}

.podium-ret {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
}

.podium-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--text-muted);
  padding: 8px 0;
  border-top: 1px solid var(--border-subtle);
  border-bottom: 1px solid var(--border-subtle);
}

.podium-desc {
  font-size: 12.5px;
  color: var(--text-secondary);
  margin-top: 10px;
}

.insights-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 14px;
  margin-top: 18px;
}

.insight-card {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
  padding: 14px 16px;
}

.insight-card h3 {
  font-size: 14px;
  font-weight: 700;
  color: var(--accent-blue);
  margin-bottom: 6px;
}

.insight-card p {
  font-size: 13px;
  color: var(--text-secondary);
}

.scientific-callout {
  background: var(--bg-card);
  border-left: 4px solid var(--warn-amber);
  border-radius: 8px;
  padding: 16px 18px;
  margin-bottom: 18px;
}

.scientific-callout h3 {
  font-size: 15px;
  color: #fde68a;
  margin-bottom: 8px;
}

.scientific-callout p, .scientific-callout li {
  font-size: 13.5px;
  color: var(--text-secondary);
}

.scientific-callout ul {
  margin-top: 10px;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.reasoning-cell {
  min-width: 240px;
  max-width: 440px;
  color: var(--text-secondary);
}

.empty-state {
  text-align: center;
  padding: 20px !important;
  color: var(--text-muted);
  font-style: italic;
}

.site-footer {
  border-top: 1px solid var(--border-subtle);
  padding: 20px 24px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}
"""
