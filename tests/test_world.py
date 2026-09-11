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
