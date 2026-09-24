"""Regression tests for automatic game archiving.

The archive exists so that two runs can be compared after the fact, which makes
its failure mode unusually quiet: an archive that is missing, truncated or
mislabelled looks exactly like an archive that is fine until someone tries to
draw a conclusion from it weeks later. The cases below pin the properties that
would be invisible otherwise.

The sharpest of them is ordering. ``Simulation.reset()`` destroys the box
score, and the archiver is the only thing standing between a finished game and
that call, so ``test_boxscore_is_captured_before_reset_wipes_it`` is the test
this whole module is really built around.
"""

import gzip
import json
from pathlib import Path

import pytest

from app.game import archive
from app.game.simulation import Simulation
from app.models.action import ActionType
from app.models.entity import Direction, EntityType, Position
from app.models.simulation import SimulationConfig


@pytest.fixture(autouse=True)
def reset_admin_simulations():
    import app.api.admin as admin
    admin.default_sim_id = None
    admin.simulations.clear()


@pytest.fixture
def archiving(tmp_path, monkeypatch):
    """Turns the archiver on, pointed entirely at temp directories.

    Both redirections matter. Without the archive dir the suite would write
    into the repository, and without the runtime dir it would copy the
    developer's live 55 MB prompt log on every test that resets a simulation.
    """
    archive_dir = tmp_path / "archive"
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    monkeypatch.setenv("CAT_GAME_ARCHIVE", "1")
    monkeypatch.setenv("CAT_GAME_ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(runtime_dir))
    return {"archive": archive_dir, "runtime": runtime_dir}


@pytest.fixture
def reloaded_store():
    """Rebinds ``store``'s module-level paths to the current environment.

    ``store.RUNTIME_DIR`` is resolved once at import, so a test that redirects
    ``CAT_AGENT_RUNTIME_DIR`` has to reload the module for the change to take.
    The teardown reload is the important half: without it the module would stay
    pointed at a deleted temp directory for every test that ran afterwards, in
    any file.
    """
    import importlib

    from actors.cat import store

    importlib.reload(store)
    yield store
    importlib.reload(store)



def _sim(**kwargs) -> Simulation:
    config = SimulationConfig(populations={"cats": 0, "mice": 0}, **kwargs)
    return Simulation(config=config)


def _played(sim: Simulation, tick: int = 12, captures: int = 3) -> Simulation:
    """Puts the simulation in the state of a game that has actually happened.

    Driving real scheduler steps would work but couples these tests to the
    movement rules; what the archiver reads is the counter state, so the
    counters are set directly.
    """
    sim.scheduler.tick = tick
    sim.scheduler.round = 2
    sim.scheduler.captures = captures
    return sim


def _archives(root: Path):
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.is_symlink())


def _only_archive(root: Path) -> Path:
    found = _archives(root)
    assert len(found) == 1, f"expected exactly one archive, found {found}"
    return found[0]


def _manifest(path: Path) -> dict:
    return json.loads((path / "manifest.json").read_text())


# --- the ordering guarantee --------------------------------------------


@pytest.mark.asyncio
async def test_boxscore_is_captured_before_reset_wipes_it(archiving):
    """The whole reason the game server owns archiving rather than the actor.

    `reset()` zeroes the counters in the same call, and the cat actor does not
    find out a reset happened until a later poll. If the snapshot is ever moved
    below `stats.reset()` this test fails and every archive silently becomes a
    record of a game in which nothing occurred.
    """
    sim = _sim()
    cat = sim.entity_manager.spawn_entity(
        EntityType.CAT, pos=Position(x=5, y=5), facing=Direction.EAST
    )
    sim.stats.record_action(
        entity=cat,
        action=ActionType.ATTACK,
        accepted=True,
        prev_x=5,
        prev_y=5,
        new_x=5,
        new_y=5,
        hits=1,
        zone_id=None,
        had_zone_chance=True,
        prey_visible=True,
    )
    _played(sim, tick=40)

    await sim.reset()

    saved = json.loads((_only_archive(archiving["archive"]) / "boxscore.json").read_text())
    assert saved["tick"] == 40
    row = next(c for c in saved["cats"] if c["id"] == cat.id)
    assert row["slash_attempts"] == 1

    # And the live simulation really was wiped, so the archive is the only copy.
    assert sim.get_boxscore()["tick"] == 0


