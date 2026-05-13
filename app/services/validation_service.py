"""Workflow semantic validation + summary extraction.

Validates a ``WorkflowDoc`` *after* it has already passed Pydantic schema
validation. The schema layer enforces field-level shape (non-empty name,
positive position, etc.); this service enforces graph-level invariants
that span multiple nodes/edges and that mirror the React app's
``validateWorkflow`` checks.

The default config matches the React app's expectations:
- one Start node (type ``"start"``) is required
- at least one Output node (type ``"output"``) is required
- self-edges and cycles are disallowed
- unknown node types pass silently unless a known set is configured

Every issue carries a stable ``code`` so callers (UI, tests) can branch
on the rule rather than the message text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.models.common import ValidationIssue, ValidationResult, ValidationSeverity
from app.models.summary import ValidationStatus, WorkflowSummary
from app.models.workflow import WorkflowDoc, WorkflowNode


def _effective_node_type(node: WorkflowNode) -> str:
    """Return the registry key used for graph-semantic checks.

    Two storage conventions exist for the registry node-type key:

    1. Server-native: ``node.type`` holds the key (``"start"``, ``"output"``).
    2. React Flow integration: ``node.type`` is the renderer key
       (``"dynamic"``) and the actual registry key lives in ``node.data.type``
       (``NodeData`` has ``extra='allow'``).

    Prefer ``data.type`` if present and non-empty; else fall back to
    ``node.type``.
    """
    data_type = getattr(node.data, "type", None)
    if isinstance(data_type, str) and data_type:
        return data_type
    return node.type


# --------------------------- issue codes ---------------------------


class IssueCode:
    """Stable rule identifiers. Tests and UIs should match on these, not text."""

    WORKFLOW_NAME_REQUIRED = "WORKFLOW_NAME_REQUIRED"
    NODE_ID_DUPLICATE = "NODE_ID_DUPLICATE"
    NODE_NAME_DUPLICATE = "NODE_NAME_DUPLICATE"
    EDGE_ID_DUPLICATE = "EDGE_ID_DUPLICATE"
    EDGE_SOURCE_UNKNOWN = "EDGE_SOURCE_UNKNOWN"
    EDGE_TARGET_UNKNOWN = "EDGE_TARGET_UNKNOWN"
    EDGE_DUPLICATE = "EDGE_DUPLICATE"
    EDGE_SELF = "EDGE_SELF"
    START_NODE_MISSING = "START_NODE_MISSING"
    START_NODE_DUPLICATE = "START_NODE_DUPLICATE"
    START_NODE_IMPLICIT = "START_NODE_IMPLICIT"
    OUTPUT_NODE_MISSING = "OUTPUT_NODE_MISSING"
    NODE_DISCONNECTED = "NODE_DISCONNECTED"
    CYCLE_DETECTED = "CYCLE_DETECTED"
    NODE_TYPE_UNKNOWN = "NODE_TYPE_UNKNOWN"


# --------------------------- config ---------------------------


@dataclass(frozen=True)
class ValidatorConfig:
    known_node_types: frozenset[str] | None = None
    """If set, node types not in this set produce a NODE_TYPE_UNKNOWN warning."""

    start_node_type: str = "start"
    output_node_type: str = "output"
    allow_self_edges: bool = False
    allow_cycles: bool = False
    require_start_node: bool = True
    require_output_node: bool = True


DEFAULT_CONFIG = ValidatorConfig()


# --------------------------- public API ---------------------------


def validate_workflow(
    doc: WorkflowDoc,
    config: ValidatorConfig | None = None,
) -> ValidationResult:
    """Run all semantic checks. Pydantic schema is assumed to have passed."""
    cfg = config or DEFAULT_CONFIG
    issues: list[ValidationIssue] = []

    issues.extend(_check_workflow_name(doc))
    issues.extend(_check_node_id_uniqueness(doc))
    issues.extend(_check_node_name_uniqueness(doc))
    issues.extend(_check_edge_id_uniqueness(doc))
    issues.extend(_check_edge_endpoints(doc))
    issues.extend(_check_self_edges(doc, cfg))
    issues.extend(_check_duplicate_edges(doc))
    issues.extend(_check_start_node(doc, cfg))
    issues.extend(_check_output_node(doc, cfg))
    issues.extend(_check_disconnected_nodes(doc))
    issues.extend(_check_cycles(doc, cfg))
    issues.extend(_check_unknown_node_types(doc, cfg))

    valid = not any(i.severity is ValidationSeverity.ERROR for i in issues)
    return ValidationResult(valid=valid, issues=issues)


def extract_summary(
    doc: WorkflowDoc,
    validation: ValidationResult | None = None,
    config: ValidatorConfig | None = None,
) -> WorkflowSummary:
    """Compute denormalized summary fields. Re-uses ``validation`` if given,
    else runs ``validate_workflow`` to populate the validation_* fields."""
    cfg = config or DEFAULT_CONFIG
    if validation is None:
        validation = validate_workflow(doc, cfg)

    types = sorted({_effective_node_type(n) for n in doc.nodes})
    starts = sum(1 for n in doc.nodes if _effective_node_type(n) == cfg.start_node_type)
    outputs = sum(1 for n in doc.nodes if _effective_node_type(n) == cfg.output_node_type)
    errors = sum(1 for i in validation.issues if i.severity is ValidationSeverity.ERROR)
    warnings = sum(1 for i in validation.issues if i.severity is ValidationSeverity.WARNING)

    if errors > 0:
        status = ValidationStatus.ERROR
    elif warnings > 0:
        status = ValidationStatus.WARNING
    else:
        status = ValidationStatus.VALID

    return WorkflowSummary(
        node_count=len(doc.nodes),
        edge_count=len(doc.edges),
        node_types=types,
        has_start_node=starts > 0,
        has_output_node=outputs > 0,
        start_node_count=starts,
        output_node_count=outputs,
        validation_status=status,
        validation_errors=errors,
        validation_warnings=warnings,
    )


# --------------------------- individual rules ---------------------------


def _err(path: str, message: str, code: str) -> ValidationIssue:
    return ValidationIssue(
        path=path, message=message, severity=ValidationSeverity.ERROR, code=code
    )


def _warn(path: str, message: str, code: str) -> ValidationIssue:
    return ValidationIssue(
        path=path, message=message, severity=ValidationSeverity.WARNING, code=code
    )


def _check_workflow_name(doc: WorkflowDoc) -> Iterable[ValidationIssue]:
    """Pydantic enforces ``min_length=1`` on doc.name; this is defense-in-depth
    for docs constructed via ``model_construct`` (which bypasses validation)."""
    if not doc.name or not doc.name.strip():
        yield _err("name", "Workflow name is required", IssueCode.WORKFLOW_NAME_REQUIRED)


def _check_node_id_uniqueness(doc: WorkflowDoc) -> Iterable[ValidationIssue]:
    seen: dict[str, int] = {}
    for i, node in enumerate(doc.nodes):
        if node.id in seen:
            yield _err(
                f"nodes[{i}].id",
                f"Duplicate node id '{node.id}' (also at nodes[{seen[node.id]}])",
                IssueCode.NODE_ID_DUPLICATE,
            )
        else:
            seen[node.id] = i


def _check_node_name_uniqueness(doc: WorkflowDoc) -> Iterable[ValidationIssue]:
    seen: dict[str, int] = {}
    for i, node in enumerate(doc.nodes):
        name = node.data.name
        if name in seen:
            yield _err(
                f"nodes[{i}].data.name",
                f"Duplicate node name '{name}' (also at nodes[{seen[name]}])",
                IssueCode.NODE_NAME_DUPLICATE,
            )
        else:
            seen[name] = i


def _check_edge_id_uniqueness(doc: WorkflowDoc) -> Iterable[ValidationIssue]:
    seen: dict[str, int] = {}
    for i, edge in enumerate(doc.edges):
        if edge.id in seen:
            yield _err(
                f"edges[{i}].id",
                f"Duplicate edge id '{edge.id}' (also at edges[{seen[edge.id]}])",
                IssueCode.EDGE_ID_DUPLICATE,
            )
        else:
            seen[edge.id] = i


def _check_edge_endpoints(doc: WorkflowDoc) -> Iterable[ValidationIssue]:
    node_ids = {n.id for n in doc.nodes}
    for i, edge in enumerate(doc.edges):
        if edge.source not in node_ids:
            yield _err(
                f"edges[{i}].source",
                f"Edge source '{edge.source}' references unknown node",
                IssueCode.EDGE_SOURCE_UNKNOWN,
            )
        if edge.target not in node_ids:
            yield _err(
                f"edges[{i}].target",
                f"Edge target '{edge.target}' references unknown node",
                IssueCode.EDGE_TARGET_UNKNOWN,
            )


def _check_self_edges(doc: WorkflowDoc, cfg: ValidatorConfig) -> Iterable[ValidationIssue]:
    if cfg.allow_self_edges:
        return
    for i, edge in enumerate(doc.edges):
        if edge.source == edge.target:
            yield _err(
                f"edges[{i}]",
                f"Self-edge: node '{edge.source}' connects to itself",
                IssueCode.EDGE_SELF,
            )


def _check_duplicate_edges(doc: WorkflowDoc) -> Iterable[ValidationIssue]:
    """Two edges with the same (source, target, sourceHandle, targetHandle).
    Different handles between the same pair of nodes are NOT duplicates —
    they're parallel paths through different output handles."""
    seen: dict[tuple, int] = {}
    for i, edge in enumerate(doc.edges):
        key = (edge.source, edge.target, edge.sourceHandle, edge.targetHandle)
        if key in seen:
            yield _err(
                f"edges[{i}]",
                f"Duplicate edge: same endpoints/handles as edges[{seen[key]}]",
                IssueCode.EDGE_DUPLICATE,
            )
        else:
            seen[key] = i


