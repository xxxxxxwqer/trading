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
from .claude_rmp_agent import ClaudeRMPAgent
from .kiro_pair_reversion_agent import KiroPairReversionAgent
from .calendar_agents import (
    EODLoserOvernightAgent,
    GapDownFillAgent,
    IntradayMomentumAgent,
    OvernightDriftAgent,
    TurnOfMonthAgent,
)
from .allocation_agents import (
    DualMomentumMacroAgent,
    LowVolAnomalyAgent,
    MegaCap5HoldAgent,
    MinVarianceETFAgent,
    RiskParity3Agent,
)
from .factor_agents import (
    CrossSectionalShortReversalAgent,
    High52AnchorAgent,
    RegimeBetaSwitchAgent,
    ValueMomentumAgent,
    VolumeAccumulationAgent,
)
from .timing_agents import (
    BreadthTimingAgent,
    PanicDipIndexAgent,
    RiskAppetiteRatioAgent,
)
from .microstructure_agents import (
    SqueezeBreakoutAgent,
    VWAPReversionAgent,
)

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
    "claude_rmp": ClaudeRMPAgent,
    "kiro_pair_reversion": KiroPairReversionAgent,
    "overnight_drift": OvernightDriftAgent,
    "eod_loser_overnight": EODLoserOvernightAgent,
    "intraday_momentum": IntradayMomentumAgent,
    "turn_of_month": TurnOfMonthAgent,
    "gap_down_fill": GapDownFillAgent,
    "dual_momentum_macro": DualMomentumMacroAgent,
    "risk_parity_3": RiskParity3Agent,
    "min_variance_etf": MinVarianceETFAgent,
    "low_vol_anomaly": LowVolAnomalyAgent,
    "megacap5_hold": MegaCap5HoldAgent,
    "xs_short_reversal": CrossSectionalShortReversalAgent,
    "high52_anchor": High52AnchorAgent,
    "value_momentum": ValueMomentumAgent,
    "volume_accumulation": VolumeAccumulationAgent,
    "regime_beta_switch": RegimeBetaSwitchAgent,
    "breadth_timing": BreadthTimingAgent,
    "panic_dip_index": PanicDipIndexAgent,
    "risk_appetite_ratio": RiskAppetiteRatioAgent,
    "vwap_reversion": VWAPReversionAgent,
    "squeeze_breakout": SqueezeBreakoutAgent,
}


def build_all_agents(allow_offline_llm_simulation: bool = False) -> List[BaseAgent]:
    """Instantiate and return all 13 competition agents in the canonical order of AGENT_SPECS."""
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
    "ClaudeRMPAgent",
    "KiroPairReversionAgent",
    "LLMAgent",
    "build_all_agents",
]
