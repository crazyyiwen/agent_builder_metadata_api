"""Collection name constants. Single source of truth for every Mongo namespace."""

WORKFLOWS = "agent_workflows_metadata"
WORKFLOW_VERSIONS = "workflow_versions"
WORKFLOW_AUDIT_LOGS = "workflow_audit_logs"

ALL_COLLECTIONS: tuple[str, ...] = (
    WORKFLOWS,
    WORKFLOW_VERSIONS,
    WORKFLOW_AUDIT_LOGS,
)
