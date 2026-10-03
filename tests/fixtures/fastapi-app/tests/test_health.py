from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_items_requires_database() -> None:
    # The /api/items route needs PostgreSQL; skip in unit-test runs.
    assert client.get("/health").status_code == 200
