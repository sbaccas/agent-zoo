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
    # Looking East: the box reaches 4 tiles ahead and only FOV_REAR_DEPTH behind.
    fov = world.get_fov_tiles(pos, Direction.EAST, distance=4)
    assert len(fov) > 0
    for tx, ty in fov:
        assert tx >= 10 - World.FOV_REAR_DEPTH, f"FoV tile ({tx}, {ty}) is too far behind"
        assert tx <= 14, f"FoV tile ({tx}, {ty}) should be within 4 tiles ahead"
    # Facing is still meaningful: the box is biased forward, not symmetric.
    assert (14, 10) in fov
    assert (6, 10) not in fov


def test_fov_is_a_box_around_the_entity_in_every_facing():
    """The viewport surrounds the entity and is biased forward.

    Pinned per-facing because the shape is built from a perpendicular vector
    ``(px, py) = (-dy, dx)`` whose sign flips with facing, and from a forward
    delta that decides which side the rear margin lands on. A bug in either
    would produce a box that is correct facing east and mirrored or rotated
    facing west -- exactly the class of error the old cone made hard to spot.
    """
    world = World(width=100, height=100, topology=Topology.BOUNDED)
    pos = Position(x=50, y=50)
    deltas = {
        Direction.NORTH: (0, -1),
        Direction.SOUTH: (0, 1),
        Direction.EAST: (1, 0),
        Direction.WEST: (-1, 0),
    }
    expected = {
        (f, l)
        for f in range(-World.FOV_REAR_DEPTH, 11)
        for l in range(-World.FOV_HALF_WIDTH, World.FOV_HALF_WIDTH + 1)
    }

    for facing, (dx, dy) in deltas.items():
        px, py = -dy, dx
        tiles = world.get_fov_tiles(pos, facing, distance=10)
        # Re-express every tile in the cat's own frame: forward and offaxis.
        in_frame = set()
        for tx, ty in tiles:
            ox, oy = tx - pos.x, ty - pos.y
            in_frame.add((ox * dx + oy * dy, ox * px + oy * py))
        assert in_frame == expected, f"viewport facing {facing} is not the box"
        assert len(tiles) == 13 * 7  # forward 10 + rear 2 + the entity's own row


def test_fov_width_does_not_vary_with_distance():
    """The viewport is a box, not a cone and not a taper.

    Both rejected shapes varied width with forward distance, and both were
    rejected for the same underlying reason rather than for their coverage: a
    width that depends on ``forward`` makes membership a conditional the actor
    has to evaluate in its head, mid-decision, for every mouse. The cone
    spread as ``dist // 2``; the taper stepped down through bands. Constant
    width is what makes the rule a single comparison against a fixed number.

    If a future change reintroduces a distance-dependent width, this fails.
    """
    world = World(width=100, height=100, topology=Topology.BOUNDED)
    pos = Position(x=50, y=50)
    tiles = set(world.get_fov_tiles(pos, Direction.EAST, distance=10))

    widths = []
    for forward in range(-World.FOV_REAR_DEPTH, 11):
        row = [off for off in range(-20, 21) if (50 + forward, 50 + off) in tiles]
        widths.append(len(row))
    assert len(set(widths)) == 1, f"width varies with distance: {widths}"
    assert widths[0] == 2 * World.FOV_HALF_WIDTH + 1


def test_fov_includes_the_tiles_touching_the_entity():
    """Regression: nothing adjacent to the cat may be invisible.

    Two shapes in a row got this wrong. The cone saw only ``|offaxis| <= 1`` at
    forward 1..3, so a mouse three ahead and two to the side -- one sidestep
    from the strike zone -- did not appear. The forward-only rectangle that
    replaced it fixed that but still started at ``forward == 1``, so a mouse
    standing directly beside or behind the cat did not appear either, and the
    actor would walk away from adjacent prey.
    """
    world = World(width=100, height=100, topology=Topology.BOUNDED)
    pos = Position(x=50, y=50)
    tiles = set(world.get_fov_tiles(pos, Direction.EAST, distance=10))

    # All eight neighbours, including the three directly behind.
    for ox in (-1, 0, 1):
        for oy in (-1, 0, 1):
            assert (50 + ox, 50 + oy) in tiles, f"neighbour ({ox}, {oy}) is invisible"

    # The near-field tiles the cone could not see.
    for forward in (1, 2, 3):
        for offaxis in (-3, -2, 2, 3):
            assert (50 + forward, 50 + offaxis) in tiles

    # The box still has edges: 3 off-axis and FOV_REAR_DEPTH behind.
    assert (51, 54) not in tiles
    assert (51, 46) not in tiles
    assert (50 - World.FOV_REAR_DEPTH, 50) in tiles
    assert (50 - World.FOV_REAR_DEPTH - 1, 50) not in tiles


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