@pytest.mark.asyncio
async def test_reset_still_succeeds_when_archiving_explodes(archiving, monkeypatch):
    """Bookkeeping must never cost the user a working reset button."""
    def boom(_captured):
        raise OSError("disk went away")

    monkeypatch.setattr(archive, "write", boom)
    sim = _played(_sim())

    state = await sim.reset()

    assert state.tick == 0
    assert _archives(archiving["archive"]) == []


# --- what ends up on disk ----------------------------------------------


@pytest.mark.asyncio
async def test_reset_writes_a_manifest_naming_the_final_state(archiving):
    sim = _played(_sim(), tick=137, captures=9)

    await sim.reset()

    manifest = _manifest(_only_archive(archiving["archive"]))
    assert manifest["reason"] == "reset"
    assert manifest["final"]["tick"] == 137
    assert manifest["final"]["captures"] == 9
    assert manifest["simulation_id"] == sim.simulation_id
    assert manifest["schema"] == archive.MANIFEST_SCHEMA_VERSION
    assert "disclosure" in manifest["settings"]
    assert manifest["settings"]["disclosure"]["coach_sees_population"] is True
    assert manifest["settings"]["disclosure"]["coach_sees_coverage_map"] is True
    assert manifest["settings"]["disclosure"]["actor_sees_own_trail"] is False



@pytest.mark.asyncio
async def test_directory_name_sorts_chronologically_and_shows_the_length(archiving):
    sim = _played(_sim(), tick=137)

    await sim.reset()

    name = _only_archive(archiving["archive"]).name
    assert name.endswith(sim.simulation_id)
    assert "__tick00137__" in name
    # Zero-padded so a 9-tick game does not sort after a 137-tick one.
    assert name[:8].isdigit() and name[8] == "T"


@pytest.mark.asyncio
async def test_runtime_files_are_copied_out_of_the_actor_directory(archiving):
    (archiving["runtime"] / "transcript.jsonl").write_text('{"tick":1}\n')
    (archiving["runtime"] / "coaching_notes.md").write_text("# orders\nlunge more\n")
    (archiving["runtime"] / "coach_memory.jsonl").write_text('{"note":"sector cluster"}\n')
    sim = _played(_sim())

    await sim.reset()

    dest = _only_archive(archiving["archive"])
    assert (dest / "transcript.jsonl").read_text() == '{"tick":1}\n'
    assert "lunge more" in (dest / "coaching_notes.md").read_text()
    assert (dest / "coach_memory.jsonl").read_text() == '{"note":"sector cluster"}\n'


@pytest.mark.asyncio
async def test_a_missing_runtime_file_is_recorded_as_absent_not_omitted(archiving):
    """"The actor never wrote one" and "the archiver forgot to look" must differ."""
    sim = _played(_sim())

    await sim.reset()

    files = _manifest(_only_archive(archiving["archive"]))["files"]
    assert files["prompts.jsonl"] == {"present": False}
    assert files["coaching_notes.md"] == {"present": False}


@pytest.mark.asyncio
async def test_a_large_prompt_log_is_stored_compressed_and_still_readable(archiving, monkeypatch):
    """A real prompt log is ~55 MB, which is why this one is not stored raw."""
    monkeypatch.setattr(archive, "GZIP_THRESHOLD_BYTES", 64)
    payload = "".join(json.dumps({"applied_at_tick": i}) + "\n" for i in range(50))
    (archiving["runtime"] / "prompts.jsonl").write_text(payload)
    sim = _played(_sim())

    await sim.reset()

    dest = _only_archive(archiving["archive"])
    assert not (dest / "prompts.jsonl").exists()
    with gzip.open(dest / "prompts.jsonl.gz", "rt") as fh:
        assert fh.read() == payload

    entry = _manifest(dest)["files"]["prompts.jsonl"]
    assert entry["stored_as"] == "prompts.jsonl.gz"
    # Byte counts describe the original, so a later reader can tell how big the
    # game really was without decompressing it.
    assert entry["bytes"] == len(payload)
    assert entry["stored_bytes"] < entry["bytes"]


