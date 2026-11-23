"""Trading Arena agents package: coded strategies, Gemini Flash LLM traders, and controls."""

from __future__ import annotations

import inspect
from typing import Dict, List, Type

from ..config import AGENT_SPECS
from .base import BaseAgent, compute_strategy_hashes
from .coded_agents import (
    BuyAndHoldSPYAgent,
    IntradayORBAgent,
    MeanReversionAgent,
    RandomTraderAgent,
    SectorRotationAgent,
    TrendFollowingAgent,
)
from .style_agents import BerkshireStyleAgent, CongressStyleAgent

try:
    from .llm_agents import LLMAgent
except ImportError:  # pragma: no cover - handled lazily in build_all_agents if needed
    LLMAgent = None  # type: ignore[assignment,misc]


CODED_AND_CONTROL_AGENT_CLASSES: Dict[str, Type[BaseAgent]] = {
    "coded_trend": TrendFollowingAgent,
    "coded_mean_rev": MeanReversionAgent,
    "coded_orb": IntradayORBAgent,
    "coded_sector": SectorRotationAgent,
    "ctrl_spy": BuyAndHoldSPYAgent,
    "ctrl_random": RandomTraderAgent,
    "style_berkshire": BerkshireStyleAgent,
    "style_congress": CongressStyleAgent,
}


def build_all_agents(allow_offline_llm_simulation: bool = False) -> List[BaseAgent]:
    """Instantiate and return all 10 competition agents in the canonical order of AGENT_SPECS."""
    from .llm_agents import LLMAgent as _LLMAgent

    llm_sig = inspect.signature(_LLMAgent.__init__)
    llm_params = llm_sig.parameters

    agents: List[BaseAgent] = []
    for spec in AGENT_SPECS:
        if spec.agent_id in CODED_AND_CONTROL_AGENT_CLASSES:
            cls = CODED_AND_CONTROL_AGENT_CLASSES[spec.agent_id]
            agents.append(cls(spec=spec))
        else:
            if "allow_offline_llm_simulation" in llm_params:
                agents.append(
                    _LLMAgent(spec=spec, allow_offline_llm_simulation=allow_offline_llm_simulation)
                )
            elif "allow_offline_simulation" in llm_params:
                agents.append(
                    _LLMAgent(spec=spec, allow_offline_simulation=allow_offline_llm_simulation)
                )
            elif "offline_simulation" in llm_params:
                agents.append(
                    _LLMAgent(spec=spec, offline_simulation=allow_offline_llm_simulation)
                )
            elif any(p.kind == inspect.Parameter.VAR_KEYWORD for p in llm_params.values()):
                agents.append(
                    _LLMAgent(spec=spec, allow_offline_llm_simulation=allow_offline_llm_simulation)
                )
            elif len(llm_params) >= 3:
                agents.append(_LLMAgent(spec, allow_offline_llm_simulation))
            else:
                agents.append(_LLMAgent(spec))
    return agents


__all__ = [
    "BaseAgent",
    "compute_strategy_hashes",
    "TrendFollowingAgent",
    "MeanReversionAgent",
    "IntradayORBAgent",
    "SectorRotationAgent",
    "BuyAndHoldSPYAgent",
    "RandomTraderAgent",
    "BerkshireStyleAgent",
    "CongressStyleAgent",
    "LLMAgent",
    "build_all_agents",
]
