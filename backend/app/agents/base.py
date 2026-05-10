from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..models import AgentEnvelope, CaseState, ReadinessReport
from ..opensearch_adapter import OpenSearchAdapter


@dataclass
class AgentContext:
    case: CaseState
    adapter: OpenSearchAdapter
    readiness: Optional[ReadinessReport] = None
    outputs: List[AgentEnvelope] = field(default_factory=list)


class BaseAgent:
    name = "base"

    def run(self, context: AgentContext) -> AgentEnvelope:
        raise NotImplementedError

