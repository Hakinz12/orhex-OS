"""Tests for System Health and Diagnostic Endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_root_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["app"] == "ORHEX OS"
    assert data["tagline"] == "Your World. Understood."
    assert data["version"] == "0.1.0"
    assert "health_check" in data


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ["ok", "degraded"]
    assert data["app_name"] == "ORHEX OS"
    assert data["version"] == "0.1.0"
    assert "adapters" in data
    assert "database" in data["adapters"]
    assert "gemini" in data["adapters"]
    assert "walrus" in data["adapters"]
    assert "tatum" in data["adapters"]
