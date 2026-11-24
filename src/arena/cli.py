"""Command-Line Interface & Cycle Orchestrator for the AI Trading Arena.

Provides:
- Competition state & bug-fix audit log management (`state/competition_state.json`, `state/bug_fixes_log.json`)
- `init_or_reset_state(mode="LIVE")`: Resets all 10 agents to $10,000.00 cash and rebuilds a clean dashboard
- `run_single_cycle(...)`: Executes one 30-minute decision -> risk check -> next-price fill -> MTM -> journal cycle
- CLI subcommands (`python -m arena.cli <command>`):
  - `run-cycle`: Live cycle runner invoked by GitHub Actions every 30 minutes
  - `dry-run`: Replays 5 US trading days of 30-min historical data, saves `docs_dry_run/` & `docs/`, prints leaderboard
  - `reset`: Resets `state/` and `docs/` cleanly to $10,000.00 (Day 0/10)
  - `verify-setup`: Validates `.env`, Alpaca Paper API clock/calendar, Gemini Flash API key, and strategy SHA-256 hashes
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import logging
import os
import sys
import types
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Unified Module Aliasing (`arena.*` <-> `src.arena.*`)
# Ensures all submodules share identical class objects regardless of how
# PYTHONPATH is configured (`PYTHONPATH=src` vs `PYTHONPATH=.`).
# ---------------------------------------------------------------------------

_THIS_FILE = Path(__file__).resolve()
_ARENA_DIR = _THIS_FILE.parent
_SRC_DIR = _ARENA_DIR.parent
_PROJECT_ROOT = _SRC_DIR.parent

for _p in (str(_SRC_DIR), str(_PROJECT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import arena  # noqa: E402
import arena.config as _arena_config  # noqa: E402
import arena.models as _arena_models  # noqa: E402

if "src" not in sys.modules:
    _src_pkg = types.ModuleType("src")
    _src_pkg.__path__ = [str(_SRC_DIR)]  # type: ignore[attr-defined]
    sys.modules["src"] = _src_pkg

sys.modules["src"].arena = arena  # type: ignore[attr-defined]
sys.modules["src.arena"] = arena
sys.modules["src.arena.config"] = _arena_config
sys.modules["src.arena.models"] = _arena_models

from arena.agents import CODED_AND_CONTROL_AGENT_CLASSES, BaseAgent, compute_strategy_hashes  # noqa: E402
from arena.agents.llm_agents import LLM_AGENT_CLASSES, create_llm_agent  # noqa: E402
from arena.config import (  # noqa: E402
    AGENT_SPECS,
    AGENT_SPECS_BY_ID,
    ALPACA_PAPER_BASE_URL,
    COMPETITION_TRADING_DAYS,
    DEFAULT_SPREAD_BPS,
    DOCS_DIR,
    DRY_RUN_DOCS_DIR,
    INITIAL_CASH,
    PROJECT_ROOT,
    STATE_DIR,
    UNIVERSE,
    assert_paper_trading_only,
)
from arena.data.alpaca_client import (  # noqa: E402
    AlpacaDataClient,
    format_iso_utc,
    get_session_hours_et,
    load_dotenv_if_present,
    parse_iso_utc,
    to_et_datetime,
)
from arena.data.historical_provider import HistoricalReplayProvider  # noqa: E402
from arena.engine.ledger import LedgerManager, compute_leaderboard_metrics  # noqa: E402
from arena.engine.risk_manager import RiskManager  # noqa: E402
from arena.engine.simulator import FillSimulator  # noqa: E402
from arena.models import (  # noqa: E402
    AgentLedgerState,
    Bar,
    EquityPoint,
    MarketSnapshot,
    OrderSignal,
    Quote,
)
from arena.reporting.daily_report import (  # noqa: E402
    REPORTS_DIR,
    build_smart_market_summary,
    generate_and_save_daily_report,
    save_market_summary,
)

# Register remaining subpackages across both `arena.*` and `src.arena.*`
for _sub in (
    "agents",
    "agents.base",
    "agents.coded_agents",
    "agents.llm_agents",
    "data",
    "data.alpaca_client",
    "data.historical_provider",
    "engine",
    "engine.ledger",
    "engine.risk_manager",
    "engine.simulator",
    "reporting",
    "reporting.daily_report",
):
    _arena_key = f"arena.{_sub}"
    _src_key = f"src.arena.{_sub}"
    if _arena_key in sys.modules and _src_key not in sys.modules:
        sys.modules[_src_key] = sys.modules[_arena_key]
    elif _src_key in sys.modules and _arena_key not in sys.modules:
        sys.modules[_arena_key] = sys.modules[_src_key]

# Explicit alias matching prompt terminology
AlpacaPaperDataClient = AlpacaDataClient

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("arena.cli")


# ---------------------------------------------------------------------------
# Strategy Integrity Hash & State Persistence Helpers
# ---------------------------------------------------------------------------

def compute_all_strategy_hashes() -> Dict[str, str]:
    """Compute SHA-256 hashes of all strategy and execution engine files."""
    hashes = dict(compute_strategy_hashes())
    engine_files = {
        "risk_manager.py": _ARENA_DIR / "engine" / "risk_manager.py",
        "simulator.py": _ARENA_DIR / "engine" / "simulator.py",
        "coded_agents.py": _ARENA_DIR / "agents" / "coded_agents.py",
        "llm_agents.py": _ARENA_DIR / "agents" / "llm_agents.py",
        "style_agents.py": _ARENA_DIR / "agents" / "style_agents.py",
        "claude_rmp_agent.py": _ARENA_DIR / "agents" / "claude_rmp_agent.py",
        "claude_rmp_params.json": _ARENA_DIR / "agents" / "claude_rmp_params.json",
    }
    for name, path in engine_files.items():
        if hashes.get(name) in (None, "", "MISSING") and path.exists() and path.is_file():
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def build_competition_agents(
    allow_offline_llm_simulation: bool = False,
    force_offline_llm: bool = False,
    state_dir: Path = STATE_DIR,
) -> List[BaseAgent]:
    """Instantiate all competition agents (1-12 + 14) in canonical AGENT_SPECS order."""
    agents: List[BaseAgent] = []
    for spec in AGENT_SPECS:
        if spec.agent_id == "claude_rmp":
            from arena.agents.claude_rmp_agent import ClaudeRMPAgent

            agents.append(ClaudeRMPAgent(spec=spec, state_dir=state_dir))
        elif spec.agent_id in CODED_AND_CONTROL_AGENT_CLASSES:
            cls = CODED_AND_CONTROL_AGENT_CLASSES[spec.agent_id]
            agents.append(cls(spec=spec))
        elif spec.agent_id in LLM_AGENT_CLASSES:
            agent = create_llm_agent(
                agent_id=spec.agent_id,
                allow_offline_simulation=allow_offline_llm_simulation,
                api_key="" if force_offline_llm else None,
            )
            if force_offline_llm:
                # Ensure _get_api_key returns "" during offline dry-run so it uses
                # the deterministic persona simulation without consuming live quota
                agent._get_api_key = lambda: ""  # type: ignore[method-assign]
            agents.append(agent)
        else:
            raise ValueError(f"Unrecognized agent_id in AGENT_SPECS: {spec.agent_id}")
    return agents


def _competition_state_path(state_dir: Path = STATE_DIR) -> Path:
    return state_dir / "competition_state.json"


def _bug_fixes_log_path(state_dir: Path = STATE_DIR) -> Path:
    return state_dir / "bug_fixes_log.json"


def load_competition_state(state_dir: Path = STATE_DIR) -> Dict[str, Any]:
    """Load `state/competition_state.json` or return a fresh default structure."""
    path = _competition_state_path(state_dir)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception as exc:
            logger.warning("Failed to parse %s (%s); reinitializing.", path, exc)

    now_iso = format_iso_utc(datetime.now(timezone.utc))
    return {
        "status": "READY",
        "mode": "LIVE",
        "trading_days_completed": 0,
        "total_trading_days": COMPETITION_TRADING_DAYS,
        "active_trading_dates": [],
        "cycles_run": 0,
        "strategy_hashes": compute_all_strategy_hashes(),
        "strategy_integrity_verified": True,
        "started_at_utc": "",
        "completed_at_utc": "",
        "last_updated_utc": now_iso,
        "last_decision_timestamp_utc": "",
        "current_trading_date_et": "",
        "regime_summary": "Waiting for first market open cycle.",
    }


def save_competition_state(comp_state: Dict[str, Any], state_dir: Path = STATE_DIR) -> Path:
    """Persist `state/competition_state.json`."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = _competition_state_path(state_dir)
    with path.open("w", encoding="utf-8") as f:
        json.dump(comp_state, f, indent=2)
    return path


