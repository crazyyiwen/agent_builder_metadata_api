"""ImportExportService — JSON in/out for workflow definitions.

Imports go through ``WorkflowService`` so they get the same validation,
versioning, and audit trail as native API saves. Exports read from the
workflow + version repos.

Accepted import payload shapes:
- bare ``WorkflowDoc`` JSON: ``{id, name, nodes, edges}``
- ExportResponse-shaped envelope: ``{workflow_id, version, exported_at, doc, ...}``
- create-style envelope: ``{name, description?, tags?, doc}``
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.repos.version_repo import WorkflowVersionRepository
from app.errors import NotFoundError
from app.models.requests import CreateWorkflowRequest, UpdateWorkflowRequest
from app.models.responses import ExportResponse, ImportResponse
from app.models.workflow import WorkflowDoc
from app.services.workflow_service import WorkflowService


def _extract_doc_and_envelope(payload: dict[str, Any]) -> tuple[WorkflowDoc, dict[str, Any]]:
    """Return (parsed doc, envelope-level metadata)."""
    if isinstance(payload.get("doc"), dict):
        return WorkflowDoc.model_validate(payload["doc"]), {
            k: v for k, v in payload.items() if k != "doc"
        }
    # Treat the whole payload as a bare WorkflowDoc.
    return WorkflowDoc.model_validate(payload), {}


class ImportExportService:
    def __init__(
        self,
        workflow_service: WorkflowService,
        version_repo: WorkflowVersionRepository,
    ) -> None:
        self._workflows = workflow_service
        self._versions = version_repo

    async def import_workflow(
        self,
        payload: dict[str, Any],
        *,
        replace_workflow_id: str | None = None,
        actor_id: str | None = None,
    ) -> ImportResponse:
        doc, envelope = _extract_doc_and_envelope(payload)

        if replace_workflow_id is not None:
            request = UpdateWorkflowRequest(
                doc=doc,
                change_note=envelope.get("change_note") or "Imported",
            )
            response = await self._workflows.update_workflow(
                replace_workflow_id, request, actor_id=actor_id
            )
            validation = self._workflows.validate_doc(response.doc)
            return ImportResponse(
                workflow_id=response.meta.workflow_id,
                created=False,
                version=response.meta.current_version,
                validation=validation,
            )

        request = CreateWorkflowRequest(
            name=envelope.get("name") or doc.name,
            description=envelope.get("description"),
            tags=list(envelope.get("tags") or []),
            category=envelope.get("category"),
            doc=doc,
            change_note=envelope.get("change_note") or "Imported",
        )
        response = await self._workflows.create_workflow(request, actor_id=actor_id)
        validation = self._workflows.validate_doc(response.doc)
        return ImportResponse(
            workflow_id=response.meta.workflow_id,
            created=True,
            version=response.meta.current_version,
            validation=validation,
        )

    async def export_latest(self, workflow_id: str) -> ExportResponse:
        response = await self._workflows.get_workflow(workflow_id)
        return ExportResponse(
            workflow_id=workflow_id,
            version=response.meta.current_version,
            exported_at=datetime.now(timezone.utc),
            doc=response.doc,
        )

    async def export_version(
        self, workflow_id: str, version: int
    ) -> ExportResponse:
        version_response = await self._versions.get(workflow_id, version)
        if version_response is None:
            raise NotFoundError(
                f"workflow {workflow_id!r} version {version} not found"
            )
        return ExportResponse(
            workflow_id=workflow_id,
            version=version,
            exported_at=datetime.now(timezone.utc),
            doc=version_response.doc,
        )
