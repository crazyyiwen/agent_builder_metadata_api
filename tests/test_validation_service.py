"""Tests for ``app.services.validation_service``.

Covers every rule documented in the service module. Tests isolate one rule
at a time by relaxing other requirements via ``ValidatorConfig`` so failures
don't cascade into unrelated checks.
"""

from __future__ import annotations

import pytest

from app.models.common import ValidationSeverity
from app.models.summary import ValidationStatus
from app.models.workflow import WorkflowDoc
from app.services.validation_service import (
    DEFAULT_CONFIG,
    IssueCode,
    ValidatorConfig,
    extract_summary,
    validate_workflow,
)


# --------------------------- doc builders ---------------------------


def _node(node_id: str, *, type: str = "llm", name: str | None = None) -> dict:
    return {
        "id": node_id,
        "type": type,
        "position": {"x": 0, "y": 0},
        "data": {"name": name or node_id, "config": {}},
    }


def _edge(
    edge_id: str,
    source: str,
    target: str,
    *,
    source_handle: str | None = None,
    target_handle: str | None = None,
) -> dict:
    return {
        "id": edge_id,
        "source": source,
        "target": target,
        "sourceHandle": source_handle,
        "targetHandle": target_handle,
    }


def _doc(nodes: list[dict] | None = None, edges: list[dict] | None = None, name: str = "Test") -> WorkflowDoc:
    return WorkflowDoc.model_validate(
        {"id": "wf_test", "name": name, "nodes": nodes or [], "edges": edges or []}
    )


def _codes(result) -> list[str]:
    return [i.code for i in result.issues]


# --------------------------- valid baseline ---------------------------


def test_valid_minimal_workflow_has_no_issues():
    doc = _doc(
        nodes=[
            _node("start", type="start", name="Start"),
            _node("out", type="output", name="Output"),
        ],
        edges=[_edge("e1", "start", "out")],
    )
    result = validate_workflow(doc)
    assert result.valid is True
    assert result.issues == []


# --------------------------- workflow name ---------------------------


def test_workflow_missing_name_via_model_construct():
    """Pydantic blocks empty names normally; this exercises the defensive
    rule for docs constructed via ``model_construct``."""
    doc = WorkflowDoc.model_construct(id="x", name="", nodes=[], edges=[])
    result = validate_workflow(doc, ValidatorConfig(require_start_node=False, require_output_node=False))
    assert IssueCode.WORKFLOW_NAME_REQUIRED in _codes(result)


# --------------------------- node id / name uniqueness ---------------------------


def test_duplicate_node_ids_is_error():
    doc = _doc(
        nodes=[
            _node("dup", type="start", name="A"),
            _node("dup", type="output", name="B"),
        ],
        edges=[],
    )
    result = validate_workflow(doc)
    codes = _codes(result)
    assert IssueCode.NODE_ID_DUPLICATE in codes
    assert result.valid is False


def test_duplicate_node_names_is_error():
    doc = _doc(
        nodes=[
            _node("a", type="start", name="Same"),
            _node("b", type="output", name="Same"),
        ],
        edges=[_edge("e1", "a", "b")],
    )
    result = validate_workflow(doc)
    codes = _codes(result)
    assert IssueCode.NODE_NAME_DUPLICATE in codes
    dup_issues = [i for i in result.issues if i.code == IssueCode.NODE_NAME_DUPLICATE]
    assert dup_issues[0].severity is ValidationSeverity.ERROR


# --------------------------- edge ids / endpoints ---------------------------


def test_duplicate_edge_ids_is_error():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("o", type="output", name="O"),
        ],
        edges=[
            _edge("e1", "s", "o"),
            _edge("e1", "s", "o", source_handle="alt"),  # different shape, same id
        ],
    )
    result = validate_workflow(doc)
    assert IssueCode.EDGE_ID_DUPLICATE in _codes(result)


def test_invalid_edge_source_is_error():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("bad", "ghost", "o")],
    )
    result = validate_workflow(doc)
    assert IssueCode.EDGE_SOURCE_UNKNOWN in _codes(result)
    assert result.valid is False


def test_invalid_edge_target_is_error():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("bad", "s", "ghost")],
    )
    result = validate_workflow(doc)
    assert IssueCode.EDGE_TARGET_UNKNOWN in _codes(result)


