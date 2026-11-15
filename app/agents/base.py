"""Base class for all agents: own persistent beliefs, message handling, reply helpers."""
import json

from sqlalchemy import Integer, String, cast, select, update
from sqlalchemy.exc import IntegrityError

from app.bus import BROADCAST, Message, Performative as P
from app.models import AgentState


class Agent:
    name = "Agent"
    counters: list = []   # integer beliefs created at start-up

    def __init__(self):
        self.bus = None

    # ---------- persistent beliefs (survive restarts) ----------
    def init_state(self, db):
        for key in self.counters:
            if db.get(AgentState, (self.name, key)) is None:
                db.add(AgentState(agent=self.name, key=key, value="0"))
        db.flush()

    def belief(self, db, key, default=0):
        raw = db.execute(select(AgentState.value).where(
            AgentState.agent == self.name, AgentState.key == key)).scalar()
        return default if raw is None else json.loads(raw)

    def bump(self, db, key, n=1):
        """Atomic counter increment (safe when two requests arrive together)."""
        stmt = (update(AgentState)
                .where(AgentState.agent == self.name, AgentState.key == key)
                .values(value=cast(cast(AgentState.value, Integer) + n, String))
                .execution_options(synchronize_session=False))
        if db.execute(stmt).rowcount == 0:
            try:
                db.add(AgentState(agent=self.name, key=key, value=str(n)))
                db.flush()
            except IntegrityError:
                db.rollback()
                raise

    # ---------- messaging ----------
    def receive(self, db, msg: Message):
        # Reactive behaviour: INFORM / broadcast events call on_<event>()
        if msg.performative == P.INFORM or msg.receiver == BROADCAST:
            handler = getattr(self, "on_" + str(msg.content.get("event", "")), None)
            if handler:
                handler(db, msg)
            return None
        if msg.performative != P.REQUEST:
            return self.reply(msg, P.REFUSE, reason=f"{self.name} only accepts REQUEST and INFORM messages.")
        handler = getattr(self, "do_" + str(msg.content.get("action", "")), None)
        if handler is None:
            return self.reply(msg, P.REFUSE, reason=f"{self.name} does not know the action "
                                                    f"'{msg.content.get('action')}'.")
        return handler(db, msg)

    def reply(self, msg: Message, performative, **content):
        return Message(sender=self.name, receiver=msg.sender, performative=performative,
                       content=content, conversation_id=msg.conversation_id)

    def ask(self, db, receiver, action, conversation_id, **content):
        return self.bus.request(db, self.name, receiver, action, content, conversation_id)

    def broadcast(self, db, conversation_id, event, **data):
        self.bus.send(db, Message(sender=self.name, receiver=BROADCAST, performative=P.INFORM,
                                  content={"event": event, **data}, conversation_id=conversation_id))
