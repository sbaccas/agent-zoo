import pytest
from app.game.simulation import Simulation
from app.models.action import ActionType
from app.models.entity import Direction, EntityType, Position
from app.models.simulation import SimulationConfig


@pytest.mark.asyncio
async def test_cat_hp_initialization():
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10))

    assert cat.hp == 100
    assert cat.max_hp == 100
    assert mouse.hp == 100
    assert mouse.max_hp == 100


@pytest.mark.asyncio
async def test_cat_vs_cat_three_slashes_to_eliminate():
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10), facing=Direction.WEST)

    assert cat2.hp == 100
    assert cat2.alive is True

    # Slash 1: HP 100 -> 66 (~33% damage, alive)
    res1 = sim.action_handler.apply_action(cat1, ActionType.ATTACK, current_tick=1)
    assert res1.accepted is True
    assert cat2.id in res1.affected_entities
    assert cat2.hp == 66
    assert cat2.alive is True

    # Reset cooldown on cat1 to simulate subsequent attacks
    cat1.action_cooldown = 0

    # Slash 2: HP 66 -> 32 (alive)
    res2 = sim.action_handler.apply_action(cat1, ActionType.ATTACK, current_tick=5)
    assert res2.accepted is True
    assert cat2.id in res2.affected_entities
    assert cat2.hp == 32
    assert cat2.alive is True

    # Reset cooldown on cat1
    cat1.action_cooldown = 0

    # Slash 3: HP 32 -> 0 (Cat 2 goes down! 3 slashes and you're out!)
    res3 = sim.action_handler.apply_action(cat1, ActionType.ATTACK, current_tick=10)
    assert res3.accepted is True
    assert cat2.id in res3.affected_entities
    assert cat2.hp == 0
    assert cat2.alive is False


@pytest.mark.asyncio
async def test_observation_payload_includes_hp_and_attack_power():
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10), facing=Direction.WEST)

    obs = sim.scheduler._build_observation_payload(cat1)
    # self checks
    assert obs["self"]["hp"] == 100
    assert obs["self"]["max_hp"] == 100
    # capabilities checks
    assert obs["capabilities"]["hp"] == 100
    assert obs["capabilities"]["max_hp"] == 100
    assert obs["capabilities"]["attack_power"] == 34
    # visible_entities checks
    vis_cat = [v for v in obs["visible_entities"] if v["id"] == cat2.id][0]
    assert vis_cat["hp"] == 100
    assert vis_cat["max_hp"] == 100


@pytest.mark.asyncio
async def test_cat_cannot_damage_itself():
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.NORTH)

    res = sim.action_handler.apply_action(cat, ActionType.ATTACK, current_tick=1)
    assert res.accepted is True
    assert cat.id not in res.affected_entities
    assert cat.hp == 100
    assert cat.alive is True


@pytest.mark.asyncio
async def test_cat_slash_captures_mouse_instantly():
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=11, y=10), facing=Direction.WEST)

    res = sim.action_handler.apply_action(cat, ActionType.ATTACK, current_tick=1)
    assert res.accepted is True
    assert mouse.id in res.affected_entities
    assert mouse.hp == 0
    assert mouse.alive is False


@pytest.mark.asyncio
async def test_scheduler_takedown_credits_capture_on_zero_hp():
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10), facing=Direction.WEST)
    sim.entity_manager.set_agent_mode(cat1.id, "manual")
    sim.entity_manager.set_agent_mode(cat2.id, "manual")

    assert sim.scheduler.captures == 0

    # Put cat2 at 32 HP (after 2 hits)
    cat2.hp = 32
    # Third slash knocks cat2 out
    sim.action_handler.apply_action(cat1, ActionType.ATTACK, current_tick=1)
    assert cat2.alive is False

    # Simulate scheduler recording event
    sim.scheduler._register_capture(cat1.id, cat2, "slash impact")
    assert sim.scheduler.captures == 1
    assert sim.scheduler.scores[cat1.id] == 1


@pytest.mark.asyncio
async def test_hazard_zone_does_not_double_hit_same_tick():
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10), facing=Direction.EAST)
    cat2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=11, y=10), facing=Direction.WEST)

    # Cat 1 slashes on tick 1
    sim.scheduler.tick = 1
    sim.action_handler.apply_action(cat1, ActionType.ATTACK, current_tick=1)
    assert cat2.hp == 66  # took 34 damage on instant swing

    # End of tick 1: _resolve_hazard_zones runs
    sim.scheduler._resolve_hazard_zones()
    assert cat2.hp == 66  # NOT hit again on tick 1!

    # Tick 2: cat2 stays in the lethal zone
    sim.scheduler.tick = 2
    sim.scheduler._resolve_hazard_zones()
    assert cat2.hp == 32  # took 34 damage on tick 2


@pytest.mark.asyncio
async def test_dead_cat_observation_legal_actions_empty():
    sim = Simulation(config=SimulationConfig(populations={"cats": 0, "mice": 0}))
    cat = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=10, y=10))
    cat.hp = 0
    cat.alive = False

    obs = sim.scheduler._build_observation_payload(cat)
    assert obs["self"]["alive"] is False
    assert obs["self"]["hp"] == 0
    assert obs["legal_actions"] == []


@pytest.mark.asyncio
async def test_dead_cat_workbench_act_async_eliminated():
    from actors.cat.api import act_async, state
    from actors.models import Observation
    dead_obs = Observation(
        round=1,
        tick=42,
        self={"id": "cat-1", "position": {"x": 5, "y": 5}, "facing": "north", "alive": False, "hp": 0, "max_hp": 100},
        visible_entities=[],
        legal_actions=[]
    )
    resp = await act_async(dead_obs)
    assert resp.action == "stay"
    assert "ELIMINATED" in resp.thought
    assert state.active_directive["strategy"] == "ELIMINATED"


@pytest.mark.asyncio
async def test_dead_cat_radio_fallback_acknowledges_knockout():
    from actors.cat.actor import interrogate_adk_gemini
    from actors.models import Observation
    dead_obs = Observation(
        round=1,
        tick=42,
        self={"id": "cat-1", "position": {"x": 8, "y": 12}, "facing": "south", "alive": False, "hp": 0, "max_hp": 100},
        visible_entities=[],
        legal_actions=[]
    )
    reply, engine, err = await interrogate_adk_gemini(
        user_prompt="Status report?",
        obs=dead_obs,
        history=[],
    )
    assert "knocked out" in reply.lower() or "eliminated" in reply.lower()
    assert "0/100 hp" in reply.lower()

