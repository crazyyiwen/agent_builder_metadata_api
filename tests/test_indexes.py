"""Unit tests for the index plan. Run without MongoDB."""

from app.db import collections as col
from app.db.indexes import (
    INDEX_PLAN,
    WORKFLOW_AUDIT_LOGS_INDEXES,
    WORKFLOW_INDEXES,
    WORKFLOW_VERSIONS_INDEXES,
)


def _index_doc(model):
    """``IndexModel.document`` returns the spec dict."""
    return model.document


def test_index_plan_covers_every_collection():
    assert set(INDEX_PLAN.keys()) == set(col.ALL_COLLECTIONS)


def test_workflow_indexes_present():
    expected = {
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
    }
    actual = {_index_doc(m)["name"] for m in WORKFLOW_INDEXES}
    assert actual == expected


def test_workflow_unique_and_sparse_flags():
    by_name = {_index_doc(m)["name"]: _index_doc(m) for m in WORKFLOW_INDEXES}
    assert by_name["workflow_id_unique"].get("unique") is True
    assert by_name["slug_unique"].get("unique") is True
    assert by_name["slug_unique"].get("sparse") is True


def test_workflow_versions_unique_compound():
    assert len(WORKFLOW_VERSIONS_INDEXES) == 1
    doc = _index_doc(WORKFLOW_VERSIONS_INDEXES[0])
    assert doc["name"] == "workflow_id_version_unique"
    assert doc.get("unique") is True
    keys = list(doc["key"].items())
    assert keys[0][0] == "workflow_id"
    assert keys[1][0] == "version"


def test_audit_logs_compound_descending_created_at():
    assert len(WORKFLOW_AUDIT_LOGS_INDEXES) == 1
    doc = _index_doc(WORKFLOW_AUDIT_LOGS_INDEXES[0])
    keys = list(doc["key"].items())
    assert keys[0] == ("workflow_id", 1)
    assert keys[1] == ("created_at", -1)