def test_duplicate_exact_edge_is_error():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("o", type="output", name="O"),
        ],
        edges=[
            _edge("e1", "s", "o", source_handle="out"),
            _edge("e2", "s", "o", source_handle="out"),
        ],
    )
    result = validate_workflow(doc)
    assert IssueCode.EDGE_DUPLICATE in _codes(result)


def test_parallel_edges_via_different_handles_are_not_duplicates():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("o", type="output", name="O"),
        ],
        edges=[
            _edge("e1", "s", "o", source_handle="ok"),
            _edge("e2", "s", "o", source_handle="error"),
        ],
    )
    result = validate_workflow(doc)
    assert IssueCode.EDGE_DUPLICATE not in _codes(result)


# --------------------------- self edges ---------------------------


def test_self_edge_disallowed_by_default():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="llm", name="A"),
            _node("o", type="output", name="O"),
        ],
        edges=[
            _edge("e1", "s", "a"),
            _edge("e2", "a", "a"),
            _edge("e3", "a", "o"),
        ],
    )
    result = validate_workflow(doc, ValidatorConfig(allow_cycles=True))
    assert IssueCode.EDGE_SELF in _codes(result)


def test_self_edge_allowed_when_configured():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="llm", name="A"),
            _node("o", type="output", name="O"),
        ],
        edges=[
            _edge("e1", "s", "a"),
            _edge("e2", "a", "a"),
            _edge("e3", "a", "o"),
        ],
    )
    cfg = ValidatorConfig(allow_self_edges=True, allow_cycles=True)
    result = validate_workflow(doc, cfg)
    assert IssueCode.EDGE_SELF not in _codes(result)


# --------------------------- start node ---------------------------


def test_no_start_node_with_single_root_is_warning():
    doc = _doc(
        nodes=[
            _node("a", type="llm", name="A"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("e1", "a", "o")],
    )
    result = validate_workflow(doc)
    codes = _codes(result)
    assert IssueCode.START_NODE_IMPLICIT in codes
    implicit = [i for i in result.issues if i.code == IssueCode.START_NODE_IMPLICIT][0]
    assert implicit.severity is ValidationSeverity.WARNING
    assert IssueCode.START_NODE_MISSING not in codes


def test_no_start_node_with_multiple_roots_is_error():
    doc = _doc(
        nodes=[
            _node("a", type="llm", name="A"),
            _node("b", type="llm", name="B"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("e1", "a", "o"), _edge("e2", "b", "o")],
    )
    result = validate_workflow(doc)
    assert IssueCode.START_NODE_MISSING in _codes(result)
    assert result.valid is False


def test_multiple_start_nodes_is_error():
    doc = _doc(
        nodes=[
            _node("s1", type="start", name="S1"),
            _node("s2", type="start", name="S2"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("e1", "s1", "o"), _edge("e2", "s2", "o")],
    )
    result = validate_workflow(doc)
    assert IssueCode.START_NODE_DUPLICATE in _codes(result)


# --------------------------- output node ---------------------------


def test_missing_output_node_is_warning_not_error():
    """Missing Output is a warning so drafts can save before the user wires
    the terminal node. Set ``require_output_node=False`` to silence entirely."""
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="llm", name="A"),
        ],
        edges=[_edge("e1", "s", "a")],
    )
    result = validate_workflow(doc)
    issues = [i for i in result.issues if i.code == IssueCode.OUTPUT_NODE_MISSING]
    assert len(issues) == 1
    assert issues[0].severity is ValidationSeverity.WARNING
    # Warning alone doesn't fail validation.
    assert result.valid is True


def test_missing_output_node_skipped_when_not_required():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="llm", name="A"),
        ],
        edges=[_edge("e1", "s", "a")],
    )
    cfg = ValidatorConfig(require_output_node=False)
    result = validate_workflow(doc, cfg)
    assert IssueCode.OUTPUT_NODE_MISSING not in _codes(result)


# --------------------------- disconnected nodes ---------------------------


def test_disconnected_node_is_warning():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("o", type="output", name="O"),
            _node("orphan", type="llm", name="Orphan"),
        ],
        edges=[_edge("e1", "s", "o")],
    )
    result = validate_workflow(doc)
    disconnected = [i for i in result.issues if i.code == IssueCode.NODE_DISCONNECTED]
    assert len(disconnected) == 1
    assert disconnected[0].severity is ValidationSeverity.WARNING
    # disconnected alone doesn't make the workflow invalid
    assert result.valid is True


