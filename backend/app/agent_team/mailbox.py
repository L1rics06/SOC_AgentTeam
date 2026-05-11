"""Agent Mailbox：在同一个 Case 内保存 Agent 间的结构化消息。"""

from __future__ import annotations

from typing import Dict, List

from ..models import CaseState, TeamMessage
from ..utils import model_to_dict, short_id


class TeamMailbox:
    """围绕 CaseState.team_messages 提供发送和按可见性读取消息的能力。"""

    def __init__(self, case: CaseState):
        self.case = case

    def send(self, from_agent: str, to_agent: str, message_type: str, content: str) -> Dict[str, str]:
        """发送消息，并自动附带当前 Case 的证据引用。"""
        message = TeamMessage(
            message_id=short_id("MSG", f"{self.case.case_id}:{from_agent}:{to_agent}:{message_type}:{content}"),
            from_agent=from_agent,
            to_agent=to_agent,
            message_type=message_type or "note",
            content=content,
            evidence_refs=list(self.case.evidence_refs),
        )
        self.case.team_messages.append(message)
        return {"message_id": message.message_id, "status": "sent"}

    def read(self, agent: str) -> List[Dict[str, object]]:
        """读取发给该 Agent、广播给 all，或由该 Agent 发出的消息。"""
        visible = []
        for message in self.case.team_messages:
            if message.to_agent in {agent, "all"} or message.from_agent == agent:
                visible.append(model_to_dict(message))
        return visible