@pytest.mark.asyncio
async def test_a_small_file_is_left_uncompressed_so_it_stays_greppable(archiving):
    (archiving["runtime"] / "transcript.jsonl").write_text('{"tick":1}\n')
    sim = _played(_sim())

    await sim.reset()

    dest = _only_archive(archiving["archive"])
    assert (dest / "transcript.jsonl").exists()
    assert not (dest / "transcript.jsonl.gz").exists()


@pytest.mark.asyncio
async def test_the_prompt_itself_is_snapshotted(archiving):
    """The skills markdown *is* the independent variable between two runs.

    It changes constantly and usually uncommitted, so a commit hash alone would
    not identify which prompt produced a given box score.
    """
    sim = _played(_sim())

    await sim.reset()

    dest = _only_archive(archiving["archive"])
    assert (dest / "prompt_sources" / "cat" / "actor_skills.md").exists()
    recorded = _manifest(dest)["prompt_sources"]
    assert "cat/actor_skills.md" in recorded
    assert recorded["cat/actor_skills.md"]["sha256"]


@pytest.mark.asyncio
async def test_manifest_records_which_cat_was_driven_by_the_model(archiving):
    """Without this a head-to-head box score can be read exactly backwards."""
    sim = _sim()
    llm = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=2, y=2))
    heuristic = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=8, y=8))
    mouse = sim.entity_manager.spawn_entity(EntityType.MOUSE, pos=Position(x=4, y=4))
    sim.entity_manager.set_agent_mode(llm.id, "manual")
    _played(sim)

    await sim.reset()

    agents = _manifest(_only_archive(archiving["archive"]))["agents"]
    assert agents[llm.id] == "manual"
    assert agents[heuristic.id] == "auto"
    # Mice are not agents under test and would only add noise.
    assert mouse.id not in agents


@pytest.mark.asyncio
async def test_latest_symlink_follows_the_newest_archive(archiving):
    first = _played(_sim(), tick=10)
    await first.reset()
    second = _played(_sim(), tick=20)
    await second.reset()

    latest = archiving["archive"] / "latest"
    assert latest.is_symlink()
    assert _manifest(latest)["final"]["tick"] == 20


# --- when not to archive -----------------------------------------------


@pytest.mark.asyncio
async def test_a_game_with_no_ticks_is_not_archived(archiving):
    """Otherwise double-clicking reset leaves a directory describing nothing."""
    sim = _sim()

    await sim.reset()
    await sim.reset()

    assert _archives(archiving["archive"]) == []


@pytest.mark.asyncio
async def test_archiving_is_off_by_default_under_pytest(tmp_path, monkeypatch):
    """The suite must not touch the developer's live runtime files.

    Nothing here sets CAT_GAME_ARCHIVE, so this reproduces what every other
    test module in the repository sees.
    """
    monkeypatch.delenv("CAT_GAME_ARCHIVE", raising=False)
    monkeypatch.setenv("CAT_GAME_ARCHIVE_DIR", str(tmp_path / "archive"))
    assert archive.is_enabled() is False

    sim = _played(_sim())
    await sim.reset()

    assert not (tmp_path / "archive").exists()


# --- cross-process invariants ------------------------------------------


def test_archiver_and_cat_store_agree_on_the_runtime_directory(monkeypatch):
    """Guards the one duplicated fact between the two processes.

    ``app.game.archive`` deliberately does not import ``actors.cat.store`` just
    to learn a path. That is only safe while the two resolve the same place, so
    the drift is caught here rather than by an archive that is quietly empty.
    """
    monkeypatch.delenv("CAT_AGENT_RUNTIME_DIR", raising=False)
    import importlib

    from actors.cat import store
    importlib.reload(store)

    assert archive.cat_runtime_dir().resolve() == store.RUNTIME_DIR.resolve()


def test_every_file_the_actor_writes_is_on_the_archive_list():
    """A new runtime artefact should not be silently left out of archives."""
    from actors.cat import store

    archived = {name for name, _ in archive.RUNTIME_ARTEFACTS}
    for path in (store.TRANSCRIPT_PATH, store.PROMPTS_PATH, store.COACHING_NOTES_PATH):
        assert path.name in archived


# --- the run configuration ---------------------------------------------