def load_bug_fixes_log(state_dir: Path = STATE_DIR) -> List[Dict[str, Any]]:
    """Load `state/bug_fixes_log.json` or initialize an empty list `[]`."""
    path = _bug_fixes_log_path(state_dir)
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    save_bug_fixes_log([], state_dir=state_dir)
    return []


def save_bug_fixes_log(entries: List[Dict[str, Any]], state_dir: Path = STATE_DIR) -> Path:
    """Persist `state/bug_fixes_log.json`."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = _bug_fixes_log_path(state_dir)
    with path.open("w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    return path


def _rebuild_dashboard(
    agent_states: List[AgentLedgerState],
    competition_state: Dict[str, Any],
    bug_fixes: List[Dict[str, Any]],
    output_dir: Path = DOCS_DIR,
    state_dir: Optional[Path] = None,
    reports_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Invoke DashboardBuilder.build_site to generate the static GitHub Pages site."""
    try:
        from arena.dashboard.builder import DashboardBuilder

        builder = DashboardBuilder()
        sig = inspect.signature(builder.build_site)
        params = sig.parameters
        kwargs: Dict[str, Any] = {}
        if "agent_states" in params:
            kwargs["agent_states"] = agent_states
        elif "states" in params:
            kwargs["states"] = agent_states
        if "competition_state" in params:
            kwargs["competition_state"] = competition_state
        if "bug_fixes" in params:
            kwargs["bug_fixes"] = bug_fixes
        elif "bug_fixes_log" in params:
            kwargs["bug_fixes_log"] = bug_fixes
        if "output_dir" in params:
            kwargs["output_dir"] = output_dir
        elif "docs_dir" in params:
            kwargs["docs_dir"] = output_dir
        if "state_dir" in params and state_dir is not None:
            kwargs["state_dir"] = state_dir
        if "reports_dir" in params and reports_dir is not None:
            kwargs["reports_dir"] = reports_dir

        if len(kwargs) >= 4:
            return builder.build_site(**kwargs)
        return builder.build_site(agent_states, competition_state, bug_fixes, output_dir)
    except Exception as exc:
        logger.warning("Dashboard build warning (%s): %s", type(exc).__name__, exc)
        return None


def _resolve_reports_dir(state_dir: Path, reports_dir: Optional[Path]) -> Path:
    if reports_dir is not None:
        return Path(reports_dir)
    if state_dir.resolve() == STATE_DIR.resolve():
        return REPORTS_DIR
    return state_dir.parent / "reports"


# ---------------------------------------------------------------------------
# State Reset & Initialization
# ---------------------------------------------------------------------------

def init_or_reset_state(
    mode: str = "LIVE",
    state_dir: Path = STATE_DIR,
    docs_dir: Path = DOCS_DIR,
    preserve_bug_fixes: bool = True,
    rebuild_docs: bool = True,
    reports_dir: Optional[Path] = None,
    clear_reports: bool = True,
) -> Dict[str, Any]:
    """Reset all active agents to $10,000.00 cash, 0 trades, Day 0/10, and rebuild `docs/`."""
    state_dir.mkdir(parents=True, exist_ok=True)
    agents_dir = state_dir / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    now_iso = format_iso_utc(now_utc)
    today_et = to_et_datetime(now_utc).strftime("%Y-%m-%d")

    ledger = LedgerManager(state_dir=agents_dir)
    agent_states: List[AgentLedgerState] = []

    for spec in AGENT_SPECS:
        state = ledger.initialize_agent(
            spec,
            initial_cash=INITIAL_CASH,
            timestamp=now_iso,
            trading_date_et="",
            save=False,
        )
        # Seed initial $10,000.00 starting point on the equity curve
        state.equity_curve = [
            EquityPoint(
                timestamp=now_iso,
                trading_date_et=today_et,
                equity=round(INITIAL_CASH, 2),
                cash=round(INITIAL_CASH, 2),
                positions_value=0.0,
                drawdown_pct=0.0,
                daily_return_pct=0.0,
            )
        ]
        ledger.save_agent_state(state)
        agent_states.append(state)

    comp_state: Dict[str, Any] = {
        "status": "READY",
        "mode": mode,
        "trading_days_completed": 0,
        "total_trading_days": COMPETITION_TRADING_DAYS,
        "active_trading_dates": [],
        "cycles_run": 0,
        "strategy_hashes": compute_all_strategy_hashes(),
        "strategy_integrity_verified": True,
        "started_at_utc": "",
        "completed_at_utc": "",
        "last_updated_utc": now_iso,
        "last_decision_timestamp_utc": "",
        "current_trading_date_et": "",
        "regime_summary": "Ready for Day 1 market open.",
    }
    save_competition_state(comp_state, state_dir=state_dir)

    # Reset Agent 14 (Claude RMP) internal daily cache, cooldowns, and cycle telemetry logs
    from arena.agents.claude_rmp_agent import ClaudeRMPAgent

    ClaudeRMPAgent(state_dir=state_dir).reset_internal_state()

    # Reset daily report log & cached market summary cleanly for Day 0
    (state_dir / "daily_reports_log.json").write_text("[]\n", encoding="utf-8")
    ms_cache = state_dir / "last_market_summary.json"
    if ms_cache.exists():
        ms_cache.unlink()

    target_reports = _resolve_reports_dir(state_dir, reports_dir)
    if clear_reports:
        daily_dir = target_reports / "daily"
        if daily_dir.exists():
            for md_file in daily_dir.glob("*.md"):
                md_file.unlink()
        latest_md = target_reports / "LATEST_REPORT.md"
        if latest_md.exists():
            latest_md.unlink()

    bug_fixes_path = _bug_fixes_log_path(state_dir)
    if preserve_bug_fixes and bug_fixes_path.exists():
        bug_fixes = load_bug_fixes_log(state_dir=state_dir)
    else:
        bug_fixes = []
        save_bug_fixes_log(bug_fixes, state_dir=state_dir)

    if rebuild_docs:
        _rebuild_dashboard(
            agent_states=agent_states,
            competition_state=comp_state,
            bug_fixes=bug_fixes,
            output_dir=docs_dir,
            state_dir=state_dir,
            reports_dir=target_reports,
        )

    return comp_state


# ---------------------------------------------------------------------------
# Competition Finalization (After Day 10)
# ---------------------------------------------------------------------------

