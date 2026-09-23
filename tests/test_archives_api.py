from pathlib import Path
import json
import pytest
from starlette.testclient import TestClient

from app.main import app
from app.game.simulation import Simulation
from app.models.simulation import SimulationConfig
import app.api.admin as admin


@pytest.fixture(autouse=True)
def reset_admin_simulations():
    admin.default_sim_id = None
    admin.simulations.clear()


@pytest.fixture
def archiving(tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive"
    runtime_dir = tmp_path / "runtime"
    archive_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAT_GAME_ARCHIVE", "1")
    monkeypatch.setenv("CAT_GAME_ARCHIVE_DIR", str(archive_dir))
    monkeypatch.setenv("CAT_AGENT_RUNTIME_DIR", str(runtime_dir))
    return {"archive": archive_dir, "runtime": runtime_dir}


def _played_sim(tick: int = 42, captures: int = 3) -> Simulation:
    sim = Simulation(config=SimulationConfig(populations={"cats": 1, "mice": 10}))
    sim.scheduler.tick = tick
    sim.scheduler.round = 1
    sim.scheduler.captures = captures
    admin.simulations[sim.simulation_id] = sim
    admin.default_sim_id = sim.simulation_id
    return sim


def test_list_archives_empty(archiving):
    client = TestClient(app)
    res = client.get("/api/archives")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 0
    assert data["archives"] == []


def test_manual_capture_tick_zero_fails(archiving):
    client = TestClient(app)
    sim = Simulation()
    admin.simulations[sim.simulation_id] = sim
    admin.default_sim_id = sim.simulation_id

    res = client.post("/api/archives/capture", json={"reason": "test_zero"})
    assert res.status_code == 400
    assert "tick 0" in res.json()["detail"]


def test_manual_capture_and_list(archiving):
    client = TestClient(app)
    sim = _played_sim(tick=100, captures=4)

    res = client.post("/api/archives/capture", json={"reason": "operator_snapshot"})
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    archive_id = data["archive_id"]
    assert "tick00100" in archive_id

    # List archives
    list_res = client.get("/api/archives")
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["total"] == 1
    arch = list_data["archives"][0]
    assert arch["id"] == archive_id
    assert arch["tick"] == 100
    assert arch["captures"] == 4
    assert arch["reason"] == "operator_snapshot"
    assert arch["is_latest"] is True


def test_get_archive_detail_and_file(archiving):
    client = TestClient(app)
    sim = _played_sim(tick=50, captures=2)
    cap_res = client.post("/api/archives/capture", json={"reason": "detail_test"})
    archive_id = cap_res.json()["archive_id"]

    # Detail
    detail_res = client.get(f"/api/archives/{archive_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["id"] == archive_id
    assert detail["manifest"]["final"]["tick"] == 50
    assert len(detail["files"]) > 0

    # Fetch manifest file via endpoint
    file_res = client.get(f"/api/archives/{archive_id}/files/manifest.json")
    assert file_res.status_code == 200
    file_data = file_res.json()
    assert file_data["final"]["tick"] == 50


def test_path_traversal_guards(archiving):
    client = TestClient(app)
    # Traversal in archive_id
    res1 = client.get("/api/archives/..%2F..%2Fetc%2Fpasswd")
    assert res1.status_code in (400, 404)

    # Traversal with valid archive_id
    sim = _played_sim(tick=10)
    cap_res = client.post("/api/archives/capture")
    archive_id = cap_res.json()["archive_id"]

    res2 = client.get(f"/api/archives/{archive_id}/files/../../main.py")
    assert res2.status_code in (400, 404)


def test_delete_archive(archiving):
    client = TestClient(app)
    sim1 = _played_sim(tick=10)
    res1 = client.post("/api/archives/capture", json={"reason": "run1"})
    arch1 = res1.json()["archive_id"]

    sim2 = _played_sim(tick=20)
    res2 = client.post("/api/archives/capture", json={"reason": "run2"})
    arch2 = res2.json()["archive_id"]

    assert client.get("/api/archives").json()["total"] == 2

    # Delete arch2
    del_res = client.delete(f"/api/archives/{arch2}")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # Check list
    list_after = client.get("/api/archives").json()
    assert list_after["total"] == 1
    assert list_after["archives"][0]["id"] == arch1
    assert list_after["archives"][0]["is_latest"] is True


def test_wipe_archives(archiving):
    client = TestClient(app)
    _played_sim(tick=10)
    client.post("/api/archives/capture")
    _played_sim(tick=20)
    client.post("/api/archives/capture")

    assert client.get("/api/archives").json()["total"] == 2

    wipe_res = client.post("/api/archives/wipe")
    assert wipe_res.status_code == 200
    assert wipe_res.json()["deleted_count"] == 2

    assert client.get("/api/archives").json()["total"] == 0


def test_archive_page_served():
    client = TestClient(app)
    res = client.get("/archive")
    assert res.status_code == 200
    assert "Simulation Archives" in res.text
    assert res.headers.get("Cache-Control") == "no-cache"
    assert res.headers.get("X-Frame-Options") == "DENY"

