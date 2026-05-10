"""WorkflowService — orchestrates head + version + audit + validation.

This is the single write-orchestration point for workflows. Routes call this
service; this service composes the three repositories plus the validation
service. Repos themselves do single-collection reads/writes only.

Notes on transactions: Phase 6 uses sequential writes rather than
multi-collection transactions. A failure between writes can leave history
in an inconsistent state (head bumped but version row missing, etc.).
This is acceptable for the dev/standalone-Mongo path; a later phase will
wrap multi-write flows in ``with_transaction`` once we standardize on a
replica-set deployment.
"""

from __future__ import annotations

from typing import Any

from app.db.repos.audit_log_repo import WorkflowAuditLogRepository
from app.db.repos.version_repo import WorkflowVersionRepository
from app.db.repos.workflow_repo import WorkflowRepository, generate_workflow_id
from app.errors import (
    NotFoundError,
    OperationNotAllowedError,
    ValidationError,
)
from app.models.audit import AuditAction
from app.models.common import PaginationMeta
from app.models.meta import WorkflowMeta, WorkflowStatus
from app.models.requests import (
    CreateWorkflowRequest,
    PatchWorkflowRequest,
    UpdateWorkflowRequest,
)
from app.models.responses import WorkflowListResponse, WorkflowResponse
from app.models.workflow import NodeData, Position, WorkflowDoc, WorkflowEdge, WorkflowNode
from app.services.validation_service import (
    DEFAULT_CONFIG,
    ValidatorConfig,
    extract_summary,
    validate_workflow,
)


def _default_doc(workflow_id: str, name: str) -> WorkflowDoc:
    """Server-side ``createEmptyWorkflow``: Start → Output skeleton that
    satisfies the default validator (one Start, one Output, connected)."""
    return WorkflowDoc(
        id=workflow_id,
        name=name,
        nodes=[
            WorkflowNode(
                id="n_start",
                type="start",
                position=Position(x=240, y=200),
                data=NodeData(name="Start", config={}),
            ),
            WorkflowNode(
                id="n_output",
                type="output",
                position=Position(x=600, y=200),
                data=NodeData(name="Output", config={}),
            ),
        ],
        edges=[
            WorkflowEdge(
                id="e_start_to_output",
                source="n_start",
                target="n_output",
            ),
        ],
    )


