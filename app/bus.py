"""Message bus. Agents talk ONLY by sending Message objects through this bus.
Every message is persisted (with its conversation id) so one user action can be traced."""
import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app import clock
from app.models import MessageLog

BROADCAST = "*"


class Performative(str, Enum):
    REQUEST = "REQUEST"
    AGREE = "AGREE"
    REFUSE = "REFUSE"
    PROPOSE = "PROPOSE"
    INFORM = "INFORM"
    FAILURE = "FAILURE"


def new_conversation_id() -> str:
    return uuid.uuid4().hex[:12]


class Message(BaseModel):
    sender: str
    receiver: str
    performative: Performative
    content: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str = Field(default_factory=new_conversation_id)
    timestamp: datetime = Field(default_factory=lambda: clock.now())


def _redact(content: dict) -> dict:
    """Ticket codes are secrets: never store them in full in the readable log."""
    return {k: (str(v)[:6] + "…" if k == "code" else v) for k, v in content.items()}


class MessageBus:
    def __init__(self):
        self.agents: dict = {}

    def register(self, agent):
        self.agents[agent.name] = agent
        agent.bus = self

    def init_state(self, db):
        for agent in self.agents.values():
            agent.init_state(db)

    def log(self, db, msg: Message):
        db.add(MessageLog(
            conversation_id=msg.conversation_id, timestamp=msg.timestamp, sender=msg.sender,
            receiver=msg.receiver, performative=msg.performative.value,
            content=json.dumps(_redact(msg.content), default=str)))
        db.flush()

    def send(self, db, msg: Message):
        """Deliver a message. Returns the receiver's reply (None for broadcasts / INFORMs)."""
        self.log(db, msg)
        if msg.receiver == BROADCAST:
            for agent in list(self.agents.values()):
                if agent.name != msg.sender:
                    agent.receive(db, msg)
            return None
        agent = self.agents.get(msg.receiver)
        if agent is None:
            return None
        reply = agent.receive(db, msg)
        if reply is not None:
            self.log(db, reply)
        return reply

    def request(self, db, sender, receiver, action, content=None, conversation_id=None):
        msg = Message(sender=sender, receiver=receiver, performative=Performative.REQUEST,
                      content={"action": action, **(content or {})},
                      conversation_id=conversation_id or new_conversation_id())
        return self.send(db, msg)
