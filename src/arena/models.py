"""Core data models for market snapshots, orders, fills, positions, and agent ledgers."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional


OrderAction = Literal["BUY", "SELL", "HOLD"]


@dataclass
class Bar:
    """OHLCV bar with UTC ISO-8601 timestamp."""
    ticker: str
    timestamp: str  # ISO-8601 UTC string, e.g. "2026-11-12T15:00:00Z"
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Bar":
        return cls(**data)


@dataclass
class Quote:
    """Bid/Ask quote strictly at or before decision time, or next available fill quote."""
    ticker: str
    timestamp: str  # ISO-8601 UTC string
    bid_price: float
    ask_price: float
    mid_price: float
    spread_bps: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Quote":
        return cls(**data)


@dataclass
class TickerIndicators:
    """Precomputed technical indicators strictly up to decision_timestamp (no look-ahead)."""
    ticker: str
    last_price: float
    prev_close: float
    change_pct_1d: float
    change_pct_5d: float
    sma_10: float
    sma_20: float
    sma_30: float
    rsi_14: float
    atr_14: float
    bb_upper_20: float
    bb_mid_20: float
    bb_lower_20: float
    high_20: float
    low_20: float
    opening_range_high: Optional[float] = None
    opening_range_low: Optional[float] = None
    volume_ratio: float = 1.0
    relative_strength_vs_spy: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TickerIndicators":
        return cls(**data)


@dataclass
class NewsHeadline:
    """Market news item available at or before decision_timestamp."""
    headline: str
    summary: str
    source: str
    created_at: str  # ISO-8601 UTC string
    symbols: List[str] = field(default_factory=list)
    url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NewsHeadline":
        return cls(**data)


@dataclass
class MarketSnapshot:
    """Complete point-in-time market state passed to every agent at decision time."""
    decision_timestamp: str              # ISO-8601 UTC cutoff; all data inside has timestamp <= decision_timestamp
    trading_date_et: str                 # YYYY-MM-DD in America/New_York
    trading_day_number: int              # 1..10
    is_near_market_close: bool           # True in the final 35 minutes before session close (handles early closes)
    session_open_et: str                 # e.g. "09:30"
    session_close_et: str                # e.g. "16:00" or "13:00" on Black Friday
    bars_by_ticker: Dict[str, List[Bar]] # Historical bars with timestamp <= decision_timestamp
    quotes_by_ticker: Dict[str, Quote]   # Latest quote at or before decision_timestamp
    indicators: Dict[str, TickerIndicators]
    news: List[NewsHeadline] = field(default_factory=list)
    regime_summary: str = ""
    daily_bars_by_ticker: Dict[str, List[Bar]] = field(default_factory=dict)


@dataclass
class OrderSignal:
    """Decision emitted by an agent (coded or LLM)."""
    agent_id: str
    action: OrderAction                  # "BUY", "SELL", or "HOLD"
    ticker: str                          # Ticker symbol (or "" for HOLD)
    stop_loss: Optional[float] = None    # Required for every BUY order
    take_profit: Optional[float] = None  # Optional take-profit target
    confidence: float = 0.5              # 0.0 to 1.0
    reasoning: str = ""                  # Plain-English explanation of why this decision was made
    requested_shares: Optional[float] = None  # Optional cap; Risk Manager sizes position by 1% risk / stop distance
    decision_timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderSignal":
        return cls(**data)


@dataclass
class RiskCheckResult:
    """Result from the shared Risk Manager after evaluating an OrderSignal."""
    approved: bool
    signal: OrderSignal
    approved_shares: float = 0.0
    estimated_notional: float = 0.0
    risk_dollars: float = 0.0
    rejection_reason: str = ""


@dataclass
class Position:
    """An open long position in an agent's virtual ledger (fractional shares supported)."""
    ticker: str
    shares: float
    avg_entry_price: float
    current_price: float
    stop_loss: float
    take_profit: Optional[float]
    opened_at: str
    entry_reasoning: str = ""
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    market_value: float = 0.0

    def update_mark(self, price: float) -> None:
        self.current_price = round(price, 4)
        self.market_value = round(self.shares * price, 2)
        cost_basis = self.shares * self.avg_entry_price
        self.unrealized_pnl = round(self.market_value - cost_basis, 2)
        self.unrealized_pnl_pct = round(
            ((price - self.avg_entry_price) / self.avg_entry_price) * 100.0, 2
        ) if self.avg_entry_price > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Position":
        return cls(**data)