def finalize_competition(
    agent_states: List[AgentLedgerState],
    effective_quotes: Dict[str, Quote],
    mark_prices: Dict[str, float],
    decision_timestamp: str,
    fill_timestamp: str,
    trading_date_et: str,
    trading_day_number: int,
    competition_state: Dict[str, Any],
    ledger: LedgerManager,
    simulator: FillSimulator,
) -> None:
    """Close all open positions across all agents at the final price and mark competition COMPLETED."""
    logger.info(
        "Finalizing 10-day competition at %s (Day %d): closing all open positions at final prices.",
        fill_timestamp,
        trading_day_number,
    )
    for state in agent_states:
        if state.positions:
            ledger.flatten_all_positions(
                state=state,
                quotes_by_ticker=effective_quotes,
                decision_timestamp=decision_timestamp,
                fill_timestamp=fill_timestamp,
                exit_reason="COMPETITION_END",
                simulator=simulator,
            )
            ledger.update_mark_to_market(
                state=state,
                prices_by_ticker=mark_prices,
                timestamp=fill_timestamp,
                trading_date_et=trading_date_et,
            )

        if state.status != "BLOWN_UP":
            state.status = "COMPLETED"
            state.status_reason = "10-day competition completed; all positions closed at final price"

        ledger.update_daily_journal(
            state=state,
            trading_date_et=trading_date_et,
            trading_day_number=trading_day_number,
        )
        ledger.save_agent_state(state)

    competition_state["status"] = "COMPLETED"
    competition_state["completed_at_utc"] = fill_timestamp
    competition_state["last_updated_utc"] = fill_timestamp


# ---------------------------------------------------------------------------
# Single Cycle Orchestrator
# ---------------------------------------------------------------------------

def _build_effective_fill_quotes_and_marks(
    snapshot: MarketSnapshot,
    next_quotes: Dict[str, Quote],
    next_bars: Dict[str, Bar],
    fill_timestamp: str,
) -> tuple[Dict[str, Quote], Dict[str, float]]:
    """Build complete fill-time quotes and mark-to-market prices strictly at fill_timestamp."""
    effective_quotes: Dict[str, Quote] = {}
    mark_prices: Dict[str, float] = {}
    half_spread_rate = (DEFAULT_SPREAD_BPS / 2.0) / 10_000.0

    all_tickers = set(UNIVERSE) | set(snapshot.quotes_by_ticker.keys()) | set(next_quotes.keys()) | set(next_bars.keys())

    for ticker in all_tickers:
        q = next_quotes.get(ticker)
        b = next_bars.get(ticker)
        snap_q = snapshot.quotes_by_ticker.get(ticker)
        snap_ind = snapshot.indicators.get(ticker)

        if q is not None and q.mid_price > 0:
            bid = q.bid_price if q.bid_price > 0 else round(q.mid_price * (1.0 - half_spread_rate), 4)
            ask = q.ask_price if q.ask_price >= bid else round(q.mid_price * (1.0 + half_spread_rate), 4)
            effective_quotes[ticker] = Quote(
                ticker=ticker,
                timestamp=fill_timestamp,
                bid_price=bid,
                ask_price=ask,
                mid_price=q.mid_price,
                spread_bps=q.spread_bps if q.spread_bps > 0 else DEFAULT_SPREAD_BPS,
            )
        elif b is not None and (b.open > 0 or b.close > 0):
            ref = b.open if b.open > 0 else b.close
            effective_quotes[ticker] = Quote(
                ticker=ticker,
                timestamp=fill_timestamp,
                bid_price=round(ref * (1.0 - half_spread_rate), 4),
                ask_price=round(ref * (1.0 + half_spread_rate), 4),
                mid_price=round(ref, 4),
                spread_bps=DEFAULT_SPREAD_BPS,
            )
        elif snap_q is not None and snap_q.mid_price > 0:
            effective_quotes[ticker] = Quote(
                ticker=ticker,
                timestamp=fill_timestamp,
                bid_price=snap_q.bid_price,
                ask_price=snap_q.ask_price,
                mid_price=snap_q.mid_price,
                spread_bps=snap_q.spread_bps,
            )
        elif snap_ind is not None and snap_ind.last_price > 0:
            ref = snap_ind.last_price
            effective_quotes[ticker] = Quote(
                ticker=ticker,
                timestamp=fill_timestamp,
                bid_price=round(ref * (1.0 - half_spread_rate), 4),
                ask_price=round(ref * (1.0 + half_spread_rate), 4),
                mid_price=round(ref, 4),
                spread_bps=DEFAULT_SPREAD_BPS,
            )

        # Mark price at end of cycle prefers next_bar.close if available, else effective quote mid
        if b is not None and b.close > 0:
            mark_prices[ticker] = round(b.close, 4)
        elif ticker in effective_quotes:
            mark_prices[ticker] = round(effective_quotes[ticker].mid_price, 4)

    return effective_quotes, mark_prices


