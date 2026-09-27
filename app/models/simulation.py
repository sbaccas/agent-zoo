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
    hp: int = Field(
        default=100,
        ge=1,
        description="Max hit points / health (ratio out of 100)",
    )
    attack_power: int = Field(
        default=34,
        ge=0,
        description="Attack damage per slash out of 100 (~33/100; 3 slashes to defeat 100 HP)",
    )
    # Publishes each visible entity's position in the observer's own frame --
    # `forward`, `offaxis`, `in_strike_zone` -- alongside the absolute
    # coordinates that are always sent. It grants no new information: every one
    # of those numbers is derivable from data the observer already has. It only
    # removes the requirement to derive them.
    #
    # That distinction is the whole point. The scripted cat evaluates
    # `1 <= forward <= attack_range and |offaxis| <= 1` in code and converts
    # 100% of its chances with zero whiffs. The model cat is handed raw
    # coordinates and must rotate frames in its head for every mouse, every
    # tick, against a clock; measured over one game it created *more* chances
    # than the scripted cat (39 vs 32) and still finished behind on kills,
    # losing 13 shots to whiffs and forfeiting 11 more it had judged illegal
    # when they were not. So the comparison between the two was never really
    # about tactics.
    #
    # Deliberately defaults to True on predators, and deliberately lives on the
    # species config rather than in the agent. Comparability across runs is
    # preserved by the archive manifest (which records the live species config,
    # including aim_assist, in every run's manifest), not by defaulting off.
    # It is also per-species, so the mice cannot be given a faculty they have
    # no action to use.
    aim_assist: bool = Field(
        default=True,
        description=(
            "Publish observer-relative strike geometry (forward, offaxis, "
            "in_strike_zone) for each visible entity instead of requiring the "
            "agent to derive it from absolute coordinates"
        ),
    )
    absorption: bool = Field(
        default=True,
        description="When enabled on predator, eliminating a rival cat absorbs their accumulated score",
    )
    heal_on_mouse_capture: int = Field(
        default=15,
        ge=0,
        description="HP restored to predator when capturing a mouse",
    )
    heal_on_cat_elimination: int = Field(
        default=90,
        ge=0,
        description="HP restored to predator when eliminating a rival cat",
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


class DisclosureConfig(BaseModel):
    coach_sees_population: bool = Field(
        default=True,
        description="mice_alive, mice_initial, and captures included in the box score payload",
    )
    coach_sees_coverage_map: bool = Field(
        default=True,
        description="the get_coverage_map tool and heatmap endpoints return data",
    )
    actor_sees_own_trail: bool = Field(
        default=False,
        description="reserved for second arm: actor observation includes its own trail",
    )


class SimulationConfig(BaseModel):
    width: int = Field(default=100, ge=5, le=1000)
    height: int = Field(default=100, ge=5, le=1000)
    topology: Topology = Topology.BOUNDED
    populations: Dict[str, int] = Field(default_factory=lambda: {"cats": 2, "mice": 150})
    species: Dict[str, SpeciesConfig] = Field(
        default_factory=lambda: {
            "cat": SpeciesConfig(
                vision_radius=10,
                vision_rear=2,
                vision_half_width=5,
                action_budget=2,
                weight=10,
                attack_range=4,
                move_cooldown=3,
                attack_cooldown=3,
                lunge_cooldown=2,
                attack_aoe_ticks=2,
                hp=100,
                attack_power=34,
            ),
            "mouse": SpeciesConfig(
                vision_radius=4,
                action_budget=1,
                weight=1,
                attack_range=0,
                move_cooldown=2,
                attack_cooldown=0,
                lunge_cooldown=0,
                aim_assist=False,
                hp=100,
                attack_power=0,
            ),
        }
    )
    # Ticks per second, not seconds per tick: the loop sleeps 1/tick_rate.
    # 0.25 is one tick every 4 seconds, which is the pace the LLM cat needs to
    # have any chance of answering inside its own tick budget.
    tick_rate: float = Field(default=0.25, gt=0, le=100.0)
    disclosure: DisclosureConfig = Field(default_factory=DisclosureConfig)
    enable_map_features: bool = Field(
        default=False,
        description="Generate trees, buildings, and cheese on the board",
    )


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
    disclosure: Optional[DisclosureConfig] = None
    map_features: Optional[Dict[str, Any]] = None
    pending_repositions: Optional[Dict[str, Dict[str, int]]] = None


class RepositionEntityRequest(BaseModel):
    x: int = Field(..., ge=0, description="Target X coordinate")
    y: int = Field(..., ge=0, description="Target Y coordinate")


class UpdateTickRateRequest(BaseModel):
    tick_rate: float = Field(..., gt=0, le=100.0)


class SeekSimulationRequest(BaseModel):
    tick: int = Field(..., ge=0)


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
    hp: Optional[int] = Field(default=None, ge=1)
    attack_power: Optional[int] = Field(default=None, ge=0)
    # Unlike its neighbours this is a bool, so it has no bounds to mirror. It
    # still has to be listed explicitly: `update_species_config` iterates
    # `model_dump(exclude_unset=True)`, so a field absent from this model is a
    # field the PATCH endpoint silently drops.
    aim_assist: Optional[bool] = Field(default=None)
    absorption: Optional[bool] = Field(default=None)
    heal_on_mouse_capture: Optional[int] = Field(default=None, ge=0)
    heal_on_cat_elimination: Optional[int] = Field(default=None, ge=0)


class UpdateDisclosureConfigRequest(BaseModel):
    coach_sees_population: Optional[bool] = Field(default=None)
    coach_sees_coverage_map: Optional[bool] = Field(default=None)
    actor_sees_own_trail: Optional[bool] = Field(default=None)

