"""Schema tests for ``app.models``.

Covers:
- Round-tripping a real React-shaped WorkflowDoc.
- ``NodeData`` preserves unknown top-level fields and arbitrary config shapes.
- Strict models reject extras (incl. Mongo ``_id``).
- Metadata typing + datetime JSON serialization.
- Request models: required/optional/exclude_unset semantics.
- Pagination + validation result composition.
- Response models are JSON-serializable and never declare an ``_id`` field.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    CreateWorkflowRequest,
    ExportResponse,
    ImportResponse,
    NodeData,
    PaginationMeta,
    PatchWorkflowRequest,
    Position,
    UpdateWorkflowRequest,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
    WorkflowDoc,
    WorkflowEdge,
    WorkflowListResponse,
    WorkflowMeta,
    WorkflowNode,
    WorkflowResponse,
    WorkflowStatus,
    WorkflowVersionResponse,
    WorkflowVersionSummary,
)


# --------------------------- fixtures ---------------------------


def _sample_react_doc() -> dict:
    """Snapshot of what the React serializer emits."""
    return {
        "id": "wf_demo",
        "name": "Demo flow",
        "nodes": [
            {
                "id": "n_start",
                "type": "start",
                "position": {"x": 100.0, "y": 200.0},
                "data": {"name": "Start", "config": {}},
            },
            {
                "id": "n_llm",
                "type": "llm",
                "position": {"x": 400.5, "y": 200.0},
                "data": {
                    "name": "Summarize",
                    "config": {
                        "model": "claude-opus-4-7",
                        "temperature": 0.4,
                        "messages": [
                            {"role": "system", "content": "You summarize."},
                            {"role": "user", "content": "{{system.userQuery}}"},
                        ],
                        "outputVariables": [{"name": "summary", "type": "string"}],
                    },
                },
            },
        ],
        "edges": [
            {
                "id": "e1",
                "source": "n_start",
                "target": "n_llm",
                "sourceHandle": "out",
                "targetHandle": None,
            },
        ],
    }


def _meta() -> WorkflowMeta:
    now = datetime.now(timezone.utc)
    return WorkflowMeta(
        workflow_id="wf_x",
        name="X",
        current_version=1,
        created_at=now,
        updated_at=now,
    )


# --------------------------- workflow doc / nodes / edges ---------------------------


def test_workflow_doc_parses_real_react_payload():
    doc = WorkflowDoc.model_validate(_sample_react_doc())
    assert doc.id == "wf_demo"
    assert len(doc.nodes) == 2
    assert doc.nodes[1].data.config["temperature"] == 0.4
    assert doc.edges[0].sourceHandle == "out"
    assert doc.edges[0].targetHandle is None


def test_workflow_doc_round_trip_preserves_arbitrary_config():
    payload = _sample_react_doc()
    doc = WorkflowDoc.model_validate(payload)
    dumped = doc.model_dump(mode="json")
    assert dumped["nodes"][1]["data"]["config"] == payload["nodes"][1]["data"]["config"]
    json.dumps(dumped)  # JSON-serializable


def test_node_data_preserves_unknown_top_level_fields():
    nd = NodeData.model_validate(
        {
            "name": "MyNode",
            "config": {"foo": 1},
            "futureField": "tolerated",
            "anotherFuture": {"nested": True},
        }
    )
    dumped = nd.model_dump()
    assert dumped["futureField"] == "tolerated"
    assert dumped["anotherFuture"] == {"nested": True}


def test_node_data_config_accepts_arbitrary_shape():
    weird = {
        "scalar": 42,
        "string": "x",
        "list": [1, "two", {"three": 3}],
        "nested": {"a": {"b": {"c": [None, True, False]}}},
    }
    nd = NodeData.model_validate({"name": "X", "config": weird})
    assert nd.config == weird


def test_workflow_node_rejects_unknown_top_level_keys_including_id():
    with pytest.raises(ValidationError):
        WorkflowNode.model_validate(
            {
                "id": "n1",
                "type": "llm",
                "position": {"x": 0, "y": 0},
                "data": {"name": "x", "config": {}},
                "_id": "65f00abc...",  # never accept Mongo _id at the API boundary
            }
        )


def test_workflow_edge_optional_handles_default_to_none():
    e = WorkflowEdge.model_validate({"id": "e1", "source": "a", "target": "b"})
    assert e.sourceHandle is None
    assert e.targetHandle is None


def test_workflow_doc_default_empty_collections():
    doc = WorkflowDoc.model_validate({"id": "x", "name": "Empty"})
    assert doc.nodes == []
    assert doc.edges == []


def test_position_coerces_int_to_float():
    p = Position.model_validate({"x": 1, "y": -2})
    assert p.x == 1.0 and p.y == -2.0


def test_position_rejects_extras():
    with pytest.raises(ValidationError):
        Position.model_validate({"x": 0, "y": 0, "z": 5})


# --------------------------- metadata ---------------------------


def test_workflow_meta_defaults_to_draft():
    m = _meta()
    assert m.status is WorkflowStatus.DRAFT


def test_workflow_meta_serializes_datetimes_as_iso_strings():
    blob = _meta().model_dump(mode="json")
    assert isinstance(blob["created_at"], str)
    assert isinstance(blob["updated_at"], str)


def test_workflow_meta_rejects_objectid_field():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        WorkflowMeta.model_validate(
            {
                "workflow_id": "wf_x",
                "name": "X",
                "current_version": 1,
                "created_at": now,
                "updated_at": now,
                "_id": "65f00abc...",
            }
        )


def test_workflow_meta_rejects_invalid_workflow_id():
    now = datetime.now(timezone.utc)
    for bad in ("bad id", "_leading_underscore", "-leading-dash", "spaces in id"):
        with pytest.raises(ValidationError):
            WorkflowMeta.model_validate(
                {
                    "workflow_id": bad,
                    "name": "X",
                    "current_version": 1,
                    "created_at": now,
                    "updated_at": now,
                }
            )


def test_workflow_meta_status_accepts_all_enum_values():
    now = datetime.now(timezone.utc)
    for status in ("draft", "published", "archived"):
        m = WorkflowMeta(
            workflow_id="x",
            name="x",
            current_version=1,
            created_at=now,
            updated_at=now,
            status=status,
        )
        assert m.status.value == status


def test_workflow_meta_current_version_must_be_positive():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        WorkflowMeta.model_validate(
            {
                "workflow_id": "wf_x",
                "name": "X",
                "current_version": 0,
                "created_at": now,
                "updated_at": now,
            }
        )


# --------------------------- requests ---------------------------


def test_create_workflow_request_with_doc():
    req = CreateWorkflowRequest.model_validate(
        {
            "name": "Demo",
            "doc": _sample_react_doc(),
            "tags": ["a", "b"],
        }
    )
    assert req.doc is not None
    assert len(req.doc.nodes) == 2


def test_create_workflow_request_without_doc():
    req = CreateWorkflowRequest.model_validate({"name": "Empty"})
    assert req.doc is None
    assert req.tags == []


def test_create_workflow_request_requires_name():
    with pytest.raises(ValidationError):
        CreateWorkflowRequest.model_validate({})


def test_update_workflow_request_requires_doc():
    with pytest.raises(ValidationError):
        UpdateWorkflowRequest.model_validate({})


def test_patch_workflow_request_partial_uses_exclude_unset():
    req = PatchWorkflowRequest.model_validate({"name": "Renamed"})
    assert req.model_dump(exclude_unset=True) == {"name": "Renamed"}


def test_patch_workflow_request_explicit_null_is_distinct_from_omitted():
    req = PatchWorkflowRequest.model_validate({"description": None})
    dumped = req.model_dump(exclude_unset=True)
    assert "description" in dumped
    assert dumped["description"] is None


def test_patch_workflow_request_status_enum():
    req = PatchWorkflowRequest.model_validate({"status": "published"})
    assert req.status is WorkflowStatus.PUBLISHED


# --------------------------- validation + pagination ---------------------------


def test_validation_result_composition():
    r = ValidationResult(
        valid=False,
        issues=[
            ValidationIssue(path="nodes[0]", message="missing Start", code="START_REQUIRED"),
            ValidationIssue(
                path="edges[3]",
                message="dangling source",
                severity=ValidationSeverity.WARNING,
            ),
        ],
    )
    assert r.valid is False
    assert len(r.issues) == 2
    assert r.issues[1].severity is ValidationSeverity.WARNING


def test_validation_result_default_valid_no_issues():
    r = ValidationResult(valid=True)
    assert r.issues == []


def test_pagination_meta_bounds():
    p = PaginationMeta(total=100, page=3, page_size=20, has_more=True)
    assert p.has_more is True
    with pytest.raises(ValidationError):
        PaginationMeta(total=-1, page=1, page_size=20, has_more=False)
    with pytest.raises(ValidationError):
        PaginationMeta(total=0, page=1, page_size=0, has_more=False)
    with pytest.raises(ValidationError):
        PaginationMeta(total=0, page=0, page_size=20, has_more=False)


# --------------------------- responses ---------------------------


def test_workflow_response_serializes_to_json():
    resp = WorkflowResponse(meta=_meta(), doc=WorkflowDoc.model_validate(_sample_react_doc()))
    blob = resp.model_dump(mode="json")
    json.dumps(blob)
    assert "meta" in blob and "doc" in blob


def test_workflow_list_response():
    resp = WorkflowListResponse(
        items=[_meta(), _meta()],
        pagination=PaginationMeta(total=2, page=1, page_size=20, has_more=False),
    )
    assert len(resp.items) == 2
    assert resp.pagination.has_more is False


def test_workflow_version_summary_omits_doc():
    s = WorkflowVersionSummary(
        workflow_id="wf_x",
        version=2,
        created_at=datetime.now(timezone.utc),
    )
    assert "doc" not in s.model_dump()


def test_workflow_version_response_contains_doc():
    r = WorkflowVersionResponse(
        workflow_id="wf_x",
        version=2,
        doc=WorkflowDoc.model_validate(_sample_react_doc()),
        created_at=datetime.now(timezone.utc),
    )
    assert r.doc.id == "wf_demo"


def test_import_response():
    r = ImportResponse(
        workflow_id="wf_x",
        created=True,
        version=1,
        validation=ValidationResult(valid=True),
    )
    assert r.created is True


def test_export_response_self_contained_and_serializable():
    r = ExportResponse(
        workflow_id="wf_x",
        version=3,
        exported_at=datetime.now(timezone.utc),
        doc=WorkflowDoc.model_validate(_sample_react_doc()),
    )
    blob = r.model_dump(mode="json")
    json.dumps(blob)
    assert blob["doc"]["id"] == "wf_demo"


def test_no_objectid_field_in_any_response_model():
    """Defensive smoke: no response model declares a Mongo ``_id`` field."""
    for cls in (
        WorkflowResponse,
        WorkflowListResponse,
        WorkflowMeta,
        WorkflowVersionResponse,
        WorkflowVersionSummary,
        ImportResponse,
        ExportResponse,
    ):
        assert "_id" not in cls.model_fields
        assert "id_" not in cls.model_fields