def run_single_cycle(
    snapshot: MarketSnapshot,
    next_quotes: Dict[str, Quote],
    next_bars: Dict[str, Bar],
    fill_timestamp: str,
    agents: List[BaseAgent],
    state_dir: Path = STATE_DIR,
    docs_dir: Path = DOCS_DIR,
    rebuild_dashboard: bool = True,
    reports_dir: Optional[Path] = None,
    generate_daily_report: bool = True,
    use_llm_for_report: bool = True,
) -> Dict[str, Any]:
    """Execute one complete trading cycle across all active agents and update ledgers, reports & dashboard."""
    # Guarantee strict no-look-ahead timestamp ordering
    dec_dt = parse_iso_utc(snapshot.decision_timestamp)
    fill_dt = parse_iso_utc(fill_timestamp) if fill_timestamp else (dec_dt + timedelta(seconds=1))
    if fill_dt <= dec_dt:
        fill_dt = dec_dt + timedelta(seconds=1)
    fill_timestamp = format_iso_utc(fill_dt)
    FillSimulator.verify_no_lookahead(snapshot.decision_timestamp, fill_timestamp)

    comp_state = load_competition_state(state_dir=state_dir)
    bug_fixes = load_bug_fixes_log(state_dir=state_dir)

    # 1. Update trading day tracking in competition_state.json
    active_dates: List[str] = list(comp_state.get("active_trading_dates") or [])
    if snapshot.trading_date_et and snapshot.trading_date_et not in active_dates:
        active_dates.append(snapshot.trading_date_et)
        active_dates.sort()
    trading_days_completed = len(active_dates)
    snapshot.trading_day_number = max(1, trading_days_completed)

    comp_state["active_trading_dates"] = active_dates
    comp_state["trading_days_completed"] = trading_days_completed
    comp_state["total_trading_days"] = COMPETITION_TRADING_DAYS
    comp_state["cycles_run"] = int(comp_state.get("cycles_run") or 0) + 1
    comp_state["last_updated_utc"] = fill_timestamp
    comp_state["last_decision_timestamp_utc"] = snapshot.decision_timestamp
    comp_state["current_trading_date_et"] = snapshot.trading_date_et
    comp_state["regime_summary"] = snapshot.regime_summary
    if not comp_state.get("started_at_utc"):
        comp_state["started_at_utc"] = snapshot.decision_timestamp
    if comp_state.get("status") == "READY":
        comp_state["status"] = "DRY_RUN" if comp_state.get("mode") == "DRY_RUN" else "LIVE"

    # 2. Verify strategy SHA-256 hashes against stored competition hashes
    current_hashes = compute_all_strategy_hashes()
    stored_hashes: Dict[str, str] = dict(comp_state.get("strategy_hashes") or {})
    integrity_ok = True
    for fname, cur_hash in current_hashes.items():
        prev_hash = stored_hashes.get(fname, "MISSING")
        if prev_hash in ("", "MISSING") and cur_hash != "MISSING":
            stored_hashes[fname] = cur_hash
        elif prev_hash not in ("", "MISSING") and cur_hash != "MISSING" and prev_hash != cur_hash:
            integrity_ok = False
            logger.warning(
                "STRATEGY INTEGRITY WARNING: '%s' hash changed from %s... to %s... after start! "
                "Any bug fix must be documented in state/bug_fixes_log.json.",
                fname,
                prev_hash[:12],
                cur_hash[:12],
            )
    comp_state["strategy_hashes"] = stored_hashes
    comp_state["strategy_integrity_verified"] = integrity_ok

    # 3. Prepare fill quotes and mark-to-market prices
    effective_quotes, mark_prices = _build_effective_fill_quotes_and_marks(
        snapshot=snapshot,
        next_quotes=next_quotes,
        next_bars=next_bars,
        fill_timestamp=fill_timestamp,
    )

    ledger = LedgerManager(state_dir=state_dir / "agents")
    risk_manager = RiskManager()
    simulator = FillSimulator()

    agent_states: List[AgentLedgerState] = []

    # 4. Run each agent through the 9-step cycle pipeline
    for agent in agents:
        spec = agent.spec
        # Step 1: Load AgentLedgerState and run start_of_day_check
        state = ledger.load_or_initialize_agent(spec)
        ledger.start_of_day_check(state, snapshot.trading_date_et)

        # Step 2: Check intrabar Stop-Loss and Take-Profit triggers on existing positions
        if state.positions and next_bars:
            simulator.check_and_execute_stops_and_targets(
                state=state,
                next_bars_by_ticker=next_bars,
                quotes_by_ticker=effective_quotes,
                decision_timestamp=snapshot.decision_timestamp,
                fill_timestamp=fill_timestamp,
                apply_to_state=True,
            )

        # Step 3: If flatten_eod is True (coded_orb) and near market close, flatten open positions
        if spec.flatten_eod and snapshot.is_near_market_close:
            if state.positions:
                ledger.flatten_all_positions(
                    state=state,
                    quotes_by_ticker=effective_quotes,
                    decision_timestamp=snapshot.decision_timestamp,
                    fill_timestamp=fill_timestamp,
                    exit_reason="EOD_FLAT",
                    simulator=simulator,
                )
        # Step 4: If state.status == "ACTIVE" (and not near-close for flatten_eod), agent decides
        elif state.status == "ACTIVE":
            try:
                if hasattr(agent, "decide_batch") and callable(getattr(agent, "decide_batch")):
                    signals_batch = list(agent.decide_batch(snapshot, state) or [])
                else:
                    signals_batch = [agent.decide(snapshot, state)]
            except Exception as exc:
                logger.warning("Agent %s decide() error: %s; defaulting to HOLD.", agent.agent_id, exc)
                signals_batch = [
                    agent.hold_signal(
                        snapshot,
                        reasoning=f"HOLD fallback due to runtime exception: {type(exc).__name__}",
                    )
                ]

            for signal in signals_batch:
                if not signal.decision_timestamp:
                    signal.decision_timestamp = snapshot.decision_timestamp

                # Step 5: Pass OrderSignal through shared RiskManager (plus stricter agent limits where defined)
                ticker = (signal.ticker or "").strip().upper()
                decision_quote: Optional[Quote] = (
                    snapshot.quotes_by_ticker.get(ticker) or effective_quotes.get(ticker)
                    if ticker
                    else None
                )

                extra_stricter_limits = None
                if hasattr(agent, "get_effective_stricter_limits") and callable(getattr(agent, "get_effective_stricter_limits")):
                    extra_stricter_limits = agent.get_effective_stricter_limits()

                risk_result = risk_manager.evaluate(
                    signal=signal,
                    state=state,
                    quote=decision_quote,
                    bypass_position_cap=spec.bypass_position_cap,
                    stricter_limits=extra_stricter_limits,
                )

                # Step 6: If approved, simulate fill at fill_timestamp (> decision_timestamp) and record in ledger
                if risk_result.approved and signal.action in ("BUY", "SELL"):
                    fill_quote = effective_quotes.get(ticker) or decision_quote
                    if fill_quote is not None:
                        exit_code = "AGENT_SIGNAL"
                        if signal.action == "SELL" and signal.reasoning:
                            for code_prefix in ("EXIT_STOP", "EXIT_TRAIL", "EXIT_TIME", "EXIT_SMA50", "EXIT_REGIME"):
                                if signal.reasoning.startswith(code_prefix):
                                    exit_code = code_prefix
                                    break
                        trade = simulator.simulate_fill(
                            order_or_check=risk_result,
                            fill_quote_or_bar=fill_quote,
                            fill_timestamp=fill_timestamp,
                            state=state,
                            exit_reason=exit_code,
                            decision_timestamp=snapshot.decision_timestamp,
                        )
                        ledger.apply_trade(state, trade)

        # Step 7: Update mark-to-market equity and enforce -3% daily / -10% blown-up circuit breakers
        ledger.update_mark_to_market(
            state=state,
            prices_by_ticker=mark_prices,
            timestamp=fill_timestamp,
            trading_date_et=snapshot.trading_date_et,
        )

        # If newly BLOWN_UP while still holding positions, liquidate remaining positions
        if state.status == "BLOWN_UP" and state.positions:
            ledger.flatten_all_positions(
                state=state,
                quotes_by_ticker=effective_quotes,
                decision_timestamp=snapshot.decision_timestamp,
                fill_timestamp=fill_timestamp,
                exit_reason="BLOWN_UP",
                simulator=simulator,
            )
            ledger.update_mark_to_market(
                state=state,
                prices_by_ticker=mark_prices,
                timestamp=fill_timestamp,
                trading_date_et=snapshot.trading_date_et,
            )

        # Step 8: Update plain-English DailyJournalEntry for snapshot.trading_date_et
        ledger.update_daily_journal(
            state=state,
            trading_date_et=snapshot.trading_date_et,
            trading_day_number=snapshot.trading_day_number,
        )

        # Step 9: Save AgentLedgerState
        ledger.save_agent_state(state)
        agent_states.append(state)

    # 5. Check if 10th trading day has completed
    if trading_days_completed >= COMPETITION_TRADING_DAYS and snapshot.is_near_market_close:
        finalize_competition(
            agent_states=agent_states,
            effective_quotes=effective_quotes,
            mark_prices=mark_prices,
            decision_timestamp=snapshot.decision_timestamp,
            fill_timestamp=fill_timestamp,
            trading_date_et=snapshot.trading_date_et,
            trading_day_number=trading_days_completed,
            competition_state=comp_state,
            ledger=ledger,
            simulator=simulator,
        )

    save_competition_state(comp_state, state_dir=state_dir)

    # 6. Cache smart-filtered market summary every cycle, and generate verified daily Markdown report at close
    target_reports = _resolve_reports_dir(state_dir, reports_dir)
    market_summary: Optional[Dict[str, Any]] = None
    try:
        rmp_logs_path = state_dir / "claude_rmp_cycle_logs.json"
        rmp_logs = json.loads(rmp_logs_path.read_text(encoding="utf-8")) if rmp_logs_path.exists() else []
        market_summary = build_smart_market_summary(
            snapshot=snapshot,
            agent_states=agent_states,
            claude_rmp_logs=rmp_logs if isinstance(rmp_logs, list) else [],
        )
        save_market_summary(market_summary, state_dir=state_dir)
    except Exception as exc:
        logger.warning("Market summary cache warning (%s): %s", type(exc).__name__, exc)

    if generate_daily_report and (snapshot.is_near_market_close or comp_state.get("status") == "COMPLETED"):
        try:
            rep_res = generate_and_save_daily_report(
                agent_states=agent_states,
                competition_state=comp_state,
                trading_date_et=snapshot.trading_date_et,
                trading_day_number=snapshot.trading_day_number,
                market_summary=market_summary,
                state_dir=state_dir,
                reports_dir=target_reports,
                use_llm=use_llm_for_report,
            )
            logger.info(
                "Daily Markdown report generated (%s): %s",
                rep_res.get("generator"),
                rep_res.get("report_path"),
            )
        except Exception as exc:
            logger.warning("Daily Markdown report generation warning (%s): %s", type(exc).__name__, exc)

    if rebuild_dashboard:
        _rebuild_dashboard(
            agent_states=agent_states,
            competition_state=comp_state,
            bug_fixes=bug_fixes,
            output_dir=docs_dir,
            state_dir=state_dir,
            reports_dir=target_reports,
        )

    return comp_state


