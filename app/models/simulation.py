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
    # The three dimensions of the viewport, all measured in the entity's own
    # frame. Together they define the box `World.get_fov_tiles` builds:
    #
    #     -vision_rear <= forward <= vision_radius
    #     |offaxis|    <= vision_half_width
    #
    # These only govern predators. Prey use a radial check against
    # `vision_radius` instead (360 degree panoramic vision, eyes on the sides),
    # so `vision_rear` and `vision_half_width` are ignored for mice. They are
    # still declared here rather than on a cat-only model because the species
    # table is keyed generically and the settings UI PATCHes both species
    # through the same endpoint.
    vision_radius: int = Field(default=4, ge=1, description="Forward reach of vision, in tiles")
    vision_rear: int = Field(
        default=2,
        ge=0,
        le=100,
        description="How far behind the entity the viewport extends, in tiles (predators only)",
    )
    # Half-width rather than full width because the box is symmetric about the
    # centre line, so the full width is always 2n+1 and therefore always odd. A
    # full-width field would accept 6 and then silently render as 5 or 7. This
    # way every accepted value is exactly representable; the UI displays the
    # derived full width so nobody has to do the arithmetic.
    vision_half_width: int = Field(
        default=3,
        ge=0,
        le=100,
        description="Lateral reach either side of the centre line, in tiles (predators only)",
    )
    action_budget: int = Field(default=1, ge=1, description="Action budget per turn")
    weight: int = Field(default=1, ge=1, description="Physics weight for bumps and displacement")
    attack_range: int = Field(default=0, ge=0, description="Range in tiles for directional slash AoE")
    move_cooldown: int = Field(default=1, ge=1, description="Ticks required to move 1 tile")
    attack_cooldown: int = Field(default=3, ge=0, description="Ticks required to recover from attack")
    lunge_cooldown: int = Field(default=2, ge=0, description="Ticks required to recover from lunge")
    attack_aoe_ticks: int = Field(
        default=2,
        ge=1,
        description="Ticks the slash zone stays lethal, counting the tick it lands",
    )


class HazardZone(BaseModel):
    """A lingering lethal region left behind by a slash.

    The slash used to be hitscan: `apply_action` killed whatever occupied the
    strike rectangle at the instant of the swing and the tiles existed only as
    a render hint in the event payload. That demanded the attacker predict an
    exact position at an exact instant, through a one-tick actuation delay it
    was never told about, so the correct decision was frequently unknowable.

    Persisting the zone turns that exact-instant prediction into an
    interception window: prey that walks into the blade during its lifetime
    dies too, so being early is no longer indistinguishable from being wrong.
    """

    id: str
    owner_id: str
    kind: str = "slash"
    tiles: List[Position]
    facing: str
    created_tick: int
    # Inclusive. A zone created on tick T with attack_aoe_ticks=2 is lethal on
    # T and T+1, and is pruned at the top of T+2.
    lethal_until_tick: int

    def is_lethal_on(self, tick: int) -> bool:
        return self.created_tick <= tick <= self.lethal_until_tick

    def ticks_remaining(self, tick: int) -> int:
        return max(0, self.lethal_until_tick - tick + 1)

    def covers(self, x: int, y: int) -> bool:
        return any(t.x == x and t.y == y for t in self.tiles)


class SimulationConfig(BaseModel):
    width: int = Field(default=100, ge=5, le=1000)
    height: int = Field(default=100, ge=5, le=1000)
    topology: Topology = Topology.BOUNDED
    populations: Dict[str, int] = Field(default_factory=lambda: {"cats": 2, "mice": 150})
    species: Dict[str, SpeciesConfig] = Field(
        default_factory=lambda: {
            "cat": SpeciesConfig(
                vision_radius=10,
                # The viewport: 10 ahead, 2 behind, 3 either side -- a 13x7
                # box, 91 tiles. Stated here rather than left to the field
                # defaults because the cat is the only entity that uses the
                # box at all, so these are the numbers that actually ship.
                vision_rear=2,
                vision_half_width=3,
                action_budget=2,
                weight=10,
                # Depth of the strike zone in tiles. The zone is a rectangle
                # this deep and 3 tiles wide, so 4 gives the 12 tiles directly
                # ahead. Raised from 2 because the cat is slower than the mouse
                # (move_cooldown 3 vs 2) and cannot win a straight chase; reach
                # is what compensates for the speed deficit.
                attack_range=4,
                move_cooldown=3,
                attack_cooldown=3,
                lunge_cooldown=2,
                # Lethal on the tick it lands and the tick after. Paired with
                # attack_cooldown=3 the cat is locked for exactly as long as
                # its own blade is dangerous, then recovers one tick after it
                # fades.
                attack_aoe_ticks=2,
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
    # Ticks per second, not seconds per tick: the loop sleeps 1/tick_rate.
    # 0.25 is one tick every 4 seconds, which is the pace the LLM cat needs to
    # have any chance of answering inside its own tick budget.
    tick_rate: float = Field(default=0.25, gt=0, le=100.0)


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
    # Live lethal zones. The client used to infer the strike rectangle itself
    # and animate it on a local timer, which could not stay in step with a
    # zone that outlives the swing. The server is authoritative now.
    hazard_zones: List[HazardZone] = []
    can_step_back: bool = False
    species: Optional[Dict[str, Any]] = None


class UpdateTickRateRequest(BaseModel):
    tick_rate: float = Field(..., gt=0, le=100.0)


class ModifyPopulationRequest(BaseModel):
    species: str
    delta: int


class UpdateSpeciesConfigRequest(BaseModel):
    vision_radius: Optional[int] = Field(default=None, ge=1)
    # Bounds mirror SpeciesConfig exactly. They are repeated rather than shared
    # because this model's job is to reject a bad PATCH at the edge, before
    # `update_species_config` setattr's it onto the live config mid-tick.
    vision_rear: Optional[int] = Field(default=None, ge=0, le=100)
    vision_half_width: Optional[int] = Field(default=None, ge=0, le=100)
    action_budget: Optional[int] = Field(default=None, ge=1)
    weight: Optional[int] = Field(default=None, ge=1)
    attack_range: Optional[int] = Field(default=None, ge=0)
    move_cooldown: Optional[int] = Field(default=None, ge=1)
    attack_cooldown: Optional[int] = Field(default=None, ge=0)
    lunge_cooldown: Optional[int] = Field(default=None, ge=0)
    attack_aoe_ticks: Optional[int] = Field(default=None, ge=1)

