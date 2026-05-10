"""Integration tests for the MongoDB layer.

Skipped unless ``TEST_MONGODB_URI`` is set. Use a throwaway URI like::

    TEST_MONGODB_URI=mongodb://localhost:27017 pytest -m integration -v

The test creates a uniquely-named database, exercises ping + index creation,
verifies indexes round-trip, and drops the database on teardown.
"""

from __future__ import annotations

import os
import secrets

import pytest

from app.db import collections as col
from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager

_URI = os.environ.get("TEST_MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_URI is None, reason="set TEST_MONGODB_URI to run"),
]


@pytest.fixture
async def mongo():
    db_name = f"abm_test_{secrets.token_hex(4)}"
    mgr = MongoManager(uri=_URI, db_name=db_name)
    try:
        yield mgr
    finally:
        try:
            await mgr.client.drop_database(db_name)
        finally:
            await mgr.close()


async def test_ping(mongo: MongoManager):
    result = await mongo.ping()
    assert result.get("ok") == 1.0


async def test_ensure_indexes_creates_all(mongo: MongoManager):
    await ensure_indexes(mongo.db)

    workflows_info = await mongo.db[col.WORKFLOWS].index_information()
    for name in (
        "workflow_id_unique",
        "slug_unique",
        "owner_id",
        "status",
        "tags",
        "category",
        "updated_at_desc",
        "created_at_desc",
        "node_types",
        "current_version",
    ):
        assert name in workflows_info, f"missing index {name} on workflows"

    versions_info = await mongo.db[col.WORKFLOW_VERSIONS].index_information()
    assert "workflow_id_version_unique" in versions_info
    assert versions_info["workflow_id_version_unique"].get("unique") is True

    audit_info = await mongo.db[col.WORKFLOW_AUDIT_LOGS].index_information()
    assert "workflow_id_created_at_desc" in audit_info


async def test_ensure_indexes_is_idempotent(mongo: MongoManager):
    first = await ensure_indexes(mongo.db)
    second = await ensure_indexes(mongo.db)
    assert first == second