# ---------------------------------------------------------------------------
# Leaderboard Console Formatter
# ---------------------------------------------------------------------------

def format_leaderboard_table(
    agent_states: List[AgentLedgerState],
    title: str = "AI TRADING ARENA — LEADERBOARD",
) -> str:
    """Format a rich ASCII leaderboard table sorted by equity descending."""
    spy_state = next((s for s in agent_states if s.agent_id == "ctrl_spy"), None)
    spy_return_pct = (
        ((spy_state.equity - spy_state.initial_cash) / spy_state.initial_cash) * 100.0
        if (spy_state and spy_state.initial_cash > 0)
        else 0.0
    )

    metrics_list = [compute_leaderboard_metrics(s, spy_return_pct=spy_return_pct) for s in agent_states]
    metrics_list.sort(key=lambda m: (m["equity"], m["profit_pct"]), reverse=True)

    lines: List[str] = []
    sep = "=" * 124
    lines.append(sep)
    lines.append(f"  {title}")
    lines.append(sep)
    header = (
        f"{'Rank':<5} {'Agent Name':<36} {'Status':<13} {'Equity':>11} "
        f"{'Profit ($)':>11} {'Return':>8} {'vs SPY':>8} {'Max DD':>8} "
        f"{'Trades':>7} {'WinRate':>8} {'PF':>6}"
    )
    lines.append(header)
    lines.append("-" * 124)

    for rank, m in enumerate(metrics_list, start=1):
        profit_str = f"{'+' if m['profit_dollars'] >= 0 else ''}${m['profit_dollars']:,.2f}"
        ret_str = f"{m['profit_pct']:+.2f}%"
        vs_spy_str = f"{m['return_vs_spy_pct']:+.2f}%"
        dd_str = f"{m['max_drawdown_pct']:.2f}%"
        wr_str = f"{m['win_rate_pct']:.1f}%"
        pf_str = f"{m['profit_factor']:.2f}" if m["closed_trades_count"] > 0 else "N/A"
        trades_str = f"{m['trades_count']}({m['closed_trades_count']})"
        lines.append(
            f"#{rank:<4} {m['name'][:35]:<36} {m['status']:<13} "
            f"${m['equity']:>10,.2f} {profit_str:>11} {ret_str:>8} {vs_spy_str:>8} "
            f"{dd_str:>8} {trades_str:>7} {wr_str:>8} {pf_str:>6}"
        )

    lines.append(sep)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI Subcommand Implementations
# ---------------------------------------------------------------------------

