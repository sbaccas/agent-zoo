from typing import List
from app.game.entities import EntityManager
from app.game.world import World
from app.models.action import ActionRequest, ActionResult, ActionType
from app.models.entity import Entity, EntityType, Position
from app.models.simulation import HazardZone


class ActionHandler:
    def __init__(self, world: World, entity_manager: EntityManager, stats=None):
        self.world = world
        self.entity_manager = entity_manager
        # Live lethal zones, newest last. Owned here rather than on the
        # Scheduler because attacks also arrive via the manual and HTTP
        # endpoints, which call apply_action directly; hanging the registry
        # off the shared handler keeps those paths from silently skipping
        # zone creation.
        self.hazard_zones: List[HazardZone] = []
        self._zone_seq = 0
        # Optional box-score recorder. Attached here for the same reason the
        # zone registry is: this is the one function every control path calls,
        # so counting here cannot miss the manual or HTTP cats.
        self.stats = stats

    def apply_action(self, entity: Entity, action: ActionType, current_tick: int) -> ActionResult:
        """Resolve an action and, for cats, record it against the box score."""
        # A locked entity is bounced by `_resolve_action` without spending a
        # turn. The scheduler already files those as `ticks_locked`, so
        # recording here too would double-count them and inflate every
        # denominator that divides by ticks.
        if self.stats is None or entity.type != EntityType.CAT or entity.action_cooldown > 0:
            return self._resolve_action(entity, action, current_tick)

        prev_x, prev_y = entity.position.x, entity.position.y
        # Sampled before resolution: an attack rewrites facing and a move
        # rewrites position, either of which would change the answer to "was
        # there a shot available when the cat chose?"
        prey_visible, had_zone_chance = self._scan_prey(entity)

        result = self._resolve_action(entity, action, current_tick)

        self.stats.record_action(
            entity,
            action,
            result.accepted,
            prev_x,
            prev_y,
            result.new_position.x,
            result.new_position.y,
            len(result.affected_entities or []),
            (result.details or {}).get("zone_id"),
            had_zone_chance,
            prey_visible,
        )
        return result

    def _scan_prey(self, entity: Entity) -> tuple:
        """Return (prey_visible, prey_in_strike_zone) for a cat, pre-action.

        Visibility deliberately reuses `get_forward_fov_tiles`, the same cone
        the observation payload is built from, so "prey in sight" means what
        the agent was actually shown rather than a looser radial guess.
        """
        spec_cfg = self.entity_manager.species_config.get(entity.type.value)
        vision = spec_cfg.vision_radius if spec_cfg else 4
        atk_range = spec_cfg.attack_range if spec_cfg and spec_cfg.attack_range > 0 else 2

        fov = set(self.world.get_forward_fov_tiles(entity.position, entity.facing, distance=vision))
        strike = set(self.world.get_slash_aoe_tiles(entity.position, entity.facing, range_tiles=atk_range))

        prey_visible = False
        in_strike_zone = False
        for other in self.entity_manager.get_alive_entities():
            if other.type != EntityType.MOUSE:
                continue
            tile = (other.position.x, other.position.y)
            if tile in fov:
                prey_visible = True
            if tile in strike:
                in_strike_zone = True
            if prey_visible and in_strike_zone:
                break
        return prey_visible, in_strike_zone

    def _resolve_action(self, entity: Entity, action: ActionType, current_tick: int) -> ActionResult:
        prev_pos = Position(x=entity.position.x, y=entity.position.y)

        # 1. Action lock check: Entity cannot change input while completing previous action animation
        if entity.action_cooldown > 0:
            return ActionResult(
                accepted=False,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                action=action,
                message=f"Entity {entity.id} is locked in action '{entity.locked_action}' ({entity.action_cooldown} tick(s) remaining)",
                cooldown=entity.action_cooldown,
            )

        species_key = entity.type.value
        spec_cfg = self.entity_manager.species_config.get(species_key)

        if action == ActionType.ATTACK:
            # Dynamic slash attack range based on species hyperparameter (default 2 tiles)
            range_tiles = spec_cfg.attack_range if spec_cfg and spec_cfg.attack_range > 0 else 2
            aoe_tiles = self.world.get_slash_aoe_tiles(entity.position, entity.facing, range_tiles=range_tiles)
            hit_entities: List[str] = []

            # Anything standing in the rectangle when the blade lands dies
            # immediately. The zone registered below then keeps killing for the
            # rest of its lifetime, so this pass is not redundant: it catches
            # prey that would otherwise flee during its own turn later in this
            # same tick.
            target_type = EntityType.MOUSE if entity.type == EntityType.CAT else EntityType.CAT
            for tx, ty in aoe_tiles:
                occupants = self.entity_manager.get_entities_at(tx, ty)
                for victim in occupants:
                    if victim.type == target_type and victim.alive:
                        victim.alive = False
                        hit_entities.append(victim.id)

            cooldown_dur = spec_cfg.attack_cooldown if spec_cfg else 3
            entity.action_cooldown = max(0, cooldown_dur - 1)
            entity.locked_action = action.value if entity.action_cooldown > 0 else None

            aoe_ticks = spec_cfg.attack_aoe_ticks if spec_cfg else 2
            self._zone_seq += 1
            zone = HazardZone(
                id=f"zone-{entity.id}-{self._zone_seq}",
                owner_id=entity.id,
                kind="slash",
                tiles=[Position(x=tx, y=ty) for tx, ty in aoe_tiles],
                facing=entity.facing.value,
                created_tick=current_tick,
                lethal_until_tick=current_tick + max(1, aoe_ticks) - 1,
            )
            self.hazard_zones.append(zone)

            return ActionResult(
                accepted=True,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                action=action,
                affected_entities=hit_entities,
                message=f"Attack executed hitting {len(hit_entities)} target(s)",
                cooldown=entity.action_cooldown,
                details={
                    "facing": entity.facing.value,
                    "range_tiles": range_tiles,
                    "aoe_tiles": [{"x": tx, "y": ty} for tx, ty in aoe_tiles],
                    "hit_entities": hit_entities,
                    "zone_id": zone.id,
                    "aoe_ticks": max(1, aoe_ticks),
                    "lethal_until_tick": zone.lethal_until_tick,
                },
            )

        if action == ActionType.LUNGE:
            allow_attack = (entity.type == EntityType.CAT)
            legal_actions = self.world.get_legal_actions(prev_pos, allow_attack=allow_attack, facing=entity.facing)
            if action not in legal_actions:
                return ActionResult(
                    accepted=False,
                    tick=current_tick,
                    previous_position=prev_pos,
                    new_position=prev_pos,
                    action=action,
                    message=f"Illegal action: {action.value} from ({prev_pos.x}, {prev_pos.y}) facing {entity.facing.value}",
                    cooldown=entity.action_cooldown,
                )

            new_pos = self.world.get_next_position(prev_pos, action, facing=entity.facing)
            entity.position = new_pos

            cooldown_dur = spec_cfg.lunge_cooldown if spec_cfg else 2
            entity.action_cooldown = max(0, cooldown_dur - 1)
            entity.locked_action = action.value if entity.action_cooldown > 0 else None

            return ActionResult(
                accepted=True,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=new_pos,
                action=action,
                message="Lunge executed successfully",
                cooldown=entity.action_cooldown,
            )

        # Movement / Stay actions
        allow_attack = (entity.type == EntityType.CAT)
        legal_actions = self.world.get_legal_actions(prev_pos, allow_attack=allow_attack, facing=entity.facing)
        if action not in legal_actions:
            return ActionResult(
                accepted=False,
                tick=current_tick,
                previous_position=prev_pos,
                new_position=prev_pos,
                action=action,
                message=f"Illegal action: {action.value} from ({prev_pos.x}, {prev_pos.y})",
                cooldown=entity.action_cooldown,
            )

        if action in [ActionType.NORTH, ActionType.SOUTH, ActionType.EAST, ActionType.WEST]:
            entity.facing = self.world.direction_from_action(action)
            new_pos = self.world.get_next_position(prev_pos, action)
            entity.position = new_pos
            cooldown_dur = spec_cfg.move_cooldown if spec_cfg else 1
        else:
            new_pos = prev_pos
            cooldown_dur = 1

        entity.action_cooldown = max(0, cooldown_dur - 1)
        entity.locked_action = action.value if entity.action_cooldown > 0 else None

        return ActionResult(
            accepted=True,
            tick=current_tick,
            previous_position=prev_pos,
            new_position=new_pos,
            action=action,
            message="Action applied successfully",
            cooldown=entity.action_cooldown,
        )
