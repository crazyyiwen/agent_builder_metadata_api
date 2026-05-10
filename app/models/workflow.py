"""Workflow definition: doc, node, edge, node data.

Mirrors the JSON shape that the React Workflow Builder emits via
``serializeWorkflow``. Field names match the React layer exactly so the
React app can POST its own JSON without an intermediate rename layer.

Design notes:
- ``NodeData`` uses ``extra='allow'`` so node-type-specific or future React
  fields are preserved through validate → dump round-trips. The ``config``
  dict is intentionally untyped since each node type has its own config
  shape declared in the React node registry, which this server
  deliberately does not duplicate.
- Other models use ``extra='forbid'`` to catch typos and reject any leaked
  Mongo ``_id`` at the API boundary.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Position(BaseModel):
    """React Flow node position."""

    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class NodeData(BaseModel):
    """Per-node payload. ``config`` is intentionally permissive — every node
    type has its own config shape declared in the React node registry."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(
        min_length=1,
        max_length=200,
        description="Node display name (unique within a workflow)",
    )
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowNode(BaseModel):
    """A single node in a workflow graph."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    type: str = Field(
        min_length=1,
        max_length=64,
        description="Node type key from the React registry (e.g. 'llm', 'agent', 'http')",
    )
    position: Position
    data: NodeData


class WorkflowEdge(BaseModel):
    """A directed edge connecting two nodes via optional named handles.

    Field names ``sourceHandle`` and ``targetHandle`` are camelCase to match
    the React Flow JSON exactly — no rename layer between the React
    serializer and the Mongo document.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=128, description="Source node id")
    target: str = Field(min_length=1, max_length=128, description="Target node id")
    sourceHandle: str | None = Field(
        default=None,
        max_length=128,
        description="Named source handle, if any",
    )
    targetHandle: str | None = Field(
        default=None,
        max_length=128,
        description="Named target handle, if any",
    )


class WorkflowDoc(BaseModel):
    """Complete workflow definition."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        min_length=1,
        max_length=64,
        description="Workflow identifier (matches WorkflowMeta.workflow_id)",
    )
    name: str = Field(min_length=1, max_length=200)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
