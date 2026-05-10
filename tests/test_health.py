from fastapi.testclient import TestClient

from app.db.mongo import MongoManager
from app.deps import get_mongo
from app.main import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert "app" in body
    assert "version" in body
    assert "env" in body


def test_cors_preflight_allowed_for_known_origin():
    client = TestClient(app)
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_health_db_returns_503_when_mongo_unreachable():
    """Override get_mongo with a client pointed at an unreachable host so the
    test is deterministic regardless of whether the dev machine happens to
    have a local MongoDB running on the default port."""
    bad_mongo = MongoManager(
        uri="mongodb://127.0.0.1:1/",
        db_name="unreachable",
        connect_timeout_ms=200,
        server_selection_timeout_ms=200,
    )
    app.dependency_overrides[get_mongo] = lambda: bad_mongo
    try:
        client = TestClient(app)
        response = client.get("/health/db")
        assert response.status_code == 503
        body = response.json()
        assert body["detail"]["status"] == "error"
        assert "error" in body["detail"]["mongodb"]
    finally:
        app.dependency_overrides.clear()
