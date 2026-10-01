import json
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.game.replay import build_archive_replay
import app.api.admin as admin


@pytest.fixture(autouse=True)
def reset_admin_simulations():
    admin.default_sim_id = None
    admin.simulations.clear()


@pytest.fixture
def replay_archive(tmp_path, monkeypatch):
    """Creates a mock archive with multiple cats, transcripts, events, and coaching notes."""
    archive_root = tmp_path / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAT_GAME_ARCHIVE", "1")
    monkeypatch.setenv("CAT_GAME_ARCHIVE_DIR", str(archive_root))

    archive_id = "game_20260928_120000_tick00050"
    archive_dir = archive_root / archive_id
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Manifest
    manifest = {
        "format_version": 1,
        "archive_id": archive_id,
        "arena": {"width": 120, "height": 80},
        "final": {"tick": 50, "captures": 2, "mice_remaining": 8},
        "agents": {
            "cat-1": {"kind": "cat", "team": "alpha", "alive": False, "kills": 0},
            "cat-2": {"kind": "cat", "team": "bravo", "alive": True, "kills": 1},
            "cat-3": {"kind": "cat", "team": "charlie", "alive": True, "kills": 0},
        },
    }
    (archive_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Authoritative events
    events = [
        {
            "event_id": "evt-1",
            "type": "cat_killed",
            "tick": 25,
            "data": {
                "killer_id": "cat-2",
                "victim_id": "cat-1",
                "x": 45.0,
                "y": 30.0,
                "reason": "slashed",
            },
        }
    ]
    (archive_dir / "events.json").write_text(json.dumps(events), encoding="utf-8")

    # Agent transcripts for cat-1, cat-2, cat-3
    for agent_id, ticks_data in [
        (
            "cat-1",
            [
                {
                    "tick": 24,
                    "state": {"x": 44.0, "y": 30.0, "facing": 0.0, "hp": 20, "cooldown": 0},
                    "action": {"type": "move", "dx": 1.0, "dy": 0.0},
                    "prompt": "I see cat-2 approaching aggressively. Trying to flank.",
                    "visible": [{"id": "cat-2", "x": 46.0, "y": 30.0, "type": "cat"}],
                },
                {
                    "tick": 25,
                    "state": {"x": 45.0, "y": 30.0, "facing": 0.0, "hp": 0, "cooldown": 0, "alive": False},
                    "action": {"type": "wait"},
                    "prompt": "Critical damage received! Systems shutting down.",
                    "visible": [],
                },
            ],
        ),
        (
            "cat-2",
            [
                {
                    "tick": 24,
                    "state": {"x": 46.0, "y": 30.0, "facing": 3.14, "hp": 100, "cooldown": 0},
                    "action": {"type": "attack", "target_id": "cat-1"},
                    "prompt": "Closing in on cat-1 for the strike.",
                    "visible": [{"id": "cat-1", "x": 44.0, "y": 30.0, "type": "cat"}],
                },
                {
                    "tick": 25,
                    "state": {"x": 45.5, "y": 30.0, "facing": 3.14, "hp": 100, "cooldown": 3},
                    "action": {"type": "slash"},
                    "prompt": "Eliminated cat-1! Re-orienting for next target.",
                    "visible": [],
                },
            ],
        ),
        (
            "cat-3",
            [
                {
                    "tick": 24,
                    "state": {"x": 48.0, "y": 35.0, "facing": 1.57, "hp": 90, "cooldown": 0},
                    "action": {"type": "move", "dx": 0.0, "dy": -1.0},
                    "prompt": "Patrolling perimeter. Hearing commotion nearby.",
                    "visible": [
                        {"id": "cat-1", "x": 44.0, "y": 30.0, "type": "cat"},
                        {"id": "cat-2", "x": 46.0, "y": 30.0, "type": "cat"},
                    ],
                },
                {
                    "tick": 25,
                    "state": {"x": 48.0, "y": 34.0, "facing": 1.57, "hp": 90, "cooldown": 0},
                    "action": {"type": "observe"},
                    "prompt": "Witnessed cat-2 eliminate cat-1! Maintaining standoff distance.",
                    "visible": [{"id": "cat-2", "x": 45.5, "y": 30.0, "type": "cat"}],
                },
            ],
        ),
    ]:
        agent_dir = archive_dir / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        transcript_file = agent_dir / "transcript.jsonl"
        with transcript_file.open("w", encoding="utf-8") as fh:
            for item in ticks_data:
                fh.write(json.dumps(item) + "\n")

        # Coach notes
        coach_file = agent_dir / "coaching_notes.json"
        coach_file.write_text(
            json.dumps([{"tick": 24, "note": f"Strategic directive for {agent_id}"}]),
            encoding="utf-8",
        )

    return {"archive_root": archive_root, "archive_id": archive_id}


def test_build_archive_replay_structure(replay_archive):
    archive_id = replay_archive["archive_id"]
    replay = build_archive_replay(archive_id)

    assert replay is not None
    assert replay["archive_id"] == archive_id
    assert replay["arena"]["width"] == 120
    assert replay["arena"]["height"] == 80
    assert "cat-1" in replay["agents"]
    assert "cat-2" in replay["agents"]
    assert "cat-3" in replay["agents"]

    # Frames
    assert len(replay["frames"]) == 2
    frame24 = next(f for f in replay["frames"] if f["tick"] == 24)
    assert "cat-1" in frame24["cats"]
    assert "cat-2" in frame24["cats"]
    assert "cat-3" in frame24["cats"]

    cat1_f24 = frame24["cats"]["cat-1"]
    assert cat1_f24["x"] == 44.0
    assert cat1_f24["y"] == 30.0
    assert "flank" in cat1_f24["thought"]

    # Deaths
    assert len(replay["deaths"]) == 1
    death = replay["deaths"][0]
    assert death["tick"] == 25
    assert death["killer_id"] == "cat-2"
    assert death["victim_id"] == "cat-1"
    assert death["x"] == 45.0
    assert death["y"] == 30.0
    assert "Eliminated cat-1" in death["killer_thought"]
    assert "Critical damage" in death["victim_thought"]

    # Witness should be cat-3
    assert len(death["witnesses"]) == 1
    witness = death["witnesses"][0]
    assert witness == "cat-3"
    assert "Witnessed" in death["witness_thoughts"]["cat-3"]


def test_build_archive_replay_range_filter(replay_archive):
    archive_id = replay_archive["archive_id"]
    replay = build_archive_replay(archive_id, from_tick=25, to_tick=25)
    assert replay is not None
    assert len(replay["frames"]) == 1
    assert replay["frames"][0]["tick"] == 25


def test_archive_replay_api_endpoint(replay_archive):
    client = TestClient(app)
    archive_id = replay_archive["archive_id"]

    res = client.get(f"/api/archives/{archive_id}/replay")
    assert res.status_code == 200
    data = res.json()
    assert data["archive_id"] == archive_id
    assert len(data["frames"]) == 2
    assert len(data["deaths"]) == 1
    assert data["deaths"][0]["killer_id"] == "cat-2"


def test_archive_replay_api_not_found():
    client = TestClient(app)
    res = client.get("/api/archives/non_existent_archive/replay")
    assert res.status_code == 404