def test_single_node_workflow_is_not_disconnected():
    doc = _doc(nodes=[_node("only", type="start", name="Only")])
    cfg = ValidatorConfig(require_output_node=False)
    result = validate_workflow(doc, cfg)
    assert IssueCode.NODE_DISCONNECTED not in _codes(result)


# --------------------------- cycles ---------------------------


def test_cycle_detected_by_default():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="llm", name="A"),
            _node("b", type="llm", name="B"),
            _node("o", type="output", name="O"),
        ],
        edges=[
            _edge("e1", "s", "a"),
            _edge("e2", "a", "b"),
            _edge("e3", "b", "a"),
            _edge("e4", "a", "o"),
        ],
    )
    result = validate_workflow(doc)
    assert IssueCode.CYCLE_DETECTED in _codes(result)
    assert result.valid is False


def test_cycle_allowed_when_configured():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="llm", name="A"),
            _node("b", type="llm", name="B"),
            _node("o", type="output", name="O"),
        ],
        edges=[
            _edge("e1", "s", "a"),
            _edge("e2", "a", "b"),
            _edge("e3", "b", "a"),
            _edge("e4", "a", "o"),
        ],
    )
    cfg = ValidatorConfig(allow_cycles=True)
    result = validate_workflow(doc, cfg)
    assert IssueCode.CYCLE_DETECTED not in _codes(result)


def test_long_chain_is_not_a_cycle():
    """Ensures iterative DFS doesn't false-positive on deep linear chains."""
    nodes = [_node("s", type="start", name="S")]
    edges = []
    prev = "s"
    for i in range(50):
        nid = f"n{i}"
        nodes.append(_node(nid, type="llm", name=f"N{i}"))
        edges.append(_edge(f"e{i}", prev, nid))
        prev = nid
    nodes.append(_node("o", type="output", name="O"))
    edges.append(_edge("eo", prev, "o"))
    doc = _doc(nodes=nodes, edges=edges)
    result = validate_workflow(doc)
    assert IssueCode.CYCLE_DETECTED not in _codes(result)


def test_diamond_is_not_a_cycle():
    """A → B, A → C, B → D, C → D — diamond, no cycle."""
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="llm", name="A"),
            _node("b", type="llm", name="B"),
            _node("c", type="llm", name="C"),
            _node("d", type="llm", name="D"),
            _node("o", type="output", name="O"),
        ],
        edges=[
            _edge("e0", "s", "a"),
            _edge("e1", "a", "b"),
            _edge("e2", "a", "c"),
            _edge("e3", "b", "d"),
            _edge("e4", "c", "d"),
            _edge("e5", "d", "o"),
        ],
    )
    result = validate_workflow(doc)
    assert IssueCode.CYCLE_DETECTED not in _codes(result)


# --------------------------- unknown node types ---------------------------


def test_unknown_node_type_is_warning_when_known_set_configured():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="custom_thing", name="A"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("e1", "s", "a"), _edge("e2", "a", "o")],
    )
    cfg = ValidatorConfig(known_node_types=frozenset({"start", "llm", "output"}))
    result = validate_workflow(doc, cfg)
    unknowns = [i for i in result.issues if i.code == IssueCode.NODE_TYPE_UNKNOWN]
    assert len(unknowns) == 1
    assert unknowns[0].severity is ValidationSeverity.WARNING
    # warning alone doesn't fail validation
    assert result.valid is True


def test_unknown_node_type_silent_when_no_known_set():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="custom_thing", name="A"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("e1", "s", "a"), _edge("e2", "a", "o")],
    )
    result = validate_workflow(doc)
    assert IssueCode.NODE_TYPE_UNKNOWN not in _codes(result)


# --------------------------- summary extraction ---------------------------


def test_summary_counts_and_types():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("a", type="llm", name="A"),
            _node("b", type="llm", name="B"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("e1", "s", "a"), _edge("e2", "a", "b"), _edge("e3", "b", "o")],
    )
    s = extract_summary(doc)
    assert s.node_count == 4
    assert s.edge_count == 3
    assert s.node_types == ["llm", "output", "start"]
    assert s.has_start_node is True
    assert s.has_output_node is True
    assert s.start_node_count == 1
    assert s.output_node_count == 1


