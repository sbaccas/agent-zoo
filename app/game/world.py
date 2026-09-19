import math
from typing import List, Optional, Tuple
from app.models.action import ActionType
from app.models.entity import Direction, Position
from app.models.simulation import Topology


class World:
    def __init__(self, width: int = 100, height: int = 100, topology: Topology = Topology.BOUNDED):
        self.width = width
        self.height = height
        self.topology = topology

    def is_in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def wrap_position(self, x: int, y: int) -> Tuple[int, int]:
        if self.topology == Topology.TORUS:
            return (x % self.width, y % self.height)
        return (x, y)

    def get_forward_delta(self, facing: Direction) -> Tuple[int, int]:
        if facing == Direction.NORTH:
            return (0, -1)
        elif facing == Direction.SOUTH:
            return (0, 1)
        elif facing == Direction.EAST:
            return (1, 0)
        elif facing == Direction.WEST:
            return (-1, 0)
        return (0, 0)

    def direction_from_action(self, action: ActionType) -> Direction:
        if action == ActionType.NORTH:
            return Direction.NORTH
        elif action == ActionType.SOUTH:
            return Direction.SOUTH
        elif action == ActionType.EAST:
            return Direction.EAST
        elif action == ActionType.WEST:
            return Direction.WEST
        return Direction.NORTH

    def get_next_position(
        self, pos: Position, action: ActionType, facing: Optional[Direction] = None
    ) -> Position:
        if action in [ActionType.STAY, ActionType.ATTACK]:
            return Position(x=pos.x, y=pos.y)

        if action == ActionType.LUNGE:
            if not facing:
                return Position(x=pos.x, y=pos.y)
            dx, dy = self.get_forward_delta(facing)
            if self.topology == Topology.TORUS:
                wx, wy = self.wrap_position(pos.x + dx * 2, pos.y + dy * 2)
                return Position(x=wx, y=wy)
            else:
                target_x = pos.x + dx * 2
                target_y = pos.y + dy * 2
                if self.is_in_bounds(target_x, target_y):
                    return Position(x=target_x, y=target_y)
                # If target is out of bounds, check 1 step forward (bonk into the wall)
                step1_x = pos.x + dx
                step1_y = pos.y + dy
                if self.is_in_bounds(step1_x, step1_y):
                    return Position(x=step1_x, y=step1_y)
                # Already at the wall
                return Position(x=pos.x, y=pos.y)

        dx, dy = 0, 0
        if action == ActionType.NORTH:
            dy = -1
        elif action == ActionType.SOUTH:
            dy = 1
        elif action == ActionType.EAST:
            dx = 1
        elif action == ActionType.WEST:
            dx = -1

        target_x = pos.x + dx
        target_y = pos.y + dy

        if self.topology == Topology.TORUS:
            wx, wy = self.wrap_position(target_x, target_y)
            return Position(x=wx, y=wy)
        else:
            if not self.is_in_bounds(target_x, target_y):
                return Position(x=pos.x, y=pos.y)
            return Position(x=target_x, y=target_y)

    def get_legal_actions(
        self,
        pos: Position,
        allow_attack: bool = False,
        facing: Optional[Direction] = None,
    ) -> List[ActionType]:
        legal = [ActionType.STAY]
        moves = [
            (ActionType.NORTH, 0, -1),
            (ActionType.SOUTH, 0, 1),
            (ActionType.EAST, 1, 0),
            (ActionType.WEST, -1, 0),
        ]
        for action, dx, dy in moves:
            nx = pos.x + dx
            ny = pos.y + dy
            if self.topology == Topology.TORUS or self.is_in_bounds(nx, ny):
                legal.append(action)

        if allow_attack:
            legal.append(ActionType.ATTACK)
            if facing:
                fdx, fdy = self.get_forward_delta(facing)
                step1_x = pos.x + fdx
                step1_y = pos.y + fdy
                if self.topology == Topology.TORUS or self.is_in_bounds(step1_x, step1_y):
                    legal.append(ActionType.LUNGE)
            else:
                legal.append(ActionType.LUNGE)

        return legal

    # The field of view is a box drawn around the entity, not a beam projected
    # out of its face. Three numbers define it, all measured in the entity's own
    # frame (forward = the way it faces, offaxis = perpendicular), and all three
    # are tunable per species and at runtime -- see SpeciesConfig.vision_radius,
    # vision_rear and vision_half_width:
    #
    #   forward  : up to `distance` tiles ahead
    #   backward : `rear` tiles
    #   sideways : `half_width` tiles in either direction
    #
    # The shipped rear margin is deliberately small. The cat is a predator built
    # to face its prey, and a symmetric box would make facing irrelevant to
    # perception and collapse a whole dimension of the game. Two tiles is enough
    # that the cat cannot be silently flanked from contact range -- which is
    # what kept happening -- without making it omniscient. Nothing stops you
    # setting it higher, but that is the trade you are making.
    #
    # The width is deliberately uniform with distance, and that is the one
    # property here that is *not* a tunable. A tapered version was tried and
    # reverted: three bands of decreasing half-width (10/4/2) put far more
    # resolution in the near field, which is where the game is decided, but it
    # cost something worth more than coverage. See `get_fov_tiles`.
    #
    # These two constants are now only fallbacks, used when a caller supplies no
    # per-species values. The live game always supplies them. They are kept
    # rather than deleted because `get_fov_tiles` is called from several places
    # and defaulting to a silent 0 would be a far worse failure mode than a
    # slightly stale constant.
    FOV_HALF_WIDTH = 3
    FOV_REAR_DEPTH = 2

    def get_fov_tiles(
        self,
        pos: Position,
        facing: Direction,
        distance: int = 4,
        rear: Optional[int] = None,
        half_width: Optional[int] = None,
    ) -> List[Tuple[int, int]]:
        """Tiles the entity can see: a box around it, biased forward.

        All three dimensions are caller-supplied and independently tunable:
        `distance` ahead, `rear` behind, `half_width` either side. Facing east
        from C, with distance 4, rear 2, half_width 3::

            X X X X X X X
            X X X X X X X
            X X X X X X X
            X X C X X X X
            X X X X X X X
            X X X X X X X
            X X X X X X X

        Membership is two integer comparisons::

            -rear <= forward <= distance
            |offaxis| <= half_width

        That line is the whole design, and it outranks the shape. The actor is
        an LLM that has to decide membership in its head, for several mice, four
        times a second, before committing to an action it cannot take back. Any
        rule it cannot evaluate reliably shows up as a whiff. Note that tuning
        the numbers does not change the *form* of the rule -- it stays two
        comparisons against two constants no matter what they are set to.

        Three shapes have occupied this method. The history is worth keeping
        because the two that were rejected failed for different reasons.

        The original was a cone spreading as ``1 if dist <= 2 else dist // 2``.
        Two separate faults: nothing in the observation described that integer
        division, so the actor could not predict membership before acting; and
        the spread was only +/-1 at forward 1..3, so a mouse three ahead and two
        to the side -- one sidestep from the strike zone -- was invisible. A
        cone is narrow exactly where the game is decided.

        The second was a forward-only rectangle of constant width. It fixed the
        near-field blind spot and made membership statable, but it kept the
        cone's unexamined premise: that the entity sits *on the boundary* of its
        own perception. A mouse standing directly beside or behind the cat did
        not exist, so the actor would walk away from adjacent prey.

        A fourth was tried and reverted: a tapered box, 21 tiles wide at
        forward -2..2, narrowing to 9 and then 5. It was strictly better on
        coverage -- it put the resolution in the near field where the cone had
        none, at 161 tiles against the default box's 91. But membership became
        a band lookup, a conditional half-width that changes with the same
        ``forward`` value being compared. That is a strictly harder thing to
        hold in your head than a constant, and it was being asked of the one
        component already measured to be struggling with this arithmetic: sign
        errors on ``offaxis`` run 6.5x the rate of errors on ``forward``.
        Buying coverage with reasoning load is a bad trade when the failure
        being chased is a reasoning failure. Keep this rule flat -- tune the
        numbers, not the form.
        """
        if rear is None:
            rear = self.FOV_REAR_DEPTH
        if half_width is None:
            half_width = self.FOV_HALF_WIDTH
        # Clamp rather than raise. These arrive from a live PATCH that can land
        # mid-tick, and a perception call is the wrong place to take down the
        # simulation over a bad number.
        rear = max(0, rear)
        half_width = max(0, half_width)

        dx, dy = self.get_forward_delta(facing)
        px, py = -dy, dx  # unit vector perpendicular to facing

        tiles = []
        for dist in range(-rear, distance + 1):
            for lat in range(-half_width, half_width + 1):
                cx = pos.x + dx * dist + px * lat
                cy = pos.y + dy * dist + py * lat
                if self.topology == Topology.TORUS:
                    tiles.append(self.wrap_position(cx, cy))
                elif self.is_in_bounds(cx, cy):
                    tiles.append((cx, cy))

        return list(set(tiles))

    # Lateral half-width of the strike zone, in tiles, measured perpendicular to
    # facing. 1 means the zone is 3 tiles wide: one row either side of the
    # centre line, plus the centre line itself.
    SLASH_HALF_WIDTH = 1

    def get_slash_aoe_tiles(
        self, pos: Position, facing: Direction, range_tiles: int = 2
    ) -> List[Tuple[int, int]]:
        """Tiles hit by a slash: a rectangle `range_tiles` deep, 3 tiles wide.

        Facing east from C, with range_tiles=2, this is the 6 tiles::

            .  X  X
            C  X  X
            .  X  X

        This replaced a 60-70 degree cone whose lateral spread grew as
        ``round(dist * tan(35deg))`` and whose outer edge was clipped by
        ``hypot(dist, lat) <= range_tiles + 0.5``. At the previous default
        range of 8 that produced a ragged 50-tile blob reaching 4 tiles
        off-axis, with membership decided by floating-point rounding.

        Nothing could predict that boundary from a text description. The actor
        attacked visible-but-unreachable mice for whole games, and the
        evaluator spent three of its coaching notes trying to reverse-engineer
        the edge empirically, arriving at a different wrong rule per facing
        ("when facing west, only attack if the y-difference is at most 2").

        A rectangle is stated in one sentence and verified by integer
        comparison, so the actor can decide before swinging rather than
        inferring from the score afterwards.
        """
        dx, dy = self.get_forward_delta(facing)
        px, py = -dy, dx  # unit vector perpendicular to facing

        tiles = []
        for dist in range(1, range_tiles + 1):
            for lat in range(-self.SLASH_HALF_WIDTH, self.SLASH_HALF_WIDTH + 1):
                tx = pos.x + dx * dist + px * lat
                ty = pos.y + dy * dist + py * lat
                if self.topology == Topology.TORUS:
                    tiles.append(self.wrap_position(tx, ty))
                elif self.is_in_bounds(tx, ty):
                    tiles.append((tx, ty))

        return list(set(tiles))

    def _axis_delta(self, origin: int, target: int, span: int) -> int:
        """Signed separation along one axis, taking the short way round a torus.

        On a bounded board this is plain subtraction. On a torus the naive
        difference is wrong for any pair straddling the seam: with width 100, a
        target at x=1 seen from x=99 is 2 tiles east, not 98 tiles west. Every
        other method here already respects wrapping via `wrap_position`, so a
        geometry helper that did not would disagree with the hit test it is
        supposed to describe -- at the seam, and only at the seam, which is the
        kind of bug that survives a long time.
        """
        delta = target - origin
        if self.topology != Topology.TORUS or span <= 0:
            return delta
        delta %= span
        if delta > span // 2:
            delta -= span
        return delta

    def strike_geometry(
        self,
        pos: Position,
        facing: Direction,
        target: Position,
        attack_range: int,
    ) -> dict:
        """Describes a target in the observer's own frame, and says if it is hittable.

        Returns `forward`, `offaxis` and `in_strike_zone`. `forward` counts
        tiles along the way the observer faces (negative = behind); `offaxis`
        counts tiles perpendicular to it, positive toward the observer's right
        under the engine's `(px, py) = (-dy, dx)` convention.

        `in_strike_zone` is the same predicate `get_slash_aoe_tiles` enumerates,
        written as the two comparisons rather than as a tile set::

            1 <= forward <= attack_range   and   |offaxis| <= SLASH_HALF_WIDTH

        Both are inclusive, and both are derived here from the same constant and
        the same perpendicular convention the tile builder uses, so the two can
        only ever agree. A test asserts that equivalence by brute force over
        every facing and every offset, because two implementations of one rule
        is exactly the situation where they silently drift apart.

        This exists because of a measured failure, not for tidiness. The actor
        is an LLM that receives absolute coordinates and has to perform this
        frame rotation in its head, for up to five mice, four times a second,
        inside a thinking budget we have observed saturating. It does the
        arithmetic well -- a forensic pass found 123 of 135 self-reported
        forward distances exactly correct -- and still loses kills at both ends
        of the rule: swinging at targets it had itself computed as out of range,
        and declining legal shots at |offaxis| == 1 because it had rounded the
        lateral test up to "must be dead ahead". The scripted opponent converts
        100% of its chances for no cleverer reason than that it evaluates this
        boolean in code. Handing the actor the same boolean is what makes that
        comparison fair; see `SpeciesConfig.aim_assist`, which gates whether it
        is actually published into the observation.
        """
        dx, dy = self.get_forward_delta(facing)
        px, py = -dy, dx  # unit vector perpendicular to facing, same as above

        rel_x = self._axis_delta(pos.x, target.x, self.width)
        rel_y = self._axis_delta(pos.y, target.y, self.height)

        forward = rel_x * dx + rel_y * dy
        offaxis = rel_x * px + rel_y * py

        return {
            "forward": forward,
            "offaxis": offaxis,
            "in_strike_zone": (
                1 <= forward <= attack_range
                and abs(offaxis) <= self.SLASH_HALF_WIDTH
            ),
        }

