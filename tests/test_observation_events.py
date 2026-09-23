import pytest
from app.game.entities import EntityManager
from app.game.world import World
from app.game.actions import ActionHandler
from app.game.scheduler import Scheduler
from app.game.simulation import Simulation
from app.models.entity import Entity, EntityType, Position, Direction
from app.models.action import ActionType
from actors.models import Observation
from actors.cat.actor import build_decision_prompt, format_event_heraldry


def test_format_event_heraldry_ostentatious_and_unbiased():
    """Verify heraldry is ostentatious and contains no banned imperative strategy."""
    events = [
        {"tick": 10, "type": "elimination", "target": "cat-2", "target_type": "cat", "source": "slash impact"},
        {"tick": 10, "type": "capture", "target": "mouse-1", "target_type": "mouse", "source": "slash impact"},
        {"tick": 10, "type": "damage_dealt", "target": "cat-3", "target_type": "cat", "amount": 34, "target_hp": 32, "target_max_hp": 100, "source": "slash impact"},
        {"tick": 10, "type": "damage_taken", "source_id": "cat-2", "amount": 34, "remaining_hp": 66, "max_hp": 100, "source": "slash impact"},
        {"tick": 10, "type": "bump", "target": "mouse-2", "source": "collision"},
        {"tick": 10, "type": "eliminated", "by": "cat-2", "source": "slash impact"},
    ]
    heraldry = format_event_heraldry(events, current_tick=10)
    assert "GLORIOUS CONQUEST" in heraldry
    assert "cat-2" in heraldry
    assert "FEAST OF CHAMPIONS" in heraldry
    assert "mouse-1" in heraldry
    assert "VICIOUS BLOW" in heraldry
    assert "34 damage" in heraldry
    assert "BLOOD DRAWN" in heraldry
    assert "66/100 HP remaining" in heraldry
    assert "IMPACT COLLISION" in heraldry
    assert "FALLEN IN BATTLE" in heraldry

    # Verify no second-person commands or strategic spoon-feeding
    banned = ("you must", "you should", "prioritize", "prioritise", "make sure", "remember to")
    for word in banned:
        assert word not in heraldry.lower()


def test_build_decision_prompt_remains_minimal_without_events():
    """Verify the decision prompt remains minimal and data-only without injected events."""
    obs = Observation(
        tick=42,
        round=1,
        self={"id": "cat-1", "position": {"x": 10, "y": 10}, "facing": "north", "hp": 100, "max_hp": 100},
    )
    prompt = build_decision_prompt(obs, agent_id="cat-1")
    assert "GLORIOUS CONQUEST" not in prompt
    assert "Chronicle of Feats & Carnage:" not in prompt
    assert "Current Position: (x=10, y=10)" in prompt


def test_observation_payload_is_minimal_and_events_in_scheduler():
    """Verify observation payload remains minimal (no events/deaths) while scheduler tracks them for Game Stats."""
    world = World(width=20, height=20)
    em = EntityManager(world, {})
    ah = ActionHandler(world, em)
    sched = Scheduler(world, em, ah)

    # Spawn 2 cats and 1 mouse
    cat1 = Entity(id="cat-1", type=EntityType.CAT, position=Position(x=5, y=5), facing=Direction.EAST, hp=100, max_hp=100)
    cat2 = Entity(id="cat-2", type=EntityType.CAT, position=Position(x=6, y=5), facing=Direction.WEST, hp=100, max_hp=100)
    mouse1 = Entity(id="mouse-1", type=EntityType.MOUSE, position=Position(x=5, y=6), facing=Direction.NORTH, hp=10, max_hp=10)
    em.entities["cat-1"] = cat1
    em.entities["cat-2"] = cat2
    em.entities["mouse-1"] = mouse1

    sched.tick = 1
    # 1. Damage cat-2 by cat-1
    cat2.hp = 66
    sched._register_damage("cat-1", cat2, "slash impact", damage=34)

    obs1 = sched._build_observation_payload(cat1)
    obs2 = sched._build_observation_payload(cat2)

    # Observation payload MUST be minimal (no events or deaths)
    assert "events" not in obs1
    assert "deaths" not in obs1
    assert "recent_deaths" not in obs1
    assert "events" not in obs2

    # Scheduler entity_events has them for live feed & Game Stats
    cat1_evs = sched.entity_events.get("cat-1", [])
    cat2_evs = sched.entity_events.get("cat-2", [])
    assert len(cat1_evs) == 1
    assert cat1_evs[0]["type"] == "damage_dealt"
    assert cat1_evs[0]["target"] == "cat-2"
    assert cat1_evs[0]["amount"] == 34

    assert len(cat2_evs) == 1
    assert cat2_evs[0]["type"] == "damage_taken"
    assert cat2_evs[0]["source_id"] == "cat-1"

    # 2. Eliminate cat-2 by cat-1
    sched.tick = 2
    cat2.hp = 0
    cat2.alive = False
    sched._register_capture("cat-1", cat2, "slash impact")

    assert len(sched.entity_events["cat-1"]) == 2
    assert sched.entity_events["cat-1"][-1]["type"] == "elimination"
    assert sched.entity_events["cat-2"][-1]["type"] == "eliminated"

    # 3. Capture mouse-1 (unified event: elimination)
    sched.tick = 3
    mouse1.hp = 0
    mouse1.alive = False
    sched._register_capture("cat-1", mouse1, "slash impact")
    assert sched.entity_events["cat-1"][-1]["type"] == "elimination"
    assert sched.entity_events["cat-1"][-1]["target"] == "mouse-1"


@pytest.mark.asyncio
async def test_simulation_step_back_and_reset_preserves_events():
    """Verify simulation snapshot restore and reset properly manage entity_events."""
    sim = Simulation()
    cat1 = Entity(id="cat-1", type=EntityType.CAT, position=Position(x=5, y=5), facing=Direction.EAST, hp=100, max_hp=100)
    cat2 = Entity(id="cat-2", type=EntityType.CAT, position=Position(x=6, y=5), facing=Direction.WEST, hp=100, max_hp=100)
    sim.entity_manager.entities = {"cat-1": cat1, "cat-2": cat2}

    # Step forward manually
    sim._save_snapshot()
    sim.scheduler.tick = 1
    sim.scheduler._register_damage("cat-1", cat2, "slash impact", damage=34)

    assert len(sim.scheduler.entity_events["cat-1"]) == 1

    # Step back
    await sim.step_back()
    assert sim.scheduler.tick == 0
    assert len(sim.scheduler.entity_events.get("cat-1", [])) == 0

    # Test reset clears events
    sim.scheduler.entity_events["cat-1"] = [{"type": "damage_dealt"}]
    await sim.reset()
    assert len(sim.scheduler.entity_events.get("cat-1", [])) == 0