@dataclass
class TradeRecord:
    """Completed buy or sell fill record in an agent's ledger."""
    trade_id: str
    agent_id: str
    side: Literal["BUY", "SELL"]
    ticker: str
    shares: float
    decision_timestamp: str
    fill_timestamp: str
    reference_price: float
    fill_price: float
    spread_cost_dollars: float
    slippage_cost_dollars: float
    commission_dollars: float
    notional_dollars: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    realized_pnl: Optional[float] = None
    realized_pnl_pct: Optional[float] = None
    exit_reason: str = ""  # e.g., "AGENT_SIGNAL", "STOP_LOSS", "TAKE_PROFIT", "EOD_FLAT", "COMPETITION_END", "BLOWN_UP"
    reasoning: str = ""
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeRecord":
        return cls(**data)


@dataclass
class RejectionRecord:
    """Audit log entry whenever the shared Risk Manager rejects an order."""
    timestamp: str
    agent_id: str
    action: str
    ticker: str
    reason: str
    requested_stop_loss: Optional[float] = None
    agent_reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RejectionRecord":
        return cls(**data)


@dataclass
class EquityPoint:
    """Single point on an agent's equity curve."""
    timestamp: str
    trading_date_et: str
    equity: float
    cash: float
    positions_value: float
    drawdown_pct: float
    daily_return_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EquityPoint":
        return cls(**data)


@dataclass
class DailyJournalEntry:
    """Plain-English daily journal summarizing what the agent did and why on a given US trading day."""
    trading_date_et: str
    trading_day_number: int
    start_equity: float
    end_equity: float
    daily_pnl: float
    daily_pnl_pct: float
    trades_count: int
    rejections_count: int
    summary_text: str
    highlights: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DailyJournalEntry":
        return cls(**data)


@dataclass
class AgentLedgerState:
    """Full serializable state of a single trading agent."""
    agent_id: str
    name: str
    category: str
    status: Literal["ACTIVE", "DAILY_HALTED", "BLOWN_UP", "COMPLETED"] = "ACTIVE"
    status_reason: str = "Trading active"
    initial_cash: float = 10_000.0
    cash: float = 10_000.0
    equity: float = 10_000.0
    peak_equity: float = 10_000.0
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    current_trading_date_et: str = ""
    day_start_equity: float = 10_000.0
    daily_pnl_pct: float = 0.0
    positions: Dict[str, Position] = field(default_factory=dict)
    pending_orders: List[OrderSignal] = field(default_factory=list)
    trades: List[TradeRecord] = field(default_factory=list)
    rejections: List[RejectionRecord] = field(default_factory=list)
    equity_curve: List[EquityPoint] = field(default_factory=list)
    daily_journal: List[DailyJournalEntry] = field(default_factory=list)
    last_updated: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "status_reason": self.status_reason,
            "initial_cash": self.initial_cash,
            "cash": round(self.cash, 2),
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "current_drawdown_pct": round(self.current_drawdown_pct, 2),
            "current_trading_date_et": self.current_trading_date_et,
            "day_start_equity": round(self.day_start_equity, 2),
            "daily_pnl_pct": round(self.daily_pnl_pct, 2),
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "pending_orders": [o.to_dict() for o in self.pending_orders],
            "trades": [t.to_dict() for t in self.trades],
            "rejections": [r.to_dict() for r in self.rejections],
            "equity_curve": [e.to_dict() for e in self.equity_curve],
            "daily_journal": [j.to_dict() for j in self.daily_journal],
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentLedgerState":
        return cls(
            agent_id=data["agent_id"],
            name=data["name"],
            category=data["category"],
            status=data.get("status", "ACTIVE"),
            status_reason=data.get("status_reason", "Trading active"),
            initial_cash=float(data.get("initial_cash", 10_000.0)),
            cash=float(data.get("cash", 10_000.0)),
            equity=float(data.get("equity", 10_000.0)),
            peak_equity=float(data.get("peak_equity", 10_000.0)),
            max_drawdown_pct=float(data.get("max_drawdown_pct", 0.0)),
            current_drawdown_pct=float(data.get("current_drawdown_pct", 0.0)),
            current_trading_date_et=data.get("current_trading_date_et", ""),
            day_start_equity=float(data.get("day_start_equity", 10_000.0)),
            daily_pnl_pct=float(data.get("daily_pnl_pct", 0.0)),
            positions={k: Position.from_dict(v) for k, v in data.get("positions", {}).items()},
            pending_orders=[OrderSignal.from_dict(o) for o in data.get("pending_orders", [])],
            trades=[TradeRecord.from_dict(t) for t in data.get("trades", [])],
            rejections=[RejectionRecord.from_dict(r) for r in data.get("rejections", [])],
            equity_curve=[EquityPoint.from_dict(e) for e in data.get("equity_curve", [])],
            daily_journal=[DailyJournalEntry.from_dict(j) for j in data.get("daily_journal", [])],
            last_updated=data.get("last_updated", ""),
        )