def cmd_run_cycle(args: argparse.Namespace) -> int:
    """Live cycle entrypoint invoked by GitHub Actions every 30 minutes."""
    load_dotenv_if_present()
    assert_paper_trading_only()

    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else STATE_DIR
    docs_dir = Path(args.docs_dir) if getattr(args, "docs_dir", None) else DOCS_DIR

    comp_state = load_competition_state(state_dir=state_dir)
    bug_fixes = load_bug_fixes_log(state_dir=state_dir)
    ledger = LedgerManager(state_dir=state_dir / "agents")
    agent_states_map = ledger.initialize_all_agents(initial_cash=INITIAL_CASH, overwrite=False)
    agent_states = [agent_states_map[spec.agent_id] for spec in AGENT_SPECS]

    # 1. If competition is already COMPLETED, refresh dashboard and exit cleanly
    if comp_state.get("status") == "COMPLETED":
        logger.info("Competition is already marked COMPLETED (10/10 trading days finished). Exiting cleanly.")
        _rebuild_dashboard(agent_states, comp_state, bug_fixes, output_dir=docs_dir)
        return 0

    client = AlpacaPaperDataClient()
    clock = client.get_clock()
    now_utc = datetime.now(timezone.utc)
    now_iso = str(clock.get("timestamp") or format_iso_utc(now_utc))
    today_et = to_et_datetime(now_iso).strftime("%Y-%m-%d")
    calendar = client.get_calendar(today_et, (date.fromisoformat(today_et) + timedelta(days=7)).isoformat())

    is_open = bool(clock.get("is_open", False))
    force_open = bool(getattr(args, "force_open_check", False))

    # 2. Check if 10 trading days have already elapsed and competition needs finalization
    active_dates: List[str] = list(comp_state.get("active_trading_dates") or [])
    if len(active_dates) >= COMPETITION_TRADING_DAYS and today_et not in active_dates:
        logger.info("10 US trading days have already elapsed (%s); finalizing competition.", active_dates)
        simulator = FillSimulator()
        snapshot = client.fetch_market_snapshot(
            decision_timestamp=now_iso,
            trading_day_number=len(active_dates),
        )
        next_quotes, next_bars = client.fetch_next_fill_quotes_and_bars(UNIVERSE, now_iso)
        fill_iso = format_iso_utc(parse_iso_utc(now_iso) + timedelta(seconds=1))
        eff_quotes, mark_prices = _build_effective_fill_quotes_and_marks(
            snapshot, next_quotes, next_bars, fill_iso
        )
        finalize_competition(
            agent_states=agent_states,
            effective_quotes=eff_quotes,
            mark_prices=mark_prices,
            decision_timestamp=now_iso,
            fill_timestamp=fill_iso,
            trading_date_et=active_dates[-1],
            trading_day_number=len(active_dates),
            competition_state=comp_state,
            ledger=ledger,
            simulator=simulator,
        )
        save_competition_state(comp_state, state_dir=state_dir)
        target_reports = _resolve_reports_dir(state_dir, None)
        try:
            generate_and_save_daily_report(
                agent_states=agent_states,
                competition_state=comp_state,
                trading_date_et=active_dates[-1],
                trading_day_number=len(active_dates),
                state_dir=state_dir,
                reports_dir=target_reports,
                use_llm=True,
            )
        except Exception as exc:
            logger.warning("Final Day-10 daily report warning (%s): %s", type(exc).__name__, exc)
        _rebuild_dashboard(
            agent_states,
            comp_state,
            bug_fixes,
            output_dir=docs_dir,
            state_dir=state_dir,
            reports_dir=target_reports,
        )
        return 0

    # Record non-secret boolean connection status so setup can be verified transparently
    comp_state["api_keys_status"] = {
        "alpaca_paper_configured": client.has_credentials(),
        "gemini_configured": bool((os.environ.get("GEMINI_API_KEY") or "").strip()),
        "checked_at_utc": format_iso_utc(now_utc),
    }

    # 3. If market is closed and --force-open-check was not passed, log clearly and refresh dashboard timestamps
    if not is_open and not force_open:
        next_open = clock.get("next_open", "unknown")
        is_holiday_or_weekend = not any(c.get("date") == today_et for c in calendar)
        reason_str = (
            "US market holiday or weekend (e.g., Thanksgiving / weekend)"
            if is_holiday_or_weekend
            else "Outside regular US trading session (09:30–16:00 ET)"
        )
        logger.info(
            "US stock market is currently CLOSED (%s). Next open: %s. Refreshing dashboard timestamps.",
            reason_str,
            next_open,
        )

        # Post-close catch-up: if the most recent active trading date does not yet have a daily report
        # (e.g. if GitHub Actions cron delayed past the 15:30 ET cycle), generate it now!
        target_reports = _resolve_reports_dir(state_dir, None)
        if active_dates:
            latest_active_date = active_dates[-1]
            log_path = state_dir / "daily_reports_log.json"
            existing_dates = set()
            if log_path.exists():
                try:
                    loaded_log = json.loads(log_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_log, list):
                        existing_dates = {str(e.get("trading_date_et", "")) for e in loaded_log}
                except Exception:
                    existing_dates = set()
            if latest_active_date not in existing_dates:
                now_et_dt = to_et_datetime(now_utc)
                _, sess_close_et = get_session_hours_et(date.fromisoformat(latest_active_date))
                is_past_close = (latest_active_date < today_et) or (now_et_dt.strftime("%H:%M") >= sess_close_et)
                if is_past_close:
                    try:
                        rep_res = generate_and_save_daily_report(
                            agent_states=agent_states,
                            competition_state=comp_state,
                            trading_date_et=latest_active_date,
                            trading_day_number=len(active_dates),
                            state_dir=state_dir,
                            reports_dir=target_reports,
                            use_llm=True,
                        )
                        logger.info(
                            "Post-close catch-up daily report generated (%s): %s",
                            rep_res.get("generator"),
                            rep_res.get("report_path"),
                        )
                    except Exception as exc:
                        logger.warning("Post-close catch-up report warning (%s): %s", type(exc).__name__, exc)

        comp_state["last_updated_utc"] = format_iso_utc(now_utc)
        save_competition_state(comp_state, state_dir=state_dir)
        _rebuild_dashboard(
            agent_states,
            comp_state,
            bug_fixes,
            output_dir=docs_dir,
            state_dir=state_dir,
            reports_dir=target_reports,
        )
        return 0

    # 4. Hard live-safety check: NEVER trade on synthetic offline data when Alpaca credentials are missing
    if not client.has_credentials():
        logger.error(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY are NOT configured in environment or GitHub Secrets. "
            "Refusing to execute live trades on synthetic fallback data. Please add your Alpaca Paper API keys."
        )
        comp_state["last_updated_utc"] = format_iso_utc(now_utc)
        save_competition_state(comp_state, state_dir=state_dir)
        _rebuild_dashboard(agent_states, comp_state, bug_fixes, output_dir=docs_dir)
        return 0

    # 5. Market is OPEN (or forced for verification): execute live cycle
    decision_timestamp = format_iso_utc(parse_iso_utc(now_iso))
    prospective_dates = sorted(set(active_dates + [today_et]))
    day_number = len(prospective_dates)

    logger.info(
        "Running LIVE trading cycle — Decision timestamp: %s (ET date: %s, Day %d/%d, forced=%s)",
        decision_timestamp,
        today_et,
        day_number,
        COMPETITION_TRADING_DAYS,
        force_open,
    )

    snapshot = client.fetch_market_snapshot(
        decision_timestamp=decision_timestamp,
        trading_day_number=day_number,
    )
    if not snapshot.bars_by_ticker.get("SPY"):
        logger.error(
            "Alpaca API returned no live SPY bars for %s (check paper API key validity or market status). "
            "Skipping trade execution to prevent trading on synthetic data.",
            decision_timestamp,
        )
        comp_state["last_updated_utc"] = format_iso_utc(now_utc)
        save_competition_state(comp_state, state_dir=state_dir)
        _rebuild_dashboard(agent_states, comp_state, bug_fixes, output_dir=docs_dir)
        return 0

    next_quotes, next_bars = client.fetch_next_fill_quotes_and_bars(
        tickers=UNIVERSE,
        after_decision_timestamp=decision_timestamp,
    )
    fill_dt = max(
        datetime.now(timezone.utc),
        parse_iso_utc(decision_timestamp) + timedelta(seconds=1),
    )
    fill_timestamp = format_iso_utc(fill_dt)

    agents = build_competition_agents(allow_offline_llm_simulation=False, force_offline_llm=False)
    updated_comp = run_single_cycle(
        snapshot=snapshot,
        next_quotes=next_quotes,
        next_bars=next_bars,
        fill_timestamp=fill_timestamp,
        agents=agents,
        state_dir=state_dir,
        docs_dir=docs_dir,
        rebuild_dashboard=True,
    )

    final_states = [ledger.load_agent_state(spec.agent_id) for spec in AGENT_SPECS]
    print(
        format_leaderboard_table(
            final_states,
            title=(
                f"LIVE CYCLE COMPLETE — Day {updated_comp['trading_days_completed']}/{COMPETITION_TRADING_DAYS} "
                f"(Cycle #{updated_comp['cycles_run']})"
            ),
        )
    )
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Replay the last 5 US trading days of 30-minute bars through the entire system."""
    load_dotenv_if_present()
    assert_paper_trading_only()

    num_days = int(getattr(args, "days", 5) or 5)
    reset_after = bool(getattr(args, "reset_after", False))
    use_live_llm = bool(getattr(args, "use_live_llm", False))
    offline_data = bool(getattr(args, "offline", False))

    logger.info(
        "Starting %d-trading-day historical DRY RUN replay (reset_after=%s)...",
        num_days,
        reset_after,
    )

    # 1. Reset state cleanly in DRY_RUN mode (write dry-run reports into docs_dry_run/reports so live reports/ stays untouched)
    dry_run_reports_dir = DRY_RUN_DOCS_DIR / "reports"
    init_or_reset_state(
        mode="DRY_RUN",
        state_dir=STATE_DIR,
        docs_dir=DOCS_DIR,
        preserve_bug_fixes=True,
        rebuild_docs=False,
        reports_dir=dry_run_reports_dir,
        clear_reports=True,
    )

    # 2. Build replay provider and agents
    provider = HistoricalReplayProvider(offline=offline_data)
    cycles = provider.get_replay_cycles(num_days=num_days)
    if not cycles:
        provider = HistoricalReplayProvider(offline=True)
        cycles = provider.get_replay_cycles(num_days=num_days)

    agents = build_competition_agents(
        allow_offline_llm_simulation=True,
        force_offline_llm=not use_live_llm,
    )

    total_cycles = len(cycles)
    logger.info("Replaying %d 30-minute market cycles across %d trading days...", total_cycles, num_days)

    comp_state: Dict[str, Any] = {}
    for idx, (snapshot, next_bars, next_quotes, fill_timestamp) in enumerate(cycles, start=1):
        is_last_cycle = idx == total_cycles
        comp_state = run_single_cycle(
            snapshot=snapshot,
            next_quotes=next_quotes,
            next_bars=next_bars,
            fill_timestamp=fill_timestamp,
            agents=agents,
            state_dir=STATE_DIR,
            docs_dir=DOCS_DIR,
            rebuild_dashboard=is_last_cycle,
            reports_dir=dry_run_reports_dir,
            generate_daily_report=True,
            use_llm_for_report=use_live_llm,
        )
        if snapshot.is_near_market_close or is_last_cycle:
            logger.info(
                "  Completed Day %d (%s) — Cycle %d/%d",
                comp_state.get("trading_days_completed", snapshot.trading_day_number),
                snapshot.trading_date_et,
                idx,
                total_cycles,
            )

    ledger = LedgerManager(state_dir=STATE_DIR / "agents")
    dry_run_states = [ledger.load_agent_state(spec.agent_id) for spec in AGENT_SPECS]
    bug_fixes = load_bug_fixes_log(state_dir=STATE_DIR)

    # 3. Save the dry-run dashboard to BOTH `docs_dry_run/` and `docs/`
    DRY_RUN_DOCS_DIR.mkdir(parents=True, exist_ok=True)
    _rebuild_dashboard(
        agent_states=dry_run_states,
        competition_state=comp_state,
        bug_fixes=bug_fixes,
        output_dir=DRY_RUN_DOCS_DIR,
        state_dir=STATE_DIR,
        reports_dir=dry_run_reports_dir,
    )
    _rebuild_dashboard(
        agent_states=dry_run_states,
        competition_state=comp_state,
        bug_fixes=bug_fixes,
        output_dir=DOCS_DIR,
        state_dir=STATE_DIR,
        reports_dir=dry_run_reports_dir,
    )

    # Also save a snapshot of dry-run JSON ledgers & Agent 13 rulebook history inside `docs_dry_run/state_snapshot/`
    snapshot_dir = DRY_RUN_DOCS_DIR / "state_snapshot"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    with (snapshot_dir / "competition_state.json").open("w", encoding="utf-8") as f:
        json.dump(comp_state, f, indent=2)
    a14_logs_src = STATE_DIR / "claude_rmp_cycle_logs.json"
    if a14_logs_src.exists():
        (snapshot_dir / "claude_rmp_cycle_logs.json").write_text(
            a14_logs_src.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    print(
        format_leaderboard_table(
            dry_run_states,
            title=f"5-DAY HISTORICAL DRY RUN RESULTS ({total_cycles} 30-Min Cycles Replayed)",
        )
    )
    logger.info("Dry-run dashboard generated in: %s", DRY_RUN_DOCS_DIR / "index.html")
    logger.info("Main dashboard updated in:      %s", DOCS_DIR / "index.html")

    # 4. If --reset-after is passed, reset `state/` and `docs/` back to $10,000 Day 0 while keeping `docs_dry_run/`
    if reset_after:
        logger.info(
            "--reset-after specified: Resetting live `state/` and `docs/` cleanly to $10,000.00 (Day 0/10) "
            "while preserving `docs_dry_run/` intact."
        )
        init_or_reset_state(
            mode="LIVE",
            state_dir=STATE_DIR,
            docs_dir=DOCS_DIR,
            preserve_bug_fixes=True,
            rebuild_docs=True,
        )
        logger.info("Live state reset complete. Ready for Day 1 live market open.")

    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Reset `state/` and `docs/` cleanly to zero ($10,000.00 per agent, 0 trades, 0/10 days)."""
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else STATE_DIR
    docs_dir = Path(args.docs_dir) if getattr(args, "docs_dir", None) else DOCS_DIR
    comp_state = init_or_reset_state(
        mode="LIVE",
        state_dir=state_dir,
        docs_dir=docs_dir,
        preserve_bug_fixes=True,
        rebuild_docs=True,
    )
    ledger = LedgerManager(state_dir=state_dir / "agents")
    states = [ledger.load_agent_state(spec.agent_id) for spec in AGENT_SPECS]
    print(
        format_leaderboard_table(
            states,
            title=f"COMPETITION STATE RESET — Status: {comp_state['status']} (Day 0/{COMPETITION_TRADING_DAYS})",
        )
    )
    logger.info("All 10 agents reset to $%.2f cash in %s and dashboard rebuilt in %s.", INITIAL_CASH, state_dir, docs_dir)
    return 0


