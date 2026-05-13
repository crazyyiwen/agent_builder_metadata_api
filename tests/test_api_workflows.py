"""End-to-end API tests for the workflow routes.

Uses ``httpx.AsyncClient`` + ``ASGITransport`` so the FastAPI app and the
MongoDB client share the same event loop (TestClient runs the app in a
worker thread with its own loop, which doesn't compose well with
loop-bound async DB clients).
"""

from __future__ import annotations

import os
import secrets

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager
from app.deps import get_db
from app.main import create_app

_URI = os.environ.get("TEST_MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_URI is None, reason="set TEST_MONGODB_URI to run"),
]


# --------------------------- fixtures ---------------------------


@pytest.fixture
async def db():
    name = f"abm_api_{secrets.token_hex(4)}"
    mgr = MongoManager(uri=_URI, db_name=name)
    try:
        await ensure_indexes(mgr.db)
        yield mgr.db
    finally:
        await mgr.client.drop_database(name)
        await mgr.close()


@pytest.fixture
async def client(db):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


def _doc(name: str = "Demo") -> dict:
    return {
        "id": "wf_caller",
        "name": name,
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
             "data": {"name": "S", "config": {}}},
            {"id": "o", "type": "output", "position": {"x": 100, "y": 0},
             "data": {"name": "O", "config": {}}},
        ],
        "edges": [{"id": "e1", "source": "s", "target": "o",
                   "sourceHandle": None, "targetHandle": None}],
    }


def _invalid_doc(name: str = "Bad") -> dict:
    """Edge target references a non-existent node — fails default validation
    with a hard error so create/PUT reject it."""
    return {
        "id": "wf_caller",
        "name": name,
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
             "data": {"name": "S", "config": {}}},
            {"id": "o", "type": "output", "position": {"x": 100, "y": 0},
             "data": {"name": "O", "config": {}}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "o",
             "sourceHandle": None, "targetHandle": None},
            {"id": "e2", "source": "s", "target": "ghost_node",
             "sourceHandle": None, "targetHandle": None},
        ],
    }


# =====================================================================
# CRUD
# =====================================================================


async def test_create_returns_201_with_response_model(client):
    resp = await client.post("/api/workflows", json={"name": "Demo", "doc": _doc()})
    assert resp.status_code == 201
    body = resp.json()
    assert body["meta"]["name"] == "Demo"
    assert body["meta"]["current_version"] == 1
    assert body["doc"]["id"] == body["meta"]["workflow_id"]


