import pytest
from app.game.world import World
from app.models.action import ActionType
from app.models.entity import Position
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