def test_summary_status_valid():
    doc = _doc(
        nodes=[_node("s", type="start", name="S"), _node("o", type="output", name="O")],
        edges=[_edge("e1", "s", "o")],
    )
    s = extract_summary(doc)
    assert s.validation_status is ValidationStatus.VALID
    assert s.validation_errors == 0
    assert s.validation_warnings == 0


def test_summary_status_warning_only():
    """No-Start fallback emits a single warning; status should be warning."""
    doc = _doc(
        nodes=[_node("a", type="llm", name="A"), _node("o", type="output", name="O")],
        edges=[_edge("e1", "a", "o")],
    )
    s = extract_summary(doc)
    assert s.validation_status is ValidationStatus.WARNING
    assert s.validation_errors == 0
    assert s.validation_warnings >= 1


def test_summary_status_error():
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("bad", "ghost", "o")],
    )
    s = extract_summary(doc)
    assert s.validation_status is ValidationStatus.ERROR
    assert s.validation_errors >= 1


def test_summary_reuses_provided_validation():
    """Passing pre-computed validation skips a second pass."""
    doc = _doc(
        nodes=[_node("s", type="start", name="S"), _node("o", type="output", name="O")],
        edges=[_edge("e1", "s", "o")],
    )
    v = validate_workflow(doc)
    s = extract_summary(doc, validation=v)
    assert s.validation_status is ValidationStatus.VALID


# --------------------------- defaults sanity ---------------------------


def test_validator_recognizes_start_via_data_type_react_convention():
    """The React Workflow Builder uses ``node.type='dynamic'`` for every
    node and stores the registry key in ``node.data.type``. Validator must
    detect Start/Output via the effective-type fallback."""
    doc = WorkflowDoc.model_validate(
        {
            "id": "wf_react",
            "name": "From React",
            "nodes": [
                {
                    "id": "n1",
                    "type": "dynamic",
                    "position": {"x": 0, "y": 0},
                    "data": {"type": "start", "name": "S", "config": {}},
                },
                {
                    "id": "n2",
                    "type": "dynamic",
                    "position": {"x": 200, "y": 0},
                    "data": {"type": "output", "name": "O", "config": {}},
                },
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2",
                 "sourceHandle": None, "targetHandle": None}
            ],
        }
    )
    result = validate_workflow(doc)
    # No START_NODE_MISSING / OUTPUT_NODE_MISSING — both detected via data.type.
    assert IssueCode.START_NODE_MISSING not in _codes(result)
    assert IssueCode.START_NODE_IMPLICIT not in _codes(result)
    assert IssueCode.OUTPUT_NODE_MISSING not in _codes(result)
    assert result.valid is True


def test_validator_falls_back_to_node_type_when_data_type_missing():
    """Pure server-shape docs (no data.type) still detect Start via node.type."""
    doc = _doc(
        nodes=[
            _node("s", type="start", name="S"),
            _node("o", type="output", name="O"),
        ],
        edges=[_edge("e1", "s", "o")],
    )
    result = validate_workflow(doc)
    assert result.valid is True


def test_summary_uses_effective_type():
    """``WorkflowSummary.node_types`` must reflect the registry key, not
    the renderer key (``'dynamic'``)."""
    from app.services.validation_service import extract_summary
    doc = WorkflowDoc.model_validate(
        {
            "id": "wf_react",
            "name": "X",
            "nodes": [
                {"id": "n1", "type": "dynamic", "position": {"x": 0, "y": 0},
                 "data": {"type": "start", "name": "S", "config": {}}},
                {"id": "n2", "type": "dynamic", "position": {"x": 100, "y": 0},
                 "data": {"type": "output", "name": "O", "config": {}}},
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "n2",
                       "sourceHandle": None, "targetHandle": None}],
        }
    )
    s = extract_summary(doc)
    assert s.node_types == ["output", "start"]
    assert s.has_start_node is True
    assert s.has_output_node is True
    assert s.start_node_count == 1
    assert s.output_node_count == 1


def test_default_config_matches_documented_defaults():
    assert DEFAULT_CONFIG.start_node_type == "start"
    assert DEFAULT_CONFIG.output_node_type == "output"
    assert DEFAULT_CONFIG.allow_self_edges is False
    assert DEFAULT_CONFIG.allow_cycles is False
    assert DEFAULT_CONFIG.require_start_node is True
    assert DEFAULT_CONFIG.require_output_node is True
    assert DEFAULT_CONFIG.known_node_types is None
