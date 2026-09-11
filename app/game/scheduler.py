import random
from typing import Callable, List, Optional
from app.game.entities import EntityManager
from app.game.world import World
from app.models.action import ActionType
from app.models.entity import Entity, EntityType


class Scheduler:
    def __init__(self, world: World, entity_manager: EntityManager):
        self.world = world
        self.entity_manager = entity_manager
        self.tick: int = 0
        self.round: int = 0

    def step_logical_clock(self) -> int:
        self.tick += 1
        # Round increments every completed cycle of active entities or periodic ticks
        active_count = len(self.entity_manager.get_alive_entities())
        if active_count > 0:
            self.round = self.tick // max(1, active_count)
        else:
            self.round = self.tick // 10
        return self.tick

    def execute_simulation_step(self) -> int:
        """
        Executes one simulation step:
        - Advances logical clock
        - For active entities, performs an action step (e.g. random move if unassigned)
        """
        self.step_logical_clock()

        entities = self.entity_manager.get_alive_entities()
        # Shuffle order per round for fairness
        random.shuffle(entities)

        for entity in entities:
            if not entity.alive:
                continue
            legal_moves = self.world.get_legal_actions(entity.position)
            chosen_action = random.choice(legal_moves)
            new_pos = self.world.get_next_position(entity.position, chosen_action)
            entity.position = new_pos

        return self.tick
