import random
from typing import Dict, List, Optional
from app.models.entity import Entity, EntityType, Position
from app.models.simulation import SpeciesConfig
from app.game.world import World


class EntityManager:
    def __init__(self, world: World, species_config: Dict[str, SpeciesConfig]):
        self.world = world
        self.species_config = species_config
        self.entities: Dict[str, Entity] = {}
        self._next_id: Dict[str, int] = {"cat": 1, "mouse": 1}

    def spawn_entity(
        self,
        entity_type: EntityType,
        pos: Optional[Position] = None,
    ) -> Entity:
        type_key = entity_type.value
        seq = self._next_id.get(type_key, 1)
        self._next_id[type_key] = seq + 1
        entity_id = f"{type_key}-{seq}"

        if pos is None:
            pos = Position(
                x=random.randint(0, self.world.width - 1),
                y=random.randint(0, self.world.height - 1),
            )

        weight = 1
        if type_key in self.species_config:
            weight = self.species_config[type_key].weight

        entity = Entity(
            id=entity_id,
            type=entity_type,
            position=pos,
            alive=True,
            weight=weight,
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
        return [e for e in self.entities.values() if e.alive]

    def count_by_type(self, entity_type: EntityType) -> int:
        return sum(1 for e in self.entities.values() if e.type == entity_type and e.alive)