def _check_start_node(doc: WorkflowDoc, cfg: ValidatorConfig) -> Iterable[ValidationIssue]:
    if not cfg.require_start_node:
        return
    starts = [n for n in doc.nodes if _effective_node_type(n) == cfg.start_node_type]
    if len(starts) > 1:
        yield _err(
            "nodes",
            f"{len(starts)} '{cfg.start_node_type}' nodes found (expected exactly 1)",
            IssueCode.START_NODE_DUPLICATE,
        )
        return
    if len(starts) == 1:
        return
    if not doc.nodes:
        yield _err(
            "nodes",
            f"No '{cfg.start_node_type}' node found",
            IssueCode.START_NODE_MISSING,
        )
        return
    node_ids = {n.id for n in doc.nodes}
    targets = {e.target for e in doc.edges if e.target in node_ids}
    roots = [n for n in doc.nodes if n.id not in targets]
    if len(roots) == 1:
        yield _warn(
            "nodes",
            (
                f"No '{cfg.start_node_type}' node; using '{roots[0].id}' as implicit "
                "start. Add an explicit Start node."
            ),
            IssueCode.START_NODE_IMPLICIT,
        )
    else:
        yield _err(
            "nodes",
            (
                f"No '{cfg.start_node_type}' node and the graph has {len(roots)} "
                "root node(s) (expected exactly 1)"
            ),
            IssueCode.START_NODE_MISSING,
        )