async def test_create_with_invalid_doc_returns_422_with_issues(client):
    resp = await client.post(
        "/api/workflows", json={"name": "Bad", "doc": _invalid_doc()}
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert any(i["code"] == "EDGE_TARGET_UNKNOWN" for i in body["issues"])


async def test_create_without_doc_uses_default_skeleton(client):
    resp = await client.post("/api/workflows", json={"name": "Empty"})
    assert resp.status_code == 201
    body = resp.json()
    types = {n["type"] for n in body["doc"]["nodes"]}
    assert types == {"start", "output"}


async def test_get_returns_workflow(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.get(f"/api/workflows/{wid}")
    assert resp.status_code == 200
    assert resp.json()["meta"]["workflow_id"] == wid


async def test_get_missing_returns_404_with_consistent_shape(client):
    resp = await client.get("/api/workflows/wf_missing")
    assert resp.status_code == 404
    assert resp.json()["code"] == "NOT_FOUND"


async def test_list_paginates(client):
    for i in range(3):
        await client.post("/api/workflows", json={"name": f"W{i}", "doc": _doc()})
    resp = await client.get("/api/workflows", params={"page": 1, "page_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pagination"]["total"] == 3
    assert body["pagination"]["page"] == 1
    assert body["pagination"]["page_size"] == 2
    assert body["pagination"]["has_more"] is True
    assert len(body["items"]) == 2


async def test_list_filters_by_name_q(client):
    await client.post("/api/workflows", json={"name": "Customer Support Bot", "doc": _doc()})
    await client.post("/api/workflows", json={"name": "Sales Pipeline", "doc": _doc()})
    resp = await client.get("/api/workflows", params={"q": "customer"})
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["name"] == "Customer Support Bot"


async def test_list_filters_by_status_includes_archived(client):
    create = await client.post("/api/workflows", json={"name": "A", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    await client.post(f"/api/workflows/{wid}/archive")

    default = await client.get("/api/workflows")
    assert default.json()["pagination"]["total"] == 0

    incl = await client.get("/api/workflows", params={"include_deleted": "true"})
    assert incl.json()["pagination"]["total"] == 1


async def test_list_sort_by_name_asc(client):
    for n in ("Cherry", "Apple", "Banana"):
        await client.post("/api/workflows", json={"name": n, "doc": _doc()})
    resp = await client.get(
        "/api/workflows", params={"sort_by": "name", "sort_order": "asc"}
    )
    names = [item["name"] for item in resp.json()["items"]]
    assert names == ["Apple", "Banana", "Cherry"]


async def test_update_bumps_version(client):
    create = await client.post("/api/workflows", json={"name": "A", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    new_doc = _doc("Renamed")
    resp = await client.put(
        f"/api/workflows/{wid}",
        json={"doc": new_doc, "change_note": "edit"},
    )
    assert resp.status_code == 200
    assert resp.json()["meta"]["current_version"] == 2


async def test_update_with_stale_if_match_returns_409(client):
    create = await client.post("/api/workflows", json={"name": "A", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    await client.put(f"/api/workflows/{wid}", json={"doc": _doc()})  # bump to v2
    resp = await client.put(
        f"/api/workflows/{wid}",
        json={"doc": _doc()},
        headers={"If-Match": "1"},  # stale
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "CONFLICT"


async def test_update_with_invalid_if_match_returns_400(client):
    create = await client.post("/api/workflows", json={"name": "A", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.put(
        f"/api/workflows/{wid}",
        json={"doc": _doc()},
        headers={"If-Match": "not-a-number"},
    )
    assert resp.status_code == 400


async def test_patch_metadata_only(client):
    create = await client.post("/api/workflows", json={"name": "Old", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.patch(
        f"/api/workflows/{wid}",
        json={"name": "New", "tags": ["a"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New"
    assert body["tags"] == ["a"]
    assert body["current_version"] == 1


async def test_soft_delete_archives(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.delete(f"/api/workflows/{wid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"


async def test_archive_unarchive_restore_cycle(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]

    arch = await client.post(f"/api/workflows/{wid}/archive")
    assert arch.json()["status"] == "archived"

    unarch = await client.post(f"/api/workflows/{wid}/unarchive")
    assert unarch.json()["status"] == "draft"

    await client.post(f"/api/workflows/{wid}/archive")
    restored = await client.post(f"/api/workflows/{wid}/restore")
    assert restored.json()["status"] == "draft"


async def test_hard_delete_returns_204_and_removes_workflow(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.delete(f"/api/workflows/{wid}/hard-delete")
    assert resp.status_code == 204
    after = await client.get(f"/api/workflows/{wid}")
    assert after.status_code == 404


async def test_duplicate_returns_201_with_new_id(client):
    create = await client.post("/api/workflows", json={"name": "Original", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.post(
        f"/api/workflows/{wid}/duplicate", json={"new_name": "Copied"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["meta"]["workflow_id"] != wid
    assert body["meta"]["name"] == "Copied"
    assert body["meta"]["current_version"] == 1


# =====================================================================
# Validation
# =====================================================================


async def test_validate_endpoint_returns_report_without_persisting(client):
    resp = await client.post("/api/workflows/validate", json=_doc())
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    # No workflow was saved.
    list_resp = await client.get("/api/workflows")
    assert list_resp.json()["pagination"]["total"] == 0


async def test_validate_endpoint_reports_issues(client):
    resp = await client.post("/api/workflows/validate", json=_invalid_doc())
    body = resp.json()
    assert body["valid"] is False
    assert any(i["code"] == "EDGE_TARGET_UNKNOWN" for i in body["issues"])


async def test_validate_existing_workflow(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.post(f"/api/workflows/{wid}/validate")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


# =====================================================================
# Versions
# =====================================================================


async def test_list_versions_newest_first(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    await client.put(f"/api/workflows/{wid}", json={"doc": _doc("v2")})
    await client.put(f"/api/workflows/{wid}", json={"doc": _doc("v3")})
    resp = await client.get(f"/api/workflows/{wid}/versions")
    body = resp.json()
    assert body["pagination"]["total"] == 3
    assert [v["version"] for v in body["items"]] == [3, 2, 1]


async def test_get_specific_version(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc("v1")})
    wid = create.json()["meta"]["workflow_id"]
    await client.put(f"/api/workflows/{wid}", json={"doc": _doc("v2")})
    resp = await client.get(f"/api/workflows/{wid}/versions/1")
    assert resp.status_code == 200
    assert resp.json()["doc"]["name"] == "v1"


async def test_get_version_missing_returns_404(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.get(f"/api/workflows/{wid}/versions/99")
    assert resp.status_code == 404


async def test_create_version_snapshot(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.post(
        f"/api/workflows/{wid}/versions", json={"change_note": "milestone"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["version"] == 2
    assert body["change_note"] == "milestone"


async def test_rollback_writes_new_head_and_audit(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc("v1 doc")})
    wid = create.json()["meta"]["workflow_id"]
    await client.put(f"/api/workflows/{wid}", json={"doc": _doc("v2 doc")})
    await client.put(f"/api/workflows/{wid}", json={"doc": _doc("v3 doc")})
    resp = await client.post(f"/api/workflows/{wid}/versions/1/rollback")
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["current_version"] == 4
    assert body["doc"]["name"] == "v1 doc"


# =====================================================================
# Import / export
# =====================================================================


async def test_import_creates_new_workflow(client):
    payload = {"name": "Imported", "doc": _doc("Imported")}
    resp = await client.post("/api/workflows/import", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["version"] == 1


async def test_import_with_replace_updates_existing(client):
    create = await client.post("/api/workflows", json={"name": "Old", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.post(
        "/api/workflows/import",
        params={"replace_workflow_id": wid},
        json={"doc": _doc("Replaced")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is False
    assert body["version"] == 2


async def test_import_bare_doc(client):
    """Posting a raw WorkflowDoc as the payload (no envelope)."""
    resp = await client.post("/api/workflows/import", json=_doc("From Bare"))
    assert resp.status_code == 200
    assert resp.json()["created"] is True


async def test_export_latest(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc()})
    wid = create.json()["meta"]["workflow_id"]
    resp = await client.get(f"/api/workflows/{wid}/export")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow_id"] == wid
    assert body["version"] == 1
    assert body["doc"]["id"] == wid


async def test_export_specific_version(client):
    create = await client.post("/api/workflows", json={"name": "X", "doc": _doc("v1")})
    wid = create.json()["meta"]["workflow_id"]
    await client.put(f"/api/workflows/{wid}", json={"doc": _doc("v2")})
    resp = await client.get(f"/api/workflows/{wid}/versions/1/export")
    assert resp.status_code == 200
    assert resp.json()["doc"]["name"] == "v1"


async def test_export_missing_workflow_returns_404(client):
    resp = await client.get("/api/workflows/wf_missing/export")
    assert resp.status_code == 404
