"""Base agent interface and strategy integrity verification utilities."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Optional

from ..config import AGENT_SPECS_BY_ID, AgentSpec
from ..models import AgentLedgerState, MarketSnapshot, OrderSignal


class BaseAgent(ABC):
    """Abstract base class for all coded, LLM, and control trading agents."""

    DEFAULT_AGENT_ID: str = ""

    def __init__(self, spec: Optional[AgentSpec] = None) -> None:
        if spec is None:
            if not self.DEFAULT_AGENT_ID or self.DEFAULT_AGENT_ID not in AGENT_SPECS_BY_ID:
                raise ValueError(
                    f"{self.__class__.__name__} requires an AgentSpec or valid DEFAULT_AGENT_ID."
                )
            spec = AGENT_SPECS_BY_ID[self.DEFAULT_AGENT_ID]
        self.spec: AgentSpec = spec
        self.agent_id: str = spec.agent_id
        self.name: str = spec.name
        self.category: str = spec.category

    @abstractmethod
    def decide(self, snapshot: MarketSnapshot, state: AgentLedgerState) -> OrderSignal:
        """Evaluate point-in-time market snapshot and current ledger state to emit an OrderSignal."""
        raise NotImplementedError

    def hold_signal(
        self,
        snapshot: MarketSnapshot,
        reasoning: str,
        confidence: float = 0.5,
    ) -> OrderSignal:
        """Helper to construct a standardized HOLD OrderSignal."""
        return OrderSignal(
            agent_id=self.agent_id,
            action="HOLD",
            ticker="",
            stop_loss=None,
            take_profit=None,
            confidence=round(float(confidence), 2),
            reasoning=reasoning,
            requested_shares=None,
            decision_timestamp=snapshot.decision_timestamp,
        )


def compute_strategy_hashes() -> Dict[str, str]:
    """Compute SHA-256 hashes of core strategy and execution rule files.

    Used by the competition runner to verify and prove that no strategy rules,
    prompts, risk limits, or execution mechanics were altered after the
    competition started.
    """
    agents_dir = Path(__file__).resolve().parent
    arena_dir = agents_dir.parent

    target_files = {
        "coded_agents.py": agents_dir / "coded_agents.py",
        "llm_agents.py": agents_dir / "llm_agents.py",
        "risk_manager.py": arena_dir / "risk_manager.py",
        "simulator.py": arena_dir / "simulator.py",
    }

    hashes: Dict[str, str] = {}
    for name, path in target_files.items():
        if path.exists() and path.is_file():
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            hashes[name] = "MISSING"
    return hashes
