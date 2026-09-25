"""Data models for the Agent-to-Agent (A2A) protocol."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class AgentSkill(BaseModel):
    """Declared skill in the A2A Agent Card."""
    id: str = Field(..., description="Unique skill identifier")
    name: str = Field(..., description="Human-readable skill name")
    description: str = Field(..., description="Explanation of what this skill does")
    tags: List[str] = Field(default_factory=list, description="Categorical tags")


class AgentCard(BaseModel):
    """Authoritative Agent Card published at /.well-known/agent.json and /a2a/agent-card."""
    id: str = Field(..., description="Unique agent identifier (e.g. 'cat-1', 'cat-2')")
    name: str = Field(..., description="Display name of the agent")
    description: str = Field(..., description="High-level description of agent role and purpose")
    version: str = Field(default="1.0.0", description="Agent implementation version")
    protocol_version: str = Field(default="a2a/v1", description="Supported A2A protocol specification")
    card_url: Optional[str] = Field(default=None, description="Absolute URL to this agent card")
    endpoints: Dict[str, str] = Field(
        default_factory=dict,
        description="A2A protocol endpoints (agent_card, messages, inbox, ws)",
    )
    capabilities: Dict[str, Any] = Field(
        default_factory=lambda: {
            "direct_messaging": True,
            "broadcast": True,
            "agent_discovery": True,
            "tools": True,
            "negotiation": True,
        },
        description="Declared agent capabilities",
    )
    skills: List[AgentSkill] = Field(
        default_factory=list,
        description="List of declared skills the agent possesses",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary agent metadata (model, team, arena designation)",
    )


class A2AMessage(BaseModel):
    """Standardized Agent-to-Agent (A2A) message envelope."""
    protocol: str = Field(default="a2a/v1", description="Protocol version identifier")
    message_id: str = Field(
        default_factory=lambda: f"a2a_{uuid.uuid4().hex[:12]}",
        description="Globally unique message identifier",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp",
    )
    tick: Optional[int] = Field(default=None, description="Game simulation tick when dispatched")
    round: Optional[int] = Field(default=None, description="Game simulation round")
    sender_id: str = Field(..., description="ID of the transmitting agent (e.g. 'cat-1')")
    sender_card_url: Optional[str] = Field(default=None, description="URL to sender's Agent Card")
    recipient_id: str = Field(
        ...,
        description="ID of target agent ('cat-2') for direct messages, or '*' / 'broadcast' for arena broadcast",
    )
    message_type: str = Field(
        default="DIRECT_MESSAGE",
        description="Message classification: DIRECT_MESSAGE, BROADCAST, PROPOSAL, PACT, or ALERT",
    )
    content: str = Field(..., description="Primary textual message body")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured metadata (strategy, target coordinates, tool invocation traces)",
    )


class A2AResponse(BaseModel):
    """Acknowledgement response returned upon receiving an A2A message."""
    status: str = Field(default="delivered", description="Delivery status: delivered, queued, error")
    message_id: str = Field(..., description="ID of acknowledged message")
    sender_id: str = Field(..., description="ID of agent that sent the message")
    recipient_id: str = Field(..., description="ID of agent that received the message")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of receipt",
    )
    error: Optional[str] = Field(default=None, description="Error detail if delivery failed")
