"""WorkflowVersionService — version-history queries + rollback.

Reads come straight from the version repo. Rollback is delegated to
``WorkflowService.update_workflow`` (with action=ROLLED_BACK), so rollback
gets the same validation, audit, and OCC behavior as a normal save.
"""

from __future__ import annotations

from app.db.repos.version_repo import WorkflowVersionRepository
from app.errors import NotFoundError
from app.models.audit import AuditAction
from app.models.common import PaginationMeta
from app.models.requests import UpdateWorkflowRequest
from app.models.responses import WorkflowResponse, WorkflowVersionResponse
from app.services.workflow_service import WorkflowService
from pydantic import BaseModel, ConfigDict

from app.models.responses import WorkflowVersionSummary


class VersionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkflowVersionSummary]
    pagination: PaginationMeta


class WorkflowVersionService:
    def __init__(
        self,
        version_repo: WorkflowVersionRepository,
        workflow_service: WorkflowService,
    ) -> None:
        self._versions = version_repo
        self._workflows = workflow_service

    async def list_versions(
        self,
        workflow_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> VersionListResponse:
        limit = page_size
        offset = (page - 1) * page_size
        items, total = await self._versions.list_summaries(
            workflow_id, limit=limit, offset=offset
        )
        has_more = offset + len(items) < total
        return VersionListResponse(
            items=items,
            pagination=PaginationMeta(
                total=total, page=page, page_size=page_size, has_more=has_more
            ),
        )

    async def get_version(
        self, workflow_id: str, version: int
    ) -> WorkflowVersionResponse:
        response = await self._versions.get(workflow_id, version)
        if response is None:
            raise NotFoundError(
                f"workflow {workflow_id!r} version {version} not found"
            )
        return response

    async def create_snapshot(
        self,
        workflow_id: str,
        *,
        change_note: str | None = None,
        actor_id: str | None = None,
    ) -> WorkflowVersionResponse:
        """Tag the current head doc as a new version row.

        Implementation: re-saves the current head verbatim through
        ``WorkflowService.update_workflow``, which bumps ``current_version``
        and writes a new version row. The doc is unchanged. The audit log
        records ``UPDATED`` with the supplied change note (defaulting to
        ``"snapshot"``).
        """
        current = await self._workflows.get_workflow(workflow_id)
        request = UpdateWorkflowRequest(
            doc=current.doc,
            change_note=change_note or "snapshot",
        )
        result = await self._workflows.update_workflow(
            workflow_id,
            request,
            actor_id=actor_id,
        )
        return await self.get_version(workflow_id, result.meta.current_version)

    async def rollback_to(
        self,
        workflow_id: str,
        target_version: int,
        *,
        change_note: str | None = None,
        actor_id: str | None = None,
    ) -> WorkflowResponse:
        """Promote the doc from ``target_version`` back to head.

        Implemented as: fetch old doc → call ``WorkflowService.update_workflow``
        with that doc + ``action=ROLLED_BACK``. The new head ends up at
        ``current_version + 1`` (history is append-only — we never overwrite
        a prior version row).
        """
        old_doc = await self._versions.get_doc_for_rollback(workflow_id, target_version)
        if old_doc is None:
            raise NotFoundError(
                f"workflow {workflow_id!r} version {target_version} not found"
            )
        request = UpdateWorkflowRequest(
            doc=old_doc,
            change_note=change_note or f"Rolled back to v{target_version}",
        )
        return await self._workflows.update_workflow(
            workflow_id,
            request,
            actor_id=actor_id,
            action=AuditAction.ROLLED_BACK,
            extra_audit_details={"target_version": target_version},
        )
