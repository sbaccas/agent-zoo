from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.models.entity import Entity, Position


class SimulationStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"


class Topology(str, Enum):
    BOUNDED = "bounded"
    TORUS = "torus"


class SpeciesConfig(BaseModel):
    vision_radius: int = Field(default=4, ge=1, description="Vision radius in grid tiles")
    action_budget: int = Field(default=1, ge=1, description="Action budget per turn")
    weight: int = Field(default=1, ge=1, description="Physics weight for bumps and displacement")
    attack_range: int = Field(default=0, ge=0, description="Range in tiles for directional slash AoE")
    move_cooldown: int = Field(default=1, ge=1, description="Ticks required to move 1 tile")
    attack_cooldown: int = Field(default=3, ge=0, description="Ticks required to recover from attack")
    lunge_cooldown: int = Field(default=2, ge=0, description="Ticks required to recover from lunge")


class SimulationConfig(BaseModel):
    width: int = Field(default=100, ge=5, le=1000)
    height: int = Field(default=100, ge=5, le=1000)
    topology: Topology = Topology.BOUNDED
    populations: Dict[str, int] = Field(default_factory=lambda: {"cats": 2, "mice": 10})
    species: Dict[str, SpeciesConfig] = Field(
        default_factory=lambda: {
            "cat": SpeciesConfig(
                vision_radius=10,
                action_budget=2,
                weight=10,
                # Depth of the strike zone in tiles. The zone is a rectangle
                # this deep and 3 tiles wide, so 2 gives the 6 tiles directly
                # ahead. Was 8, which reached 8 tiles forward and 4 off-axis.
                attack_range=2,
                move_cooldown=3,
                attack_cooldown=3,
                lunge_cooldown=2,
            ),
            "mouse": SpeciesConfig(
                vision_radius=4,
                action_budget=1,
                weight=1,
                attack_range=0,
                move_cooldown=2,
                attack_cooldown=0,
                lunge_cooldown=0,
            ),
        }
    )
    tick_rate: float = Field(default=5.0, gt=0, le=100.0)


class GameEvent(BaseModel):
    tick: int
    round: int
    event_type: str
    actor_id: str
    target_id: Optional[str] = None
    position: Optional[Position] = None
    message: str
    details: Optional[Dict[str, Any]] = None


class SimulationStateResponse(BaseModel):
    simulation_id: str
    status: SimulationStatus
    tick: int
    round: int
    width: int
    height: int
    topology: Topology
    tick_rate: float
    cat_count: int
    mouse_count: int
    captures: int
    entities: List[Entity]
    recent_events: List[GameEvent] = []
    can_step_back: bool = False
    species: Optional[Dict[str, Any]] = None


class UpdateTickRateRequest(BaseModel):
    tick_rate: float = Field(..., gt=0, le=100.0)


class ModifyPopulationRequest(BaseModel):
    species: str
    delta: int


class UpdateSpeciesConfigRequest(BaseModel):
    vision_radius: Optional[int] = Field(default=None, ge=1)
    action_budget: Optional[int] = Field(default=None, ge=1)
    weight: Optional[int] = Field(default=None, ge=1)
    attack_range: Optional[int] = Field(default=None, ge=0)
    move_cooldown: Optional[int] = Field(default=None, ge=1)
    attack_cooldown: Optional[int] = Field(default=None, ge=0)
    lunge_cooldown: Optional[int] = Field(default=None, ge=0)