class WorkflowService:
    def __init__(
        self,
        workflow_repo: WorkflowRepository,
        version_repo: WorkflowVersionRepository,
        audit_repo: WorkflowAuditLogRepository,
        *,
        validator_config: ValidatorConfig | None = None,
        enforce_validation: bool = True,
        allow_hard_delete: bool = False,
    ) -> None:
        self._workflows = workflow_repo
        self._versions = version_repo
        self._audits = audit_repo
        self._validator_config = validator_config or DEFAULT_CONFIG
        self._enforce_validation = enforce_validation
        self._allow_hard_delete = allow_hard_delete

    # ---------- read ----------

    async def get_workflow(self, workflow_id: str) -> WorkflowResponse:
        response = await self._workflows.get(workflow_id)
        if response is None:
            raise NotFoundError(f"workflow {workflow_id!r} not found")
        return response

    async def list_workflows(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        owner_id: str | None = None,
        status: WorkflowStatus | str | None = None,
        tag: str | None = None,
        category: str | None = None,
        node_type: str | None = None,
        name_contains: str | None = None,
        validation_status: str | None = None,
        include_archived: bool = False,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> WorkflowListResponse:
        limit = page_size
        offset = (page - 1) * page_size
        items, total = await self._workflows.list(
            limit=limit,
            offset=offset,
            owner_id=owner_id,
            status=status,
            tag=tag,
            category=category,
            node_type=node_type,
            name_contains=name_contains,
            validation_status=validation_status,
            include_archived=include_archived,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        has_more = offset + len(items) < total
        return WorkflowListResponse(
            items=items,
            pagination=PaginationMeta(
                total=total, page=page, page_size=page_size, has_more=has_more
            ),
        )

    # ---------- create ----------

    async def create_workflow(
        self,
        request: CreateWorkflowRequest,
        *,
        actor_id: str | None = None,
    ) -> WorkflowResponse:
        workflow_id = generate_workflow_id()
        if request.doc is None:
            doc = _default_doc(workflow_id, request.name)
        else:
            doc = request.doc.model_copy(update={"id": workflow_id})

        validation = validate_workflow(doc, self._validator_config)
        summary = extract_summary(doc, validation, self._validator_config)
        if self._enforce_validation and not validation.valid:
            raise ValidationError(
                f"Workflow validation failed with {sum(1 for i in validation.issues if i.severity.value == 'error')} error(s)",
                issues=validation.issues,
            )

        meta = await self._workflows.create(
            name=request.name,
            doc=doc,
            summary=summary,
            description=request.description,
            tags=request.tags,
            category=request.category,
            owner_id=actor_id,
            workflow_id=workflow_id,
        )
        await self._versions.create(
            workflow_id=workflow_id,
            version=1,
            doc=doc,
            change_note=request.change_note or "initial",
            validation=validation,
            summary=summary,
            created_by=actor_id,
        )
        await self._audits.write(
            workflow_id=workflow_id,
            action=AuditAction.CREATED,
            actor_id=actor_id,
            version=1,
            details={
                "name": request.name,
                "tags": request.tags,
                "category": request.category,
            },
        )
        return WorkflowResponse(meta=meta, doc=doc)

    # ---------- update (full doc → bumps version) ----------

    async def update_workflow(
        self,
        workflow_id: str,
        request: UpdateWorkflowRequest,
        *,
        expected_version: int | None = None,
        actor_id: str | None = None,
        action: AuditAction = AuditAction.UPDATED,
        extra_audit_details: dict[str, Any] | None = None,
    ) -> WorkflowResponse:
        existing = await self._workflows.get_meta(workflow_id)
        if existing is None:
            raise NotFoundError(f"workflow {workflow_id!r} not found")
        if expected_version is None:
            expected_version = existing.current_version

        doc = request.doc.model_copy(update={"id": workflow_id})
        validation = validate_workflow(doc, self._validator_config)
        summary = extract_summary(doc, validation, self._validator_config)
        if self._enforce_validation and not validation.valid:
            raise ValidationError(
                f"Workflow validation failed with {sum(1 for i in validation.issues if i.severity.value == 'error')} error(s)",
                issues=validation.issues,
            )

        new_version = expected_version + 1
        meta = await self._workflows.update_head(
            workflow_id=workflow_id,
            doc=doc,
            summary=summary,
            new_version=new_version,
            expected_current_version=expected_version,
        )
        await self._versions.create(
            workflow_id=workflow_id,
            version=new_version,
            doc=doc,
            change_note=request.change_note,
            validation=validation,
            summary=summary,
            created_by=actor_id,
        )
        details: dict[str, Any] = {"change_note": request.change_note}
        if extra_audit_details:
            details.update(extra_audit_details)
        await self._audits.write(
            workflow_id=workflow_id,
            action=action,
            actor_id=actor_id,
            version=new_version,
            details=details,
        )
        return WorkflowResponse(meta=meta, doc=doc)

    # ---------- patch (metadata-only → no version bump) ----------

    async def patch_workflow(
        self,
        workflow_id: str,
        request: PatchWorkflowRequest,
        *,
        actor_id: str | None = None,
    ) -> WorkflowMeta:
        changes = request.model_dump(exclude_unset=True)
        meta = await self._workflows.patch_metadata(workflow_id, changes)
        await self._audits.write(
            workflow_id=workflow_id,
            action=AuditAction.PATCHED,
            actor_id=actor_id,
            details={"changed_fields": sorted(changes.keys())},
        )
        return meta

    # ---------- archive / restore / delete ----------

    async def archive_workflow(
        self, workflow_id: str, *, actor_id: str | None = None
    ) -> WorkflowMeta:
        meta = await self._workflows.archive(workflow_id)
        await self._audits.write(
            workflow_id=workflow_id,
            action=AuditAction.ARCHIVED,
            actor_id=actor_id,
        )
        return meta

    async def unarchive_workflow(
        self,
        workflow_id: str,
        *,
        target_status: WorkflowStatus = WorkflowStatus.DRAFT,
        actor_id: str | None = None,
    ) -> WorkflowMeta:
        meta = await self._workflows.unarchive(
            workflow_id, target_status=target_status
        )
        await self._audits.write(
            workflow_id=workflow_id,
            action=AuditAction.RESTORED,
            actor_id=actor_id,
            details={"target_status": target_status.value},
        )
        return meta

    async def soft_delete_workflow(
        self, workflow_id: str, *, actor_id: str | None = None
    ) -> WorkflowMeta:
        """Alias for ``archive_workflow``."""
        return await self.archive_workflow(workflow_id, actor_id=actor_id)

    async def restore_workflow(
        self,
        workflow_id: str,
        *,
        target_status: WorkflowStatus = WorkflowStatus.DRAFT,
        actor_id: str | None = None,
    ) -> WorkflowMeta:
        """Alias for ``unarchive_workflow``."""
        return await self.unarchive_workflow(
            workflow_id, target_status=target_status, actor_id=actor_id
        )

    async def hard_delete_workflow(
        self, workflow_id: str, *, actor_id: str | None = None
    ) -> None:
        """Permanently removes the workflow plus all its versions and audit
        rows. Requires ``allow_hard_delete=True`` at construction."""
        if not self._allow_hard_delete:
            raise OperationNotAllowedError(
                "hard delete is disabled — initialize WorkflowService with allow_hard_delete=True"
            )
        # Confirm exists for a clean 404 (and to fail before cascading work).
        if not await self._workflows.exists(workflow_id):
            raise NotFoundError(f"workflow {workflow_id!r} not found")
        # Cascade order: versions and audit logs first, then head. Reverse order
        # so a partial failure leaves the head visible (recoverable) rather than
        # leaving orphan rows pointing at a missing head.
        await self._versions.delete_all_for_workflow(workflow_id)
        await self._audits.delete_all_for_workflow(workflow_id)
        await self._workflows.hard_delete(workflow_id)

    # ---------- duplicate ----------

    async def duplicate_workflow(
        self,
        source_workflow_id: str,
        *,
        new_name: str | None = None,
        owner_id: str | None = None,
        actor_id: str | None = None,
    ) -> WorkflowResponse:
        copy_meta = await self._workflows.duplicate(
            source_workflow_id,
            new_name=new_name,
            owner_id=owner_id if owner_id is not None else actor_id,
        )
        copy_full = await self._workflows.get(copy_meta.workflow_id)
        # Re-validate for the new doc (id stamped by repo); produces the
        # validation/summary stored against the new v1.
        validation = validate_workflow(copy_full.doc, self._validator_config)
        summary = extract_summary(copy_full.doc, validation, self._validator_config)
        await self._versions.create(
            workflow_id=copy_meta.workflow_id,
            version=1,
            doc=copy_full.doc,
            change_note=f"Duplicated from {source_workflow_id}",
            validation=validation,
            summary=summary,
            created_by=actor_id,
        )
        await self._audits.write(
            workflow_id=copy_meta.workflow_id,
            action=AuditAction.DUPLICATED,
            actor_id=actor_id,
            version=1,
            details={"source_workflow_id": source_workflow_id},
        )
        return copy_full

    # ---------- stateless validation ----------

    def validate_doc(self, doc: WorkflowDoc):
        """Stateless: returns the validation report without persisting anything.
        Wraps the validation service so callers don't need to import it directly."""
        return validate_workflow(doc, self._validator_config)
