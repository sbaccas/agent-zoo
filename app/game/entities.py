import random
from typing import Dict, List, Optional, Tuple
from app.models.entity import Direction, Entity, EntityType, Position
from app.models.simulation import SpeciesConfig
from app.game.world import World


class EntityManager:
    def __init__(self, world: World, species_config: Dict[str, SpeciesConfig]):
        self.world = world
        self.species_config = species_config
        self.entities: Dict[str, Entity] = {}
        self._next_id: Dict[str, int] = {"cat": 1, "mouse": 1}
        self.agent_modes: Dict[str, str] = {}
        self.agent_urls: Dict[str, str] = {}

    def set_agent_mode(self, entity_id: str, mode: str, agent_url: Optional[str] = None):
        self.agent_modes[entity_id] = mode
        if agent_url:
            self.agent_urls[entity_id] = agent_url
        elif mode != "http":
            self.agent_urls.pop(entity_id, None)

    def get_agent_mode(self, entity_id: str) -> str:
        explicit_mode = self.agent_modes.get(entity_id)
        if explicit_mode == "manual":
            return "manual"
        from app.game.ws_manager import ws_agent_manager
        if ws_agent_manager.has_agent(entity_id):
            return "ws"
        return explicit_mode or "auto"

    def get_agent_url(self, entity_id: str) -> Optional[str]:
        return self.agent_urls.get(entity_id)

    def spawn_entity(
        self,
        entity_type: EntityType,
        pos: Optional[Position] = None,
        facing: Optional[Direction] = None,
    ) -> Entity:
        type_key = entity_type.value
        seq = self._next_id.get(type_key, 1)
        self._next_id[type_key] = seq + 1
        entity_id = f"{type_key}-{seq}"

        if pos is None:
            chosen_pos = None
            if entity_type == EntityType.MOUSE and self.world.cheese_locations and random.random() < 0.6:
                # 60% of mice spawn within or near buildings containing cheese
                cx, cy = random.choice(self.world.cheese_locations)
                for _ in range(50):
                    nx = cx + random.randint(-4, 4)
                    ny = cy + random.randint(-4, 4)
                    if self.world.is_traversable(nx, ny):
                        chosen_pos = Position(x=nx, y=ny)
                        break

            if chosen_pos is None:
                for _ in range(100):
                    rx = random.randint(0, self.world.width - 1)
                    ry = random.randint(0, self.world.height - 1)
                    if self.world.is_traversable(rx, ry):
                        chosen_pos = Position(x=rx, y=ry)
                        break

            pos = chosen_pos or Position(x=0, y=0)

        if facing is None:
            facing = random.choice([Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST])

        weight = 1
        hp = 100
        if type_key in self.species_config:
            spec = self.species_config[type_key]
            weight = spec.weight
            hp = getattr(spec, "hp", 100)

        if entity_id in ("cat-3", "evil-cat"):
            hp = 150

        entity = Entity(
            id=entity_id,
            type=entity_type,
            position=pos,
            facing=facing,
            alive=True,
            weight=weight,
            hp=hp,
            max_hp=hp,
        )
        self.entities[entity.id] = entity
        return entity

    def spawn_population(self, entity_type: EntityType, count: int) -> List[Entity]:
        created = []
        for _ in range(count):
            created.append(self.spawn_entity(entity_type))
        return created

    def remove_entity(self, entity_id: str) -> Optional[Entity]:
        return self.entities.pop(entity_id, None)

    def remove_population(self, entity_type: EntityType, count: int) -> int:
        removed = 0
        matching_ids = [
            e.id for e in self.entities.values() if e.type == entity_type and e.alive
        ]
        for e_id in matching_ids[:count]:
            if self.remove_entity(e_id):
                removed += 1
        return removed

    def get_alive_entities(self) -> List[Entity]:
        from app.game.ws_manager import ws_agent_manager
        alive = []
        for e in self.entities.values():
            if not e.alive:
                continue
            is_agent = (
                ws_agent_manager.has_agent(e.id)
                or self.get_agent_mode(e.id) in ("ws", "http", "manual")
                or e.id in ("cat-1", "cat-2")
            )
            e.is_agent = is_agent
            if is_agent and not e.agent_name:
                e.agent_name = e.id
            alive.append(e)
        return alive

    def count_by_type(self, entity_type: EntityType) -> int:
        return sum(1 for e in self.entities.values() if e.type == entity_type and e.alive)

    def get_entities_at(self, x: int, y: int) -> List[Entity]:
        return [e for e in self.entities.values() if e.alive and e.position.x == x and e.position.y == y]

    def displace_entity(self, entity: Entity, bounce_dist: int = 2) -> Position:
        """
        Displaces / bounces an entity away by 1-2 tiles into a valid open adjacent direction.
        Updates entity position and facing.
        """
        dirs = [
            (Direction.NORTH, 0, -bounce_dist),
            (Direction.SOUTH, 0, bounce_dist),
            (Direction.EAST, bounce_dist, 0),
            (Direction.WEST, -bounce_dist, 0),
            (Direction.NORTH, 0, -1),
            (Direction.SOUTH, 0, 1),
            (Direction.EAST, 1, 0),
            (Direction.WEST, -1, 0),
        ]
        random.shuffle(dirs)

        for facing, dx, dy in dirs:
            nx = entity.position.x + dx
            ny = entity.position.y + dy
            if self.world.is_in_bounds(nx, ny):
                entity.position = Position(x=nx, y=ny)
                entity.facing = facing
                return entity.position

        # If trapped or on border, attempt standard 1-step move
        return entity.position