@pytest.mark.asyncio
async def test_the_model_settings_reach_the_archive(archiving, reloaded_store):
    """The independent variable of a prompt experiment, recorded with the result.

    The thinking budget lives in Python and appears nowhere in a transcript or
    a box score, so without this an archive can say what the cat scored but not
    what it was configured to be.
    """
    reloaded_store.write_run_config(
        {"agents": [{"name": "cat_tactician", "thinking_budget": 512}]}
    )

    sim = _played(_sim())
    await sim.reset()

    dest = _only_archive(archiving["archive"])
    saved = json.loads((dest / "run_config.json").read_text())
    assert saved["agents"][0]["thinking_budget"] == 512
    assert saved["written_at"].endswith("Z")
    assert _manifest(dest)["files"]["run_config.json"]["present"] is True


def test_a_game_reset_does_not_delete_the_run_config(tmp_path, monkeypatch):
    """It describes the process, not the game, and outlives any number of games.

    Deleting it alongside the transcript would leave every archive after the
    first reset unable to say what produced it.
    """
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(tmp_path))
    import importlib

    from actors.cat import store
    importlib.reload(store)
    try:
        store.write_run_config({"agents": []})
        store.append_tick({"tick": 1, "observation": {}, "response": {}})
        assert store.RUN_CONFIG_PATH.exists()

        store.reset_transcript()
        store.reset_prompts()
        store.reset_coaching_notes()

        assert not store.TRANSCRIPT_PATH.exists()
        assert store.RUN_CONFIG_PATH.exists()
    finally:
        monkeypatch.undo()
        importlib.reload(store)



def test_agent_settings_are_read_off_the_live_object():
    """Guards against the summary drifting from the agent it describes."""
    from actors.cat.actor import adk_agent, summarize_agent

    summary = summarize_agent(adk_agent)

    assert summary["model"] == adk_agent.model
    assert summary["name"] == adk_agent.name
    # The value the next experiment is about to change.
    assert (
        summary["thinking_budget"]
        == adk_agent.generate_content_config.thinking_config.thinking_budget
    )
    assert summary["output_schema"] == "CatTacticalDecision"


def test_agent_summary_survives_an_agent_missing_every_field():
    """Diagnostics must degrade to nulls, never take down startup."""
    class Bare:
        pass

    from actors.cat.actor import summarize_agent

    summary = summarize_agent(Bare())

    assert summary["model"] is None
    assert summary["thinking_budget"] is None
    assert summary["tools"] == []


# --- multi-agent archiving ---------------------------------------------


