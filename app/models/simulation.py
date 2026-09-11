from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from app.models.entity import Entity


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
    vision_radius: int = Field(default=4, ge=1)
    action_budget: int = Field(default=1, ge=1)
    weight: int = Field(default=1, ge=1)


class SimulationConfig(BaseModel):
    width: int = Field(default=100, ge=5, le=1000)
    height: int = Field(default=100, ge=5, le=1000)
    topology: Topology = Topology.BOUNDED
    populations: Dict[str, int] = Field(default_factory=lambda: {"cats": 10, "mice": 100})
    species: Dict[str, SpeciesConfig] = Field(
        default_factory=lambda: {
            "cat": SpeciesConfig(vision_radius=8, action_budget=2, weight=10),
            "mouse": SpeciesConfig(vision_radius=4, action_budget=1, weight=1),
        }
    )
    tick_rate: float = Field(default=10.0, gt=0, le=100.0)


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


class UpdateTickRateRequest(BaseModel):
    tick_rate: float = Field(..., gt=0, le=100.0)


class ModifyPopulationRequest(BaseModel):
    species: str
    delta: int
