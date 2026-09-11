import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_get_simulation_state():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/simulations/active/state")
        assert res.status_code == 200
        data = res.json()
        assert "tick" in data
        assert "round" in data
        assert "width" in data
        assert "entities" in data


@pytest.mark.asyncio
async def test_simulation_step_api():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step once
        res = await client.post("/api/simulations/active/step")
        assert res.status_code == 200
        data = res.json()
        assert data["tick"] >= 1


@pytest.mark.asyncio
async def test_security_headers():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/")
        assert res.status_code == 200
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert "Content-Security-Policy" in res.headers