def _check_output_node(doc: WorkflowDoc, cfg: ValidatorConfig) -> Iterable[ValidationIssue]:
    """Missing Output is a WARNING, not an error: drafts should be saveable
    before the user has wired the terminal node. Set ``require_output_node``
    to False on the config to silence the warning entirely."""
    if not cfg.require_output_node:
        return
    outputs = [n for n in doc.nodes if _effective_node_type(n) == cfg.output_node_type]
    if not outputs:
        yield _warn(
            "nodes",
            f"No '{cfg.output_node_type}' node found",
            IssueCode.OUTPUT_NODE_MISSING,
        )


def _check_disconnected_nodes(doc: WorkflowDoc) -> Iterable[ValidationIssue]:
    """Warn-only. A single-node workflow is not 'disconnected'."""
    if len(doc.nodes) <= 1:
        return
    node_ids = {n.id for n in doc.nodes}
    connected: set[str] = set()
    for e in doc.edges:
        if e.source in node_ids:
            connected.add(e.source)
        if e.target in node_ids:
            connected.add(e.target)
    for i, node in enumerate(doc.nodes):
        if node.id not in connected:
            yield _warn(
                f"nodes[{i}]",
                f"Disconnected node '{node.data.name}' has no edges",
                IssueCode.NODE_DISCONNECTED,
            )


def _check_cycles(doc: WorkflowDoc, cfg: ValidatorConfig) -> Iterable[ValidationIssue]:
    if cfg.allow_cycles or not doc.nodes:
        return
    cycle = _find_cycle(doc)
    if cycle:
        yield _err(
            "edges",
            f"Cycle detected involving nodes: {' -> '.join(cycle)}",
            IssueCode.CYCLE_DETECTED,
        )


def _check_unknown_node_types(
    doc: WorkflowDoc, cfg: ValidatorConfig
) -> Iterable[ValidationIssue]:
    if cfg.known_node_types is None:
        return
    for i, node in enumerate(doc.nodes):
        effective = _effective_node_type(node)
        if effective not in cfg.known_node_types:
            yield _warn(
                f"nodes[{i}].type",
                f"Unknown node type {effective!r}",
                IssueCode.NODE_TYPE_UNKNOWN,
            )


# --------------------------- cycle detection helper ---------------------------


def _find_cycle(doc: WorkflowDoc) -> list[str] | None:
    """Iterative DFS that returns the node ids on a back edge, or None.

    Iterative because Python's default recursion limit (1000) is shallow for
    deep linear chains; iteration scales to whatever the OS thread stack
    permits.
    """
    adj: dict[str, list[str]] = {n.id: [] for n in doc.nodes}
    node_ids = set(adj)
    for e in doc.edges:
        if e.source in node_ids and e.target in node_ids:
            adj[e.source].append(e.target)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in adj}

    for start in adj:
        if color[start] is not WHITE:
            continue
        # stack frames: (node, iterator over neighbors, path-from-root)
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = [start]
        on_path: set[str] = {start}
        color[start] = GRAY
        while stack:
            node, idx = stack[-1]
            if idx >= len(adj[node]):
                color[node] = BLACK
                on_path.discard(node)
                path.pop()
                stack.pop()
                continue
            neighbor = adj[node][idx]
            stack[-1] = (node, idx + 1)
            if color[neighbor] is BLACK:
                continue
            if color[neighbor] is GRAY and neighbor in on_path:
                cycle_start = path.index(neighbor)
                return path[cycle_start:] + [neighbor]
            color[neighbor] = GRAY
            on_path.add(neighbor)
            path.append(neighbor)
            stack.append((neighbor, 0))
    return None
