"""Advanced feature: each agent has an HTTP inbox that accepts a JSON message."""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import config
from app.agents import bus
from app.bus import Message
from app.db import get_db

router = APIRouter(prefix="/api/agents", tags=["agent inbox"])


@router.get("")
def list_agents():
    return {"agents": list(bus.agents.keys())}


@router.post("/{agent_name}/inbox")
def inbox(agent_name: str, message: Message, x_agent_key: str = Header(""), db: Session = Depends(get_db)):
    """Send a JSON message to an agent. Needs the X-Agent-Key header (AGENT_API_KEY)."""
    if not config.AGENT_API_KEY or not secrets.compare_digest(x_agent_key.encode(), config.AGENT_API_KEY.encode()):
        raise HTTPException(401, "Missing or wrong X-Agent-Key.")
    if agent_name not in bus.agents:
        raise HTTPException(404, "Unknown agent.")
    message.receiver = agent_name
    reply = bus.send(db, message)
    db.commit()
    return reply if reply is not None else {"status": "delivered"}