def _test_gemini_flash_key(api_key: str) -> tuple[bool, str]:
    """Send a tiny test prompt to Gemini Flash without ever logging the API key."""
    placeholders = {"", "your_google_ai_studio_gemini_api_key_here", "your_gemini_api_key_here", "changeme"}
    if not api_key or api_key.strip().lower() in placeholders:
        return False, "GEMINI_API_KEY is not set (or still has placeholder value in .env)"

    for model in ("gemini-2.5-flash", "gemini-2.0-flash"):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key.strip()}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": 'Reply with JSON: {"status": "ok"}'}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 30},
        }
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=12.0) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
                candidates = raw.get("candidates") or []
                if candidates:
                    return True, f"Connected to {model} successfully"
        except urllib.error.HTTPError as http_err:
            code = getattr(http_err, "code", 0)
            if code in (400, 401, 403):
                return False, f"Gemini API returned HTTP {code} (check GEMINI_API_KEY validity)"
            continue
        except Exception as exc:
            return False, f"Gemini network check failed ({type(exc).__name__})"

    return False, "Could not reach Gemini Flash endpoint"


def cmd_verify_setup(args: argparse.Namespace) -> int:
    """Verify `.env`, Alpaca Paper API connection & clock/calendar, Gemini Flash API key, and strategy hashes."""
    load_dotenv_if_present()

    print("=" * 80)
    print("  AI TRADING ARENA — SETUP & SAFETY VERIFICATION")
    print("=" * 80)

    # 1. Verify Paper-Only Endpoint Hard Rule
    try:
        paper_url = assert_paper_trading_only()
        print(f"  [PASS] Paper-Trading Endpoint Lock : {paper_url}")
    except Exception as exc:
        print(f"  [FAIL] Paper-Trading Endpoint Lock : {exc}")
        return 1

    # 2. Verify Alpaca Paper Credentials & Clock/Calendar
    client = AlpacaPaperDataClient()
    has_alpaca_creds = client.has_credentials()
    clock = client.get_clock()
    today_et = to_et_datetime(datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    cal = client.get_calendar(today_et, (date.fromisoformat(today_et) + timedelta(days=20)).isoformat())

    if has_alpaca_creds:
        try:
            live_clock = client._get_json(client.paper_base_url, "/v2/clock")
            if isinstance(live_clock, dict) and "is_open" in live_clock:
                print(
                    f"  [PASS] Alpaca Paper API Connection : Authenticated (market is_open={live_clock['is_open']}, "
                    f"next_open={live_clock.get('next_open')})"
                )
            else:
                print("  [WARN] Alpaca Paper API Connection : Unexpected response format")
        except Exception as exc:
            print(f"  [FAIL] Alpaca Paper API Connection : Failed to authenticate ({type(exc).__name__})")
    else:
        print(
            "  [WARN] Alpaca Paper API Keys       : Not configured in .env yet "
            f"(Local fallback clock is_open={clock.get('is_open')}, calendar days={len(cal)})"
        )

    print(
        f"  [PASS] US Market Calendar Check    : Found {len(cal)} upcoming trading days "
        f"(Thanksgiving & Black Friday early close supported)"
    )

    # 3. Verify Gemini Flash API Key
    gemini_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    gemini_ok, gemini_msg = _test_gemini_flash_key(gemini_key)
    if gemini_ok:
        print(f"  [PASS] Gemini Flash LLM API        : {gemini_msg}")
    else:
        print(f"  [WARN] Gemini Flash LLM API        : {gemini_msg}")

    # 4. Verify Strategy SHA-256 Hashes
    hashes = compute_all_strategy_hashes()
    missing_hashes = [k for k, v in hashes.items() if v == "MISSING"]
    if not missing_hashes:
        print(f"  [PASS] Strategy SHA-256 Integrity  : All {len(hashes)} core files hashed and locked")
        for fname, hval in sorted(hashes.items()):
            print(f"         - {fname:<18} : {hval[:16]}...")
    else:
        print(f"  [FAIL] Strategy SHA-256 Integrity  : Missing files {missing_hashes}")
        return 1

    print("=" * 80)
    return 0


# ---------------------------------------------------------------------------
# Argument Parser & Entrypoint
# ---------------------------------------------------------------------------

def cmd_learn_styles(args: argparse.Namespace) -> int:
    """Learn Berkshire (5y 13F) & Congress (2y House PTR) style patterns with Train/Test vs SPY validation."""
    from arena.research.berkshire_learner import BerkshireStyleLearner
    from arena.research.congress_learner import CongressStyleLearner
    from arena.research.market_history import MarketHistoryCache

    start_date = str(getattr(args, "start_date", "") or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    offline = bool(getattr(args, "offline", False))

    mc = MarketHistoryCache(offline=offline)
    cache_as_of = (date.fromisoformat(start_date) - timedelta(days=1)).isoformat()
    logger.info("Building universe fundamentals snapshot as of %s (strictly before %s)...", cache_as_of, start_date)
    mc.build_universe_snapshot_cache(cache_as_of)

    b_res = BerkshireStyleLearner(market_cache=mc, offline=offline).train_validate_and_save(agent_start_date=start_date)
    c_res = CongressStyleLearner(market_cache=mc, offline=offline).train_validate_and_save(agent_start_date=start_date)

    print("=" * 90)
    print("  LEARNED FILING-STYLE PATTERNS — OUT-OF-SAMPLE BACKTEST VS SPY")
    print("=" * 90)
    print(f"  11. Berkshire Style: {b_res['out_of_sample_test']['plain_verdict']}")
    print(f"  12. Congress Style : {c_res['out_of_sample_test']['plain_verdict']}")
    print("=" * 90)
    return 0


def cmd_generate_report(args: argparse.Namespace) -> int:
    """Generate (or regenerate) a verified daily GFM Markdown report from current state."""
    load_dotenv_if_present()
    state_dir = Path(args.state_dir) if getattr(args, "state_dir", None) else STATE_DIR
    docs_dir = Path(args.docs_dir) if getattr(args, "docs_dir", None) else DOCS_DIR
    reports_dir = _resolve_reports_dir(state_dir, Path(args.reports_dir) if getattr(args, "reports_dir", None) else None)
    use_llm = not bool(getattr(args, "no_llm", False))

    comp_state = load_competition_state(state_dir=state_dir)
    bug_fixes = load_bug_fixes_log(state_dir=state_dir)
    ledger = LedgerManager(state_dir=state_dir / "agents")
    agent_states = [ledger.load_or_initialize_agent(spec) for spec in AGENT_SPECS]

    active_dates: List[str] = list(comp_state.get("active_trading_dates") or [])
    req_date = (getattr(args, "date", None) or "").strip()
    if req_date:
        trading_date_et = req_date
    elif active_dates:
        trading_date_et = active_dates[-1]
    else:
        trading_date_et = to_et_datetime(datetime.now(timezone.utc)).strftime("%Y-%m-%d")

    req_day = getattr(args, "day", None)
    if req_day is not None:
        trading_day_number = int(req_day)
    elif trading_date_et in active_dates:
        trading_day_number = active_dates.index(trading_date_et) + 1
    else:
        trading_day_number = max(1, len(active_dates))

    res = generate_and_save_daily_report(
        agent_states=agent_states,
        competition_state=comp_state,
        trading_date_et=trading_date_et,
        trading_day_number=trading_day_number,
        state_dir=state_dir,
        reports_dir=reports_dir,
        use_llm=use_llm,
    )
    _rebuild_dashboard(
        agent_states=agent_states,
        competition_state=comp_state,
        bug_fixes=bug_fixes,
        output_dir=docs_dir,
        state_dir=state_dir,
        reports_dir=reports_dir,
    )
    print(f"Daily Report Generated ({res['generator']}, post_check={res['post_check_passed']}):")
    print(f"  - Daily file : {res['report_path']}")
    print(f"  - Latest file: {res['latest_path']}")
    print(f"  - Web page   : {docs_dir / 'reports.html'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m arena.cli",
        description="AI Trading Arena — 10-Day Virtual US Stock Trading Competition CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run-cycle
    p_cycle = subparsers.add_parser(
        "run-cycle",
        help="Run a single 30-minute live paper-trading cycle (checks Alpaca market clock first).",
    )
    p_cycle.add_argument(
        "--force-open-check",
        action="store_true",
        help="Bypass the market-open clock check to force a verification cycle when the market is closed.",
    )
    p_cycle.add_argument("--state-dir", type=str, default=None, help="Override state directory path.")
    p_cycle.add_argument("--docs-dir", type=str, default=None, help="Override docs output directory path.")
    p_cycle.set_defaults(func=cmd_run_cycle)

    # dry-run
    p_dry = subparsers.add_parser(
        "dry-run",
        help="Replay the last 5 trading days of historical 30-min data through all 10 agents.",
    )
    p_dry.add_argument("--days", type=int, default=5, help="Number of historical trading days to replay (default: 5).")
    p_dry.add_argument(
        "--reset-after",
        action="store_true",
        help="Reset `state/` and `docs/` back to $10,000 Day 0 after saving the dry-run dashboard to `docs_dry_run/`.",
    )
    p_dry.add_argument(
        "--use-live-llm",
        action="store_true",
        help="Call live Gemini API for every historical cycle instead of deterministic persona simulation.",
    )
    p_dry.add_argument(
        "--offline",
        action="store_true",
        help="Force deterministic offline historical dataset even if Alpaca keys are set.",
    )
    p_dry.set_defaults(func=cmd_dry_run)

    # reset
    p_reset = subparsers.add_parser(
        "reset",
        help="Reset all 10 agents in `state/` and `docs/` to $10,000.00 cash, 0 trades, Day 0/10.",
    )
    p_reset.add_argument("--state-dir", type=str, default=None, help="Override state directory path.")
    p_reset.add_argument("--docs-dir", type=str, default=None, help="Override docs output directory path.")
    p_reset.set_defaults(func=cmd_reset)

    # verify-setup
    p_verify = subparsers.add_parser(
        "verify-setup",
        help="Check .env keys, Alpaca Paper clock/calendar, Gemini Flash API, and strategy SHA-256 hashes.",
    )
    p_verify.set_defaults(func=cmd_verify_setup)

    # learn-styles
    p_learn = subparsers.add_parser(
        "learn-styles",
        help="Learn Berkshire (5y 13F) & Congress (2y House PTR) style patterns and run Train/Test split vs SPY.",
    )
    p_learn.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Competition start date YYYY-MM-DD; strictly excludes filings published on/after this date.",
    )
    p_learn.add_argument("--offline", action="store_true", help="Use only already-cached filings and prices.")
    p_learn.set_defaults(func=cmd_learn_styles)

    # generate-report
    p_rep = subparsers.add_parser(
        "generate-report",
        help="Generate a verified daily GitHub Flavored Markdown report from current state.",
    )
    p_rep.add_argument("--date", type=str, default=None, help="Target ET trading date YYYY-MM-DD (default: latest active day).")
    p_rep.add_argument("--day", type=int, default=None, help="Override trading day number (1..10).")
    p_rep.add_argument("--no-llm", action="store_true", help="Use 100% deterministic Python renderer without calling Gemini.")
    p_rep.add_argument("--state-dir", type=str, default=None, help="Override state directory path.")
    p_rep.add_argument("--reports-dir", type=str, default=None, help="Override reports output directory path.")
    p_rep.add_argument("--docs-dir", type=str, default=None, help="Override docs output directory path.")
    p_rep.set_defaults(func=cmd_generate_report)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
