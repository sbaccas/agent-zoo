import logging
import math
import random
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx
from actors.cat.api import act as cat_agent_act, Observation as CatObservation
from actors.evil_cat.api import act as evil_cat_agent_act
from actors.mouse.heuristic import act as mouse_agent_act, Observation as MouseObservation
from app.game.actions import ActionHandler
from app.game.entities import EntityManager
from app.game.world import World
from app.game.ws_manager import ws_agent_manager, COLLECT_STALE
from app.models.action import ActionType
from app.models.entity import Direction, Entity, EntityType, Position
from app.models.simulation import GameEvent

logger = logging.getLogger("simulation.events")


class Scheduler:
    def __init__(self, world: World, entity_manager: EntityManager, action_handler: ActionHandler, stats=None):
        self.world = world
        self.entity_manager = entity_manager
        self.action_handler = action_handler
        self.tick: int = 0
        self.round: int = 0
        self.captures: int = 0
        # Per-cat cumulative capture counts, keyed by entity id. Kept separate from
        # the global `captures` total because an agent diffing a *shared* counter
        # would credit itself for a rival cat's kill and reinforce its own misses.
        self.scores: Dict[str, int] = {}
        self.events: List[GameEvent] = []
        self.entity_events: Dict[str, List[Dict[str, Any]]] = {}
        self.consecutive_http_errors: dict = {}
        self.deaths: List[Dict[str, Any]] = []
        self.notified_deaths: set = set()
        # Box-score recorder. Most counters are filled in by the action handler;
        # the loop owns only the ones it alone can see, namely locked ticks and
        # capture credit.
        self.stats = stats
        # Inter-Cat Radio / A2A Comms: rolling broadcast log
        self.transmissions: List[Dict[str, Any]] = []
        # How long an agent has to answer an observation, in seconds. Owned by
        # Simulation because it is derived from tick_rate, which is live-tunable.
        # None means "unknown": the budget is then omitted from the wire rather
        # than guessed at, since a wrong figure is worse than no figure.
        self.tick_budget_sec: Optional[float] = None
        # Pending reposition requests scheduled to apply on the next tick
        self.pending_repositions: Dict[str, Tuple[int, int]] = {}
        self.known_agent_ids: Set[str] = set()
        self.game_over: bool = False
        self.winner_id: Optional[str] = None


    def get_scoreboard(self) -> Dict[str, int]:
        """Cumulative captures for every cat on the board, including those on zero.

        Cats absent from `self.scores` have simply not scored yet; they must still
        appear so the agent can see a complete, stable leaderboard from tick 1.
        """
        return {
            e.id: self.scores.get(e.id, 0)
            for e in self.entity_manager.entities.values()
            if e.type == EntityType.CAT
        }

    def step_logical_clock(self) -> int:
        self.tick += 1
        active_count = len(self.entity_manager.get_alive_entities())
        if active_count > 0:
            self.round = self.tick // max(1, active_count)
        else:
            self.round = self.tick // 10
        return self.tick

    def _build_observation_payload(self, entity: Entity, can_act: bool = True) -> dict:
        species_key = entity.type.value
        # All three viewport dimensions are per-species and live-tunable, so
        # they are read fresh every observation rather than cached anywhere.
        vision_radius = 4
        vision_rear = World.FOV_REAR_DEPTH
        vision_half_width = World.FOV_HALF_WIDTH
        if species_key in self.entity_manager.species_config:
            _cfg = self.entity_manager.species_config[species_key]
            vision_radius = _cfg.vision_radius
            vision_rear = _cfg.vision_rear
            vision_half_width = _cfg.vision_half_width

        visible = []
        # Hoisted: also used below to decide which hazard zones this entity can
        # see. Empty for mice, which use a radial check instead of the box.
        fov_tiles: set = set()
        if entity.type == EntityType.CAT:
            fov_tiles = set(self.world.get_fov_tiles(
                entity.position,
                entity.facing,
                distance=vision_radius,
                rear=vision_rear,
                half_width=vision_half_width,
            ))
            # Read once per observation rather than per visible entity: the
            # config is live-tunable, and re-reading it inside the loop would
            # let a mid-loop PATCH annotate some mice and not others, producing
            # a single observation that was internally inconsistent.
            _spec = self.entity_manager.species_config.get(species_key)
            aim_assist = bool(_spec.aim_assist) if _spec else False
            aim_range = _spec.attack_range if _spec else 0
            for other in self.entity_manager.get_alive_entities():
                if other.id != entity.id and (other.position.x, other.position.y) in fov_tiles:
                    seen = {
                        "id": other.id,
                        "type": other.type.value,
                        "position": {"x": other.position.x, "y": other.position.y},
                        "facing": other.facing.value,
                        "hp": other.hp,
                        "max_hp": other.max_hp,
                        "action_cooldown": other.action_cooldown,
                        "locked_action": other.locked_action,
                    }
                    if aim_assist:
                        # Merged in rather than nested under a sub-key because
                        # the actor prompt renders this dict verbatim, and a
                        # nested object costs the model an extra dereference to
                        # read the one field that decides whether to swing.
                        # Absolute `position` is deliberately still sent: the
                        # geometry answers "can I hit it", not "where is it",
                        # and navigation still needs the latter.
                        seen.update(self.world.strike_geometry(
                            entity.position,
                            entity.facing,
                            other.position,
                            aim_range,
                        ))
                    visible.append(seen)
        else:
            for other in self.entity_manager.get_alive_entities():
                if other.id == entity.id:
                    continue
                dist_sq = (other.position.x - entity.position.x)**2 + (other.position.y - entity.position.y)**2
                if dist_sq <= vision_radius**2:
                    visible.append({
                        "id": other.id,
                        "type": other.type.value,
                        "position": {"x": other.position.x, "y": other.position.y},
                        "facing": other.facing.value,
                        "hp": other.hp,
                        "max_hp": other.max_hp,
                        "action_cooldown": other.action_cooldown,
                        "locked_action": other.locked_action,
                    })

        # Visible lethal zones. A cat always sees its own blade regardless of
        # facing (it knows where it swung); everything else must fall inside
        # the normal sight rule for that species.
        visible_zones = []
        for zone in self.action_handler.hazard_zones:
            if not zone.is_lethal_on(self.tick):
                continue
            mine = zone.owner_id == entity.id
            if not mine:
                if entity.type == EntityType.CAT:
                    seen = any((t.x, t.y) in fov_tiles for t in zone.tiles)
                else:
                    seen = any(
                        (t.x - entity.position.x) ** 2 + (t.y - entity.position.y) ** 2 <= vision_radius ** 2
                        for t in zone.tiles
                    )
                if not seen:
                    continue
            visible_zones.append({
                "id": zone.id,
                "owner_id": zone.owner_id,
                "kind": zone.kind,
                "is_mine": mine,
                "tiles": [{"x": t.x, "y": t.y} for t in zone.tiles],
                "created_tick": zone.created_tick,
                "lethal_until_tick": zone.lethal_until_tick,
                # Counts the current tick, so 1 means "lethal now, gone next tick".
                "ticks_remaining": zone.ticks_remaining(self.tick),
            })

        allow_attack = (entity.type == EntityType.CAT)
        if entity.alive:
            legal_actions = [a.value for a in self.world.get_legal_actions(entity.position, allow_attack=allow_attack, facing=entity.facing)]
        else:
            legal_actions = []
            can_act = False

        spec_cfg = self.entity_manager.species_config.get(species_key)
        attack_range = spec_cfg.attack_range if spec_cfg else (2 if entity.type == EntityType.CAT else 0)
        move_cd = spec_cfg.move_cooldown if spec_cfg else 1
        attack_cd = spec_cfg.attack_cooldown if spec_cfg else 0
        lunge_cd = spec_cfg.lunge_cooldown if spec_cfg else 0
        weight = spec_cfg.weight if spec_cfg else 1
        aoe_ticks = spec_cfg.attack_aoe_ticks if spec_cfg else 2

        capabilities = {
            "vision_radius": vision_radius,
            # The other two dimensions of the viewport. Shipped to the agent
            # because the actor manual now derives its membership test from
            # these rather than quoting fixed numbers -- if they were tuned and
            # the prompt still said "3 either side", the manual would be lying
            # about the only rule the actor uses to decide what it can see.
            "vision_rear": vision_rear,
            "vision_half_width": vision_half_width,
            "attack_range": attack_range,
            "move_cooldown": move_cd,
            "attack_cooldown": attack_cd,
            "lunge_cooldown": lunge_cd,
            "weight": weight,
            # How many ticks a slash stays lethal, counting the tick it lands.
            "attack_aoe_ticks": aoe_ticks,
            # Whether `visible_entities` carries the pre-computed forward /
            # offaxis / in_strike_zone fields this tick. Advertised rather than
            # left implicit so the prompt can describe what is actually present
            # instead of guessing: the actor manual has to say either "these
            # fields are authoritative, trust them over your own arithmetic" or
            # nothing at all, and getting that backwards in either direction is
            # worse than the problem the feature exists to solve.
            "aim_assist": bool(spec_cfg.aim_assist) if spec_cfg else False,
            "absorption": bool(getattr(spec_cfg, "absorption", True)) if spec_cfg else (True if entity.type == EntityType.CAT else False),
            "hp": entity.hp,
            "max_hp": entity.max_hp,
            "attack_power": spec_cfg.attack_power if spec_cfg else (34 if entity.type == EntityType.CAT else 0),
            "heal_on_mouse_capture": getattr(spec_cfg, "heal_on_mouse_capture", 40) if entity.type == EntityType.CAT else 0,
            "heal_on_cat_elimination": getattr(spec_cfg, "heal_on_cat_elimination", 100) if entity.type == EntityType.CAT else 0,
        }

        return {
            "tick": self.tick,
            "round": self.round,
            "self": {
                "id": entity.id,
                "type": entity.type.value,
                "position": {"x": entity.position.x, "y": entity.position.y},
                "facing": entity.facing.value,
                "hp": entity.hp,
                "max_hp": entity.max_hp,
                "alive": entity.alive,
                "action_cooldown": entity.action_cooldown,
                "locked_action": entity.locked_action,
                "ready_to_act": (entity.action_cooldown == 0 and entity.alive),
                # Authoritative answer to "will anything I return this tick be
                # applied?". `action_cooldown` cannot express this on its own: the
                # lock branch below decrements before building the payload, so the
                # final locked tick reports a cooldown of 0 while still being
                # skipped. Agents that inferred lock state from the counter
                # misread that tick as playable.
                "can_act": can_act and entity.alive,
            },
            "capabilities": capabilities,
            "visible_entities": visible,
            # Tiles that are lethal right now. Prey needs these to route around
            # a blade that outlives the swing; the attacker needs them to see
            # how long its own zone will keep working.
            "hazard_zones": visible_zones,
            "legal_actions": legal_actions,
            # Raw outcome state. Deliberately not interpreted: the agent infers
            # hit/miss by diffing its own `score` against the previous tick.
            "score": self.scores.get(entity.id, 0),
            "scoreboard": self.get_scoreboard(),
            "eliminated_cats": [
                e.id for e in self.entity_manager.entities.values()
                if e.type == EntityType.CAT and not e.alive
            ],
            # A2A Comms: audible transmissions from other cats (broadcasts + DMs targeted to this entity or its coach)
            "incoming_transmissions": [
                t for t in self.transmissions[-25:]
                if t.get("sender_id") not in (entity.id, f"coach({entity.id})")
                and (
                    t.get("recipient_id") in (None, "*", "broadcast", "all", entity.id, f"coach({entity.id})")
                )
            ],
        }

    def _register_capture(self, owner_id: str, victim: Entity, source: str, zone_id: Optional[str] = None) -> None:
        """Credit a kill and emit its events.

        Shared by the instant swing and the lingering-zone sweep so a mouse
        that walks into a blade is scored identically to one caught by it,
        rather than dying silently with no capture event and no score.

        `zone_id` is supplied only by the lingering-zone sweep. The instant
        swing leaves it None because the action handler has already filed that
        slash as landed; passing it twice would count the same blade's hit
        against the box score on both paths.
        """
        is_cat = (victim.type == EntityType.CAT)
        death_record = {
            "id": victim.id,
            "type": victim.type.value,
            "tick": self.tick,
            "round": self.round,
            "killed_by": owner_id,
            "source": source,
            "position": {"x": victim.position.x, "y": victim.position.y},
        }
        self.deaths.append(death_record)

        self.captures += 1
        absorbed_score = 0
        is_evil = owner_id in ("cat-3", "evil-cat")

        if is_cat:
            cat_cfg = self.entity_manager.species_config.get("cat")
            if cat_cfg is None or getattr(cat_cfg, "absorption", True):
                victim_score = self.scores.get(victim.id, 0)
                if victim_score == 0 and self.stats is not None:
                    victim_stat = self.stats.cats.get(victim.id)
                    if victim_stat:
                        v_stat_score = (
                            victim_stat.absorbed_kills
                            if (victim.id in ("cat-3", "evil-cat") and victim_stat.absorbed_kills > 0)
                            else (victim_stat.kills + victim_stat.absorbed_kills)
                        )
                        victim_score = max(victim_score, v_stat_score)
                if victim_score > 0:
                    absorbed_score = victim_score

        if is_evil:
            earned_score = absorbed_score if is_cat else 0
        else:
            earned_score = 1 + absorbed_score

        self.scores[owner_id] = self.scores.get(owner_id, 0) + earned_score
        death_record["absorbed_score"] = absorbed_score
        if self.stats is not None:
            self.stats.record_kill(
                owner_id,
                zone_id,
                absorbed_kills=absorbed_score,
                is_evil=is_evil,
                is_cat=is_cat,
            )

        owner = self.entity_manager.entities.get(owner_id)
        healed_hp = 0
        if owner and owner.type == EntityType.CAT and owner.alive:
            cat_cfg = self.entity_manager.species_config.get("cat")
            if is_cat:
                heal_target = getattr(cat_cfg, "heal_on_cat_elimination", 100) if cat_cfg else 100
            else:
                heal_target = getattr(cat_cfg, "heal_on_mouse_capture", 40) if cat_cfg else 40
            old_hp = owner.hp
            owner.hp = min(owner.max_hp, owner.hp + heal_target)
            healed_hp = owner.hp - old_hp

        # Matter-of-fact event payload entries for the agents
        if owner_id not in self.entity_events:
            self.entity_events[owner_id] = []
        if is_cat:
            self.entity_events[owner_id].append({
                "tick": self.tick,
                "type": "elimination",
                "target": victim.id,
                "target_type": "cat",
                "source": source,
                "absorbed_score": absorbed_score,
                "healed_hp": healed_hp,
                "hp": owner.hp if owner else 100,
            })
        else:
            self.entity_events[owner_id].append({
                "tick": self.tick,
                "type": "elimination",
                "target": victim.id,
                "target_type": "mouse",
                "source": source,
                "healed_hp": healed_hp,
                "hp": owner.hp if owner else 100,
            })

        if victim.id not in self.entity_events:
            self.entity_events[victim.id] = []
        self.entity_events[victim.id].append({
            "tick": self.tick,
            "type": "eliminated",
            "by": owner_id,
            "source": source,
        })

        o_pos = f"({owner.position.x}, {owner.position.y})" if owner else "?"
        heal_suffix = f" (+{healed_hp} HP -> {owner.hp}/{owner.max_hp})" if healed_hp > 0 else ""
        if is_cat:
            absorb_suffix = f" (Absorbed +{absorbed_score} kills! Total score: {self.scores[owner_id]})" if absorbed_score > 0 else ""
            msg = (
                f"⚡ [TICK {self.tick} | R{self.round}] GLORIOUS CONQUEST: {owner_id} at {o_pos} "
                f"eliminated rival predator {victim.id} via {source}! ({victim.id} is out of the game!){heal_suffix}{absorb_suffix}"
            )
            event_type = "elimination"
        else:
            msg = (
                f"✨ [TICK {self.tick} | R{self.round}] FEAST OF CHAMPIONS: {owner_id} at {o_pos} "
                f"eliminated {victim.id} at ({victim.position.x}, {victim.position.y}) "
                f"via {source}!{heal_suffix} (Total captures: {self.captures})"
            )
            event_type = "elimination"

        logger.info(msg)
        print(msg, flush=True)
        self.events.append(GameEvent(
            tick=self.tick,
            round=self.round,
            event_type=event_type,
            actor_id=owner_id,
            target_id=victim.id,
            position=victim.position,
            message=msg,
            details={
                "source": source,
                "victim_type": victim.type.value,
                "zone_id": zone_id,
                "healed_hp": healed_hp,
                "hp": owner.hp if owner else 100,
            },
        ))

    def _register_damage(self, owner_id: str, victim: Entity, source: str, zone_id: Optional[str] = None, damage: int = 34) -> None:
        """Record a hit that damages but does not eliminate an entity."""
        # Matter-of-fact event payload entries for the agents
        if owner_id not in self.entity_events:
            self.entity_events[owner_id] = []
        self.entity_events[owner_id].append({
            "tick": self.tick,
            "type": "damage_dealt",
            "target": victim.id,
            "target_type": victim.type.value,
            "amount": damage,
            "target_hp": victim.hp,
            "target_max_hp": victim.max_hp,
            "source": source,
        })

        if victim.id not in self.entity_events:
            self.entity_events[victim.id] = []
        self.entity_events[victim.id].append({
            "tick": self.tick,
            "type": "damage_taken",
            "source_id": owner_id,
            "amount": damage,
            "remaining_hp": victim.hp,
            "max_hp": victim.max_hp,
            "source": source,
        })

        owner = self.entity_manager.entities.get(owner_id)
        o_pos = f"({owner.position.x}, {owner.position.y})" if owner else "?"
        msg = (
            f"🩸 [TICK {self.tick} | R{self.round}] VICIOUS BLOW: {owner_id} at {o_pos} "
            f"slashed {victim.id} at ({victim.position.x}, {victim.position.y}) "
            f"via {source} for {damage} damage! ({victim.id} HP: {victim.hp}/{victim.max_hp})"
        )
        logger.info(msg)
        print(msg, flush=True)
        self.events.append(GameEvent(
            tick=self.tick,
            round=self.round,
            event_type="damage",
            actor_id=owner_id,
            target_id=victim.id,
            position=victim.position,
            message=msg,
            details={
                "source": source,
                "damage": damage,
                "victim_hp": victim.hp,
                "victim_max_hp": victim.max_hp,
                "zone_id": zone_id,
            },
        ))

    def _resolve_hazard_zones(self) -> None:
        """Kill or damage anything standing in a live zone, then retire expired zones.

        Runs after every entity has moved, which is the whole point: resolving
        before movement would let prey step into a lethal tile and out again
        untouched, making the zone's lifetime decorative.
        """
        for zone in self.action_handler.hazard_zones:
            if not zone.is_lethal_on(self.tick):
                continue
            owner = self.entity_manager.entities.get(zone.owner_id)
            owner_spec = self.entity_manager.species_config.get(owner.type.value) if owner else None
            atk_power = owner_spec.attack_power if owner_spec and owner_spec.attack_power > 0 else 34
            for tile in zone.tiles:
                for victim in self.entity_manager.get_entities_at(tile.x, tile.y):
                    if victim.id != zone.owner_id and victim.alive:
                        if self.action_handler.has_zone_hit(zone.id, self.tick, victim.id):
                            continue
                        self.action_handler.record_zone_hit(zone.id, self.tick, victim.id)
                        if victim.type == EntityType.MOUSE:
                            victim.hp = 0
                            victim.alive = False
                        else:
                            victim.hp = max(0, victim.hp - atk_power)
                            if victim.hp <= 0:
                                victim.hp = 0
                                victim.alive = False
                        if not victim.alive:
                            self._register_capture(zone.owner_id, victim, f"{zone.kind} zone", zone_id=zone.id)
                        else:
                            self._register_damage(zone.owner_id, victim, f"{zone.kind} zone", zone_id=zone.id, damage=atk_power)

        expired = [z for z in self.action_handler.hazard_zones if z.lethal_until_tick < self.tick]
        for zone in expired:
            self.events.append(GameEvent(
                tick=self.tick,
                round=self.round,
                event_type="zone_expired",
                actor_id=zone.owner_id,
                position=zone.tiles[0] if zone.tiles else None,
                message=f"🌫️ [TICK {self.tick} | R{self.round}] FADE: {zone.owner_id}'s slash zone dissipated",
                details={"zone_id": zone.id},
            ))
        if expired:
            self.action_handler.hazard_zones = [
                z for z in self.action_handler.hazard_zones if z.lethal_until_tick >= self.tick
            ]

    async def execute_simulation_step(self, skip_entity_id: Optional[str] = None, advance_clock: bool = True) -> int:
        if advance_clock:
            self.step_logical_clock()

        # Apply any pending asset repositions staged for this tick
        if self.pending_repositions:
            for entity_id, (px, py) in list(self.pending_repositions.items()):
                ent = self.entity_manager.entities.get(entity_id)
                if ent and ent.alive:
                    old_pos = (ent.position.x, ent.position.y)
                    ent.position = Position(x=px, y=py)
                    self.events.append(GameEvent(
                        tick=self.tick,
                        round=self.round,
                        event_type="reposition",
                        actor_id=ent.id,
                        position=ent.position,
                        message=f"📌 [TICK {self.tick} | R{self.round}] REPOSITION: {ent.id} relocated from {old_pos} to ({px}, {py})",
                        details={"from": {"x": old_pos[0], "y": old_pos[1]}, "to": {"x": px, "y": py}},
                    ))
            self.pending_repositions.clear()

        entities = self.entity_manager.get_alive_entities()
        cats = [e for e in entities if e.type == EntityType.CAT and e.alive]
        mice = [e for e in entities if e.type == EntityType.MOUSE and e.alive]
        random.shuffle(cats)
        random.shuffle(mice)

        # 1. Cats turn — Phase 1a: Cooldowns & simultaneous action collection for all cats alive at tick start
        planned_cat_actions: List[Tuple[Entity, ActionType]] = []
        for cat in cats:
            if cat.id == skip_entity_id:
                continue

            mode = self.entity_manager.get_agent_mode(cat.id)
            has_ws = ws_agent_manager.has_agent(cat.id)
            if has_ws or mode in ("ws", "http"):
                self.known_agent_ids.add(cat.id)

            # OSRS action lock: if cat is recovering/animating previous action, decrement and skip turn
            if cat.action_cooldown > 0:
                if self.stats is not None:
                    # The only counter the action handler cannot observe: this
                    # branch never reaches it.
                    self.stats.record_locked_tick(cat)
                cat.action_cooldown -= 1
                if cat.action_cooldown == 0:
                    cat.locked_action = None
                # No observation is dispatched here any more. Every connected
                # agent, locked or free, is served by the dispatch phase at the
                # end of the step, which sees the final board rather than a
                # half-resolved one.
                continue

            if mode == "manual":
                # Controlled manually by human player via UI / HTTP
                continue

            action_val = None
            if has_ws:
                collected = ws_agent_manager.collect_action(cat.id)
                if self.stats is not None:
                    self.stats.record_decision_source(
                        cat,
                        collected.status,
                        engine=(collected.action or {}).get("engine"),
                    )
                if collected.action:
                    action_data = collected.action
                    action_val = action_data.get("action")
                    thought = action_data.get("thought", "")
                    strategy = action_data.get("strategy", "")
                    broadcast_msg = action_data.get("broadcast_msg") or action_data.get("radio_msg")
                    if broadcast_msg and str(broadcast_msg).strip():
                        b_text = str(broadcast_msg).strip()
                        transmission_record = {
                            "tick": self.tick,
                            "round": self.round,
                            "sender_id": cat.id,
                            "recipient_id": "*",
                            "message": b_text,
                            "type": "BROADCAST",
                            "protocol": "a2a/v1",
                            "position": {"x": cat.position.x, "y": cat.position.y},
                        }
                        self.transmissions.append(transmission_record)
                        if len(self.transmissions) > 200:
                            self.transmissions.pop(0)
                        self.events.append(GameEvent(
                            tick=self.tick,
                            round=self.round,
                            event_type="transmission",
                            actor_id=cat.id,
                            position=cat.position,
                            message=f"📻 [TICK {self.tick} | R{self.round}] RADIO: {cat.id} broadcast: \"{b_text}\"",
                            details={"sender_id": cat.id, "message": b_text},
                        ))

                    # Process explicit A2A tool calls (DMs, proposals, broadcasts)
                    tool_calls = action_data.get("tool_calls") or []
                    for tc in tool_calls:
                        t_rec = tc.get("recipient_id", "*")
                        t_msg = tc.get("message", "")
                        if t_msg and str(t_msg).strip():
                            t_text = str(t_msg).strip()
                            t_type = tc.get("message_type", "DIRECT_MESSAGE" if t_rec not in ("*", "broadcast", "all") else "BROADCAST")
                            transmission_record = {
                                "tick": self.tick,
                                "round": self.round,
                                "sender_id": cat.id,
                                "recipient_id": t_rec,
                                "message": t_text,
                                "type": t_type,
                                "protocol": "a2a/v1",
                                "position": {"x": cat.position.x, "y": cat.position.y},
                            }
                            self.transmissions.append(transmission_record)
                            if len(self.transmissions) > 200:
                                self.transmissions.pop(0)

                            is_dm = t_rec and t_rec not in ("*", "broadcast", "all")
                            tag = f"A2A DM: {cat.id} -> {t_rec}" if is_dm else f"A2A BROADCAST: {cat.id}"
                            self.events.append(GameEvent(
                                tick=self.tick,
                                round=self.round,
                                event_type="a2a_message",
                                actor_id=cat.id,
                                target_id=t_rec if is_dm else None,
                                position=cat.position,
                                message=f"📻 [TICK {self.tick} | R{self.round}] {tag}: \"{t_text}\"",
                                details=transmission_record,
                            ))
                    print(f"🤖 [WS AGENT {cat.id} @ Tick {self.tick}] -> {action_val} | Strategy: {strategy} | Thought: \"{thought}\"", flush=True)
                elif collected.status == COLLECT_STALE:
                    print(f"🗑️ [WS AGENT {cat.id} @ Tick {self.tick}] Discarded answer to tick {collected.answered_tick} (board is at {collected.expected_tick}) -> heuristic", flush=True)
                else:
                    print(f"⏳ [WS AGENT {cat.id} @ Tick {self.tick}] Missed the window -> heuristic", flush=True)
                # Deliberately no 'stay' default. A missed deadline hands the turn
                # to the heuristic below; defaulting to stay here is what used to
                # make that fallback unreachable, because 'stay' is truthy.
            elif mode == "http":
                agent_url = self.entity_manager.get_agent_url(cat.id)
                err_count = self.consecutive_http_errors.get(cat.id, 0)
                # If agent has failed consecutively, back off and retry every 5 ticks to avoid stalling the simulation loop
                should_call = (err_count < 2) or (self.tick % 5 == 0)
                if agent_url and should_call:
                    try:
                        obs = self._build_observation_payload(cat)
                        async with httpx.AsyncClient(timeout=3.0) as client:
                            resp = await client.post(agent_url, json=obs)
                            if resp.status_code == 200:
                                data = resp.json()
                                action_val = data.get("action")
                                thought = data.get("thought", "")
                                broadcast_msg = data.get("broadcast_msg") or data.get("radio_msg")
                                if broadcast_msg and str(broadcast_msg).strip():
                                    b_text = str(broadcast_msg).strip()
                                    self.transmissions.append({
                                        "tick": self.tick,
                                        "round": self.round,
                                        "sender_id": cat.id,
                                        "message": b_text,
                                        "position": {"x": cat.position.x, "y": cat.position.y},
                                    })
                                    if len(self.transmissions) > 200:
                                        self.transmissions.pop(0)
                                self.consecutive_http_errors[cat.id] = 0
                                print(f"🤖 [HTTP AGENT {cat.id} @ Tick {self.tick}] -> {action_val} | Thought: \"{thought}\"", flush=True)
                    except Exception as err:
                        self.consecutive_http_errors[cat.id] = err_count + 1
                        print(f"⚠️ [HTTP AGENT {cat.id}] Error contacting {agent_url}: {err} (falling back to fast heuristic)", flush=True)

            # In auto mode (or if HTTP fallback needed), execute fast in-memory heuristic brain
            if not action_val:
                obs_payload = self._build_observation_payload(cat)
                cat_obs = CatObservation(**obs_payload)
                if cat.id in ("cat-3", "evil-cat"):
                    act_res = evil_cat_agent_act(cat_obs, use_llm=False)
                else:
                    act_res = cat_agent_act(cat_obs, use_llm=False)
                action_val = act_res.action
                b_msg = getattr(act_res, "broadcast_msg", None)
                if b_msg and str(b_msg).strip():
                    b_text = str(b_msg).strip()
                    self.transmissions.append({
                        "tick": self.tick,
                        "round": self.round,
                        "sender_id": cat.id,
                        "message": b_text,
                        "position": {"x": cat.position.x, "y": cat.position.y},
                    })
                    if len(self.transmissions) > 200:
                        self.transmissions.pop(0)
                    self.events.append(GameEvent(
                        tick=self.tick,
                        round=self.round,
                        event_type="transmission",
                        actor_id=cat.id,
                        position=cat.position,
                        message=f"📻 [TICK {self.tick} | R{self.round}] RADIO: {cat.id} broadcast: \"{b_text}\"",
                        details={"sender_id": cat.id, "message": b_text},
                    ))

            if action_val in [a.value for a in ActionType]:
                planned_cat_actions.append((cat, ActionType(action_val)))

        # Phase 1b: Simultaneous execution of collected cat actions.
        # Cats that started the tick alive and chose ATTACK still execute their
        # simultaneous swing on this tick even if another cat's slash in the same
        # tick reduced their HP to 0. Capture healing and death registration run
        # immediately after all same-tick swings have landed so shuffle order
        # cannot deny a simultaneous counter-slash.
        pending_slash_outcomes: List[Tuple[str, List[str], int]] = []
        for cat, action_enum in planned_cat_actions:
            if not cat.alive and action_enum != ActionType.ATTACK:
                continue
            res = self.action_handler.apply_action(cat, action_enum, self.tick)
            if res.action == ActionType.ATTACK and res.accepted:
                slash_details = res.details or {}
                self.events.append(GameEvent(
                    tick=self.tick,
                    round=self.round,
                    event_type="slash",
                    actor_id=cat.id,
                    position=cat.position,
                    message=f"⚔️ [TICK {self.tick} | R{self.round}] SLASH: {cat.id} executed slash attack ({len(res.affected_entities)} hits)",
                    details=slash_details,
                ))
                slash_dmg = slash_details.get("damage", 34)
                pending_slash_outcomes.append((cat.id, list(res.affected_entities or []), slash_dmg))

        for attacker_id, affected_ids, slash_dmg in pending_slash_outcomes:
            for victim_id in affected_ids:
                victim = self.entity_manager.entities.get(victim_id)
                if victim:
                    if not victim.alive:
                        self._register_capture(attacker_id, victim, "slash impact")
                    else:
                        self._register_damage(attacker_id, victim, "slash impact", damage=slash_dmg)

        # 2. Mice turn
        for mouse in mice:
            if not mouse.alive or mouse.id == skip_entity_id:
                continue

            mode = self.entity_manager.get_agent_mode(mouse.id)
            has_ws = ws_agent_manager.has_agent(mouse.id)

            # OSRS action lock: if mouse is recovering/animating previous action, decrement and skip turn
            if mouse.action_cooldown > 0:
                mouse.action_cooldown -= 1
                if mouse.action_cooldown == 0:
                    mouse.locked_action = None
                # Observation dispatch happens once, at the end of the step.
                continue

            if mode == "manual":
                # Controlled manually by human player via UI / HTTP
                continue

            action_val = None
            if has_ws:
                collected = ws_agent_manager.collect_action(mouse.id)
                if self.stats is not None:
                    self.stats.record_decision_source(
                        mouse,
                        collected.status,
                        engine=(collected.action or {}).get("engine"),
                    )
                if collected.action:
                    action_data = collected.action
                    action_val = action_data.get("action")
                    thought = action_data.get("thought", "")
                    print(f"🤖 [WS AGENT {mouse.id} @ Tick {self.tick}] -> {action_val} | Thought: \"{thought}\"", flush=True)
                elif collected.status == COLLECT_STALE:
                    print(f"🗑️ [WS AGENT {mouse.id} @ Tick {self.tick}] Discarded answer to tick {collected.answered_tick} (board is at {collected.expected_tick}) -> heuristic", flush=True)
                else:
                    print(f"⏳ [WS AGENT {mouse.id} @ Tick {self.tick}] Missed the window -> heuristic", flush=True)
            elif mode == "http":
                agent_url = self.entity_manager.get_agent_url(mouse.id)
                err_count = self.consecutive_http_errors.get(mouse.id, 0)
                should_call = (err_count < 2) or (self.tick % 5 == 0)
                if agent_url and should_call:
                    try:
                        obs = self._build_observation_payload(mouse)
                        async with httpx.AsyncClient(timeout=3.0) as client:
                            resp = await client.post(agent_url, json=obs)
                            if resp.status_code == 200:
                                data = resp.json()
                                action_val = data.get("action")
                                thought = data.get("thought", "")
                                self.consecutive_http_errors[mouse.id] = 0
                                print(f"🤖 [HTTP AGENT {mouse.id} @ Tick {self.tick}] -> {action_val} | Thought: \"{thought}\"", flush=True)
                    except Exception as err:
                        self.consecutive_http_errors[mouse.id] = err_count + 1
                        print(f"⚠️ [HTTP AGENT {mouse.id}] Error contacting {agent_url}: {err}", flush=True)

            # In auto mode (or if HTTP fallback needed), execute mouse_agent brain
            if not action_val:
                obs_payload = self._build_observation_payload(mouse)
                mouse_obs = MouseObservation(**obs_payload)
                act_res = mouse_agent_act(mouse_obs)
                action_val = act_res.action


            if action_val in [a.value for a in ActionType]:
                self.action_handler.apply_action(mouse, ActionType(action_val), self.tick)

            # Check for bump / displacement collision with Cats
            overlapping_cats = [
                c for c in self.entity_manager.get_alive_entities()
                if c.type == EntityType.CAT and c.position.x == mouse.position.x and c.position.y == mouse.position.y
            ]
            if overlapping_cats:
                cat_threat = overlapping_cats[0]
                old_x, old_y = mouse.position.x, mouse.position.y
                self.entity_manager.displace_entity(mouse, bounce_dist=2)
                if mouse.id not in self.entity_events:
                    self.entity_events[mouse.id] = []
                self.entity_events[mouse.id].append({
                    "tick": self.tick,
                    "type": "bump",
                    "target": cat_threat.id,
                    "source": "collision",
                })
                if cat_threat.id not in self.entity_events:
                    self.entity_events[cat_threat.id] = []
                self.entity_events[cat_threat.id].append({
                    "tick": self.tick,
                    "type": "bump",
                    "target": mouse.id,
                    "source": "collision",
                })
                msg = f"🔄 [TICK {self.tick} | R{self.round}] BUMP: {mouse.id} collided with {cat_threat.id} at ({old_x}, {old_y}) -> bounced to ({mouse.position.x}, {mouse.position.y}) (facing {mouse.facing.value.upper()})"
                logger.info(msg)
                print(msg, flush=True)
                self.events.append(GameEvent(
                    tick=self.tick,
                    round=self.round,
                    event_type="bump",
                    actor_id=mouse.id,
                    target_id=cat_threat.id,
                    position=mouse.position,
                    message=msg,
                ))

        # 3. Hazard zones. Deliberately last: a zone must judge the board as it
        # ends the tick, so stepping into a live blade is fatal rather than
        # survivable by passing through between phases.
        self._resolve_hazard_zones()

        # 3b. Check if only 1 agent cat remains alive (Game Over / Last Agent Standing)
        self._check_game_over()

        # 4. Dispatch. Every connected agent is handed the finished board and
        # then left alone until the next step collects whatever it managed to
        # produce. Nothing here is awaited on the agent's behalf, so N agents
        # share one window rather than dividing it, and a slow agent costs the
        # simulation nothing.
        await self._dispatch_observations(skip_entity_id=skip_entity_id)

        return self.tick

    def _check_game_over(self) -> None:
        """Marks the match as game_over when only 1 agent cat remains alive after combat eliminations."""
        if self.game_over:
            return
        all_cats = [e for e in self.entity_manager.entities.values() if e.type == EntityType.CAT]
        if len(all_cats) < 2:
            return

        # Identify the competing agent cats (cat-1, cat-2, cat-3, or any WS/HTTP connected cat agent)
        agent_cats = [
            c for c in all_cats
            if c.id in self.known_agent_ids
            or ws_agent_manager.has_agent(c.id)
            or self.entity_manager.get_agent_mode(c.id) in ("ws", "http")
            or c.id in ("cat-1", "cat-2", "cat-3")
        ]
        if len(agent_cats) < 2:
            return

        alive_agents = [c for c in agent_cats if c.alive]
        dead_agents = [c for c in agent_cats if not c.alive]
        if not dead_agents or len(alive_agents) > 1:
            return

        self.game_over = True
        if len(alive_agents) == 1:
            winner = alive_agents[0]
            self.winner_id = winner.id
            win_score = self.scores.get(winner.id, 0)
            msg = (
                f"🏆 [TICK {self.tick} | R{self.round}] VICTORY: {winner.id} is the LAST AGENT STANDING "
                f"and wins the match! (Score: {win_score} | HP: {winner.hp}/{winner.max_hp})"
            )
            pos = winner.position
        else:
            best_cat = max(agent_cats, key=lambda c: self.scores.get(c.id, 0))
            self.winner_id = best_cat.id
            win_score = self.scores.get(best_cat.id, 0)
            msg = (
                f"🏆 [TICK {self.tick} | R{self.round}] MATCH OVER (MUTUAL KNOCKOUT): "
                f"All agent cats eliminated! Winner by score: {best_cat.id} (Score: {win_score})"
            )
            pos = best_cat.position

        logger.info(msg)
        print(msg, flush=True)
        self.events.append(GameEvent(
            tick=self.tick,
            round=self.round,
            event_type="game_over",
            actor_id=self.winner_id,
            position=pos,
            message=msg,
            details={
                "winner_id": self.winner_id,
                "score": win_score,
                "alive_agents": [a.id for a in alive_agents],
                "eliminated_agents": [d.id for d in dead_agents],
            },
        ))

    async def _dispatch_observations(self, skip_entity_id: Optional[str] = None) -> None:
        """Sends each connected agent the observation it will answer next tick.

        `can_act` describes the *next* tick, not this one, because that is the
        tick the agent's reply will execute on. A cooldown of zero here means the
        next turn is playable; any positive value means it will be skipped.
        Dead entities get a single final death notification with can_act=False,
        after which no further observations or actions are processed.
        """
        for entity in list(self.entity_manager.entities.values()):
            if entity.id == skip_entity_id:
                continue
            if not ws_agent_manager.has_agent(entity.id):
                continue
            if self.entity_manager.get_agent_mode(entity.id) == "manual":
                continue

            if not entity.alive:
                if entity.id not in self.notified_deaths:
                    self.notified_deaths.add(entity.id)
                    obs = self._build_observation_payload(entity, can_act=False)
                    await ws_agent_manager.send_observation(
                        entity.id, obs, budget_sec=self.tick_budget_sec
                    )
                continue

            obs = self._build_observation_payload(
                entity, can_act=(entity.action_cooldown == 0)
            )
            await ws_agent_manager.send_observation(
                entity.id, obs, budget_sec=self.tick_budget_sec
            )
