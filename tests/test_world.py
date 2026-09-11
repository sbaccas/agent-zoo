import pytest
from app.game.world import World
from app.models.action import ActionType
from app.models.entity import Direction, Position
from app.models.simulation import Topology


def test_bounded_world_boundaries():
    world = World(width=10, height=10, topology=Topology.BOUNDED)
    assert world.is_in_bounds(0, 0)
    assert world.is_in_bounds(9, 9)
    assert not world.is_in_bounds(-1, 0)
    assert not world.is_in_bounds(0, -1)
    assert not world.is_in_bounds(10, 5)
    assert not world.is_in_bounds(5, 10)


def test_bounded_world_legal_actions():
    world = World(width=10, height=10, topology=Topology.BOUNDED)
    # (0, 0) top-left: north and west are illegal
    legal = world.get_legal_actions(Position(x=0, y=0))
    assert ActionType.STAY in legal
    assert ActionType.EAST in legal
    assert ActionType.SOUTH in legal
    assert ActionType.NORTH not in legal
    assert ActionType.WEST not in legal


def test_torus_topology_wrapping():
    world = World(width=100, height=100, topology=Topology.TORUS)
    # (99, 50) + EAST -> (0, 50)
    next_pos = world.get_next_position(Position(x=99, y=50), ActionType.EAST)
    assert next_pos.x == 0
    assert next_pos.y == 50

    # (0, 50) + WEST -> (99, 50)
    next_pos = world.get_next_position(Position(x=0, y=50), ActionType.WEST)
    assert next_pos.x == 99
    assert next_pos.y == 50


def test_directional_fov():
    world = World(width=50, height=50, topology=Topology.BOUNDED)
    pos = Position(x=10, y=10)
    # Looking East: forward tiles should have x > 10 and not x < 10
    fov = world.get_forward_fov_tiles(pos, Direction.EAST, distance=4)
    assert len(fov) > 0
    for tx, ty in fov:
        assert tx > 10, f"FoV tile ({tx}, {ty}) should be in front (x > 10)"
        assert tx <= 14, f"FoV tile ({tx}, {ty}) should be within 4 tiles"


def test_slash_aoe():
    world = World(width=50, height=50, topology=Topology.BOUNDED)
    pos = Position(x=10, y=10)
    # Looking North: forward tiles should have y < 10, strictly in front
    aoe = world.get_slash_aoe_tiles(pos, Direction.NORTH, range_tiles=2)
    assert len(aoe) > 0
    for tx, ty in aoe:
        assert ty < 10, f"Slash AoE tile ({tx}, {ty}) should be in front (y < 10)"
        assert ty >= 8, f"Slash AoE tile ({tx}, {ty}) should be within reach of 2 tiles"


def test_cat_lunge_next_position():
    world = World(width=50, height=50, topology=Topology.BOUNDED)
    pos = Position(x=10, y=10)

    # Facing EAST -> 2 tiles forward to (12, 10)
    p_east = world.get_next_position(pos, ActionType.LUNGE, facing=Direction.EAST)
    assert p_east.x == 12
    assert p_east.y == 10

    # Facing NORTH -> 2 tiles forward to (10, 8)
    p_north = world.get_next_position(pos, ActionType.LUNGE, facing=Direction.NORTH)
    assert p_north.x == 10
    assert p_north.y == 8


def test_cat_lunge_wall_bonk_clamped():
    # 10x10 bounded world (indices 0..9)
    world = World(width=10, height=10, topology=Topology.BOUNDED)

    # 1 tile away from eastern wall: at (8, 5) facing EAST
    # 2 tiles would be x=10 (out of bounds), so it bonks and stops at x=9
    pos_near_wall = Position(x=8, y=5)
    p_bonk = world.get_next_position(pos_near_wall, ActionType.LUNGE, facing=Direction.EAST)
    assert p_bonk.x == 9
    assert p_bonk.y == 5

    # At wall: at (9, 5) facing EAST
    # Step 1 is already out of bounds -> lunge is not legal, get_next_position stays at 9
    pos_at_wall = Position(x=9, y=5)
    legal_at_wall = world.get_legal_actions(pos_at_wall, allow_attack=True, facing=Direction.EAST)
    assert ActionType.LUNGE not in legal_at_wall
    p_blocked = world.get_next_position(pos_at_wall, ActionType.LUNGE, facing=Direction.EAST)
    assert p_blocked.x == 9
    assert p_blocked.y == 5