@pytest.mark.asyncio
async def test_archiving_captures_all_participating_cat_agents(tmp_path, monkeypatch):
    """Ensures all cat agents participating in a game are archived into per-agent subdirectories."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAT_GAME_ARCHIVE", "1")
    monkeypatch.setenv("CAT_GAME_ARCHIVE_DIR", str(archive_dir))
    monkeypatch.delenv("CAT_AGENT_RUNTIME_DIR", raising=False)

    import tempfile
    import shutil
    tmp_base = Path(tempfile.gettempdir())
    cat1_runtime = tmp_base / "cm_runtime_cat-1"
    cat2_runtime = tmp_base / "cm_runtime_cat-2"
    cat1_runtime.mkdir(parents=True, exist_ok=True)
    cat2_runtime.mkdir(parents=True, exist_ok=True)

    try:
        (cat1_runtime / "transcript.jsonl").write_text('{"tick":1,"cat":"cat-1"}\n')
        (cat1_runtime / "coaching_notes.md").write_text("Orders for cat-1: attack\n")
        (cat1_runtime / "coach_memory.jsonl").write_text('{"cat":"cat-1","memory":"pincer"}\n')

        (cat2_runtime / "transcript.jsonl").write_text('{"tick":1,"cat":"cat-2"}\n')
        (cat2_runtime / "coaching_notes.md").write_text("Orders for cat-2: flank\n")
        (cat2_runtime / "coach_memory.jsonl").write_text('{"cat":"cat-2","memory":"ambush"}\n')

        sim = _played(_sim(), tick=25, captures=2)
        c1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=2, y=2), facing=Direction.NORTH)
        c2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=8, y=8), facing=Direction.SOUTH)
        assert c1.id == "cat-1"
        assert c2.id == "cat-2"

        await sim.reset()

        dest = _only_archive(archive_dir)
        # Check per-agent directories
        assert (dest / "cat-1" / "transcript.jsonl").read_text() == '{"tick":1,"cat":"cat-1"}\n'
        assert "Orders for cat-1" in (dest / "cat-1" / "coaching_notes.md").read_text()
        assert (dest / "cat-2" / "transcript.jsonl").read_text() == '{"tick":1,"cat":"cat-2"}\n'
        assert "Orders for cat-2" in (dest / "cat-2" / "coaching_notes.md").read_text()

        # Check root backwards-compatibility copy (cat-1 as primary)
        assert (dest / "transcript.jsonl").read_text() == '{"tick":1,"cat":"cat-1"}\n'

        # Check manifest
        manifest = _manifest(dest)
        assert "cat-1" in manifest["agents"]
        assert "cat-2" in manifest["agents"]
        assert "cat-1" in manifest["agent_files"]
        assert "cat-2" in manifest["agent_files"]
        assert manifest["agent_files"]["cat-1"]["transcript.jsonl"]["present"] is True
        assert manifest["agent_files"]["cat-2"]["transcript.jsonl"]["present"] is True

        # Check detail loader
        detail = archive.get_archive_detail(dest.name)
        assert detail is not None
        assert "agent_details" in detail
        assert "cat-1" in detail["agent_details"]
        assert "cat-2" in detail["agent_details"]
        assert detail["agent_details"]["cat-1"]["coaching_notes"] == "Orders for cat-1: attack\n"
        assert detail["agent_details"]["cat-2"]["coaching_notes"] == "Orders for cat-2: flank\n"
        assert "cat-1 Coaching Notes" in detail["coaching_notes"]
        assert "cat-2 Coaching Notes" in detail["coaching_notes"]

        # Check list archives
        archives_list = archive.list_archives()
        assert len(archives_list) == 1
        assert archives_list[0]["has_notes"] is True
        assert archives_list[0]["has_coach_memory"] is True
    finally:
        shutil.rmtree(cat1_runtime, ignore_errors=True)
        shutil.rmtree(cat2_runtime, ignore_errors=True)


@pytest.mark.asyncio
async def test_archiving_includes_dead_or_removed_cat_agents(tmp_path, monkeypatch):
    """Cats eliminated during combat are still archived because they participated in the run."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAT_GAME_ARCHIVE", "1")
    monkeypatch.setenv("CAT_GAME_ARCHIVE_DIR", str(archive_dir))
    monkeypatch.delenv("CAT_AGENT_RUNTIME_DIR", raising=False)

    import tempfile
    import shutil
    tmp_base = Path(tempfile.gettempdir())
    cat1_runtime = tmp_base / "cm_runtime_cat-1"
    cat2_runtime = tmp_base / "cm_runtime_cat-2"
    cat1_runtime.mkdir(parents=True, exist_ok=True)
    cat2_runtime.mkdir(parents=True, exist_ok=True)

    try:
        (cat1_runtime / "transcript.jsonl").write_text('{"tick":10,"cat":"cat-1"}\n')
        (cat2_runtime / "transcript.jsonl").write_text('{"tick":8,"cat":"cat-2"}\n')

        sim = _played(_sim(), tick=30, captures=1)
        c1 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=2, y=2), facing=Direction.NORTH)
        c2 = sim.entity_manager.spawn_entity(EntityType.CAT, pos=Position(x=8, y=8), facing=Direction.SOUTH)
        # Record cat-2 in stats then remove entity (simulating combat death)
        sim.stats.get(c2.id).kills = 1
        sim.entity_manager.remove_entity(c2.id)

        # c2 is no longer in sim.entity_manager.entities, but was in stats
        assert c2.id not in sim.entity_manager.entities
        assert c2.id in sim.stats.cats

        await sim.reset()

        dest = _only_archive(archive_dir)
        assert (dest / "cat-1" / "transcript.jsonl").exists()
        assert (dest / "cat-2" / "transcript.jsonl").exists()
        manifest = _manifest(dest)
        assert "cat-1" in manifest["agents"]
        assert "cat-2" in manifest["agents"]
    finally:
        shutil.rmtree(cat1_runtime, ignore_errors=True)
        shutil.rmtree(cat2_runtime, ignore_errors=True)


