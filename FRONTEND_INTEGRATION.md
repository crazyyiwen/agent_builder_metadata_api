# Frontend Integration Guide

How the React Agent Workflow Builder integrates with the FastAPI metadata
server. Pairs with `feeding_files/session_summary.md`, which describes the
React app's current localStorage-backed state.

## Contents

- [Overview](#overview)
- [Setup: API client + TypeScript types](#setup-api-client--typescript-types)
- [Endpoints](#endpoints)
  1. [Save workflow](#1-save-workflow)
  2. [Load workflow](#2-load-workflow)
  3. [List workflows](#3-list-workflows)
  4. [Validate workflow](#4-validate-workflow)
  5. [Delete workflow](#5-delete-workflow)
  6. [Duplicate workflow](#6-duplicate-workflow)
  7. [Export workflow](#7-export-workflow)
  8. [Import workflow](#8-import-workflow)
  9. [Get versions](#9-get-versions)
  10. [Rollback to version](#10-rollback-to-version)
- [Error handling](#error-handling)
- [Migration from localStorage](#migration-from-localstorage)
- [Rollout checklist](#rollout-checklist)

---

## Overview

- **Base URL** (dev): `http://localhost:8000`. Override per-environment via
  `VITE_API_BASE_URL`.
- **CORS** is preconfigured for `http://localhost:5173` and
  `http://127.0.0.1:5173` (the Vite dev server).
- **All requests and responses are JSON.** The wire format for `WorkflowDoc`
  matches what `serializeWorkflow()` already emits — no rename layer needed.
- **Optional `X-Actor-Id` header.** Threaded into audit logs and `created_by`.
  Send the user id once auth lands; until then, omit it or send a dev string.
- **Optimistic concurrency** on `PUT /api/workflows/{id}` via the `If-Match`
  header (numeric `current_version` from the last load).

---

## Setup: API client + TypeScript types

Add a thin client alongside the existing storage helpers. **Don't replace
`workflow/storage.ts` yet** — keep both during the migration so users can
still save offline.

### Proposed file structure

```
src/workflow/api/
├── client.ts          # fetch wrapper + ApiError
├── workflowApi.ts     # one function per endpoint
└── types.ts           # mirrors the server response models
```

### `src/workflow/api/types.ts`

```ts
// Mirrors the server's Pydantic models. Field names match exactly so the
// React serializer's WorkflowDoc shape passes through unchanged.

export type WorkflowStatus = "draft" | "published" | "archived";
export type ValidationStatus = "valid" | "warning" | "error";
export type ValidationSeverity = "error" | "warning";

export interface ValidationIssue {
  path: string;
  message: string;
  severity: ValidationSeverity;
  code: string | null;
}

export interface ValidationResult {
  valid: boolean;
  issues: ValidationIssue[];
}

export interface WorkflowMeta {
  workflow_id: string;
  name: string;
  description: string | null;
  status: WorkflowStatus;
  current_version: number;
  owner_id: string | null;
  tags: string[];
  category: string | null;
  node_types: string[];
  created_at: string; // ISO 8601
  updated_at: string;
}

// WorkflowDoc shape — IDENTICAL to what serializeWorkflow() emits.
export interface WorkflowDoc {
  id: string;
  name: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

export interface WorkflowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: { name: string; config: Record<string, unknown>; [k: string]: unknown };
}

export interface WorkflowEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle: string | null;
  targetHandle: string | null;
}

export interface WorkflowResponse {
  meta: WorkflowMeta;
  doc: WorkflowDoc;
}

export interface PaginationMeta {
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface WorkflowListResponse {
  items: WorkflowMeta[];
  pagination: PaginationMeta;
}

export interface WorkflowVersionSummary {
  workflow_id: string;
  version: number;
  change_note: string | null;
  created_at: string;
  created_by: string | null;
  valid: boolean | null;
}

export interface WorkflowVersionResponse {
  workflow_id: string;
  version: number;
  doc: WorkflowDoc;
  change_note: string | null;
  validation: ValidationResult | null;
  created_at: string;
  created_by: string | null;
}

export interface VersionListResponse {
  items: WorkflowVersionSummary[];
  pagination: PaginationMeta;
}

export interface ImportResponse {
  workflow_id: string;
  created: boolean;
  version: number;
  validation: ValidationResult;
}

export interface ExportResponse {
  workflow_id: string;
  version: number;
  exported_at: string;
  doc: WorkflowDoc;
}
```

### `src/workflow/api/client.ts`

```ts
import type { ValidationIssue } from "./types";

const API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | undefined,
    message: string,
    readonly issues?: ValidationIssue[],
  ) {
    super(message);
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  actorId?: string;
  ifMatch?: number;
}

export async function apiFetch<T>(
  path: string,
  { body, actorId, ifMatch, headers, ...init }: RequestOptions = {},
): Promise<T> {
  const finalHeaders: Record<string, string> = {
    Accept: "application/json",
    ...(headers as Record<string, string> | undefined),
  };
  if (body !== undefined) finalHeaders["Content-Type"] = "application/json";
  if (actorId) finalHeaders["X-Actor-Id"] = actorId;
  if (ifMatch !== undefined) finalHeaders["If-Match"] = String(ifMatch);

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: finalHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return undefined as T;

  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON body */
  }

  if (!response.ok) {
    const err = (payload ?? {}) as {
      detail?: string;
      code?: string;
      issues?: ValidationIssue[];
    };
    throw new ApiError(
      response.status,
      err.code,
      err.detail ?? response.statusText,
      err.issues,
    );
  }

  return payload as T;
}
```

### `src/workflow/api/workflowApi.ts`

```ts
import { apiFetch } from "./client";
import type {
  ExportResponse,
  ImportResponse,
  ValidationResult,
  VersionListResponse,
  WorkflowDoc,
  WorkflowListResponse,
  WorkflowMeta,
  WorkflowResponse,
  WorkflowStatus,
  WorkflowVersionResponse,
} from "./types";

export const workflowApi = {
  // 1. Save (create new)
  create(input: {
    name: string;
    description?: string;
    tags?: string[];
    category?: string;
    doc?: WorkflowDoc;
    change_note?: string;
    actorId?: string;
  }): Promise<WorkflowResponse> {
    const { actorId, ...body } = input;
    return apiFetch("/api/workflows", { method: "POST", body, actorId });
  },

  // 1. Save (update existing — full doc, bumps version)
  update(
    workflowId: string,
    input: {
      doc: WorkflowDoc;
      change_note?: string;
      ifMatch?: number;        // last loaded current_version, for OCC
      actorId?: string;
    },
  ): Promise<WorkflowResponse> {
    const { ifMatch, actorId, ...body } = input;
    return apiFetch(`/api/workflows/${workflowId}`, {
      method: "PUT",
      body,
      ifMatch,
      actorId,
    });
  },

  // Metadata-only patch (no version bump)
  patch(
    workflowId: string,
    input: Partial<{
      name: string;
      description: string | null;
      tags: string[];
      category: string | null;
      status: WorkflowStatus;
    }> & { actorId?: string },
  ): Promise<WorkflowMeta> {
    const { actorId, ...body } = input;
    return apiFetch(`/api/workflows/${workflowId}`, {
      method: "PATCH",
      body,
      actorId,
    });
  },

  // 2. Load
  get(workflowId: string): Promise<WorkflowResponse> {
    return apiFetch(`/api/workflows/${workflowId}`);
  },

  // 3. List + search
  list(params: {
    page?: number;
    page_size?: number;
    q?: string;
    owner_id?: string;
    status?: WorkflowStatus;
    tag?: string;
    category?: string;
    node_type?: string;
    validation_status?: "valid" | "warning" | "error";
    include_deleted?: boolean;
    sort_by?: "updated_at" | "created_at" | "name" | "current_version";
    sort_order?: "asc" | "desc";
  } = {}): Promise<WorkflowListResponse> {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) qs.set(k, String(v));
    }
    const suffix = qs.toString() ? `?${qs}` : "";
    return apiFetch(`/api/workflows${suffix}`);
  },

  // 4. Validate (stateless)
  validate(doc: WorkflowDoc): Promise<ValidationResult> {
    return apiFetch("/api/workflows/validate", { method: "POST", body: doc });
  },

  // 4b. Validate the stored doc
  validateStored(workflowId: string): Promise<ValidationResult> {
    return apiFetch(`/api/workflows/${workflowId}/validate`, { method: "POST" });
  },

  // 5. Soft delete
  softDelete(workflowId: string, actorId?: string): Promise<WorkflowMeta> {
    return apiFetch(`/api/workflows/${workflowId}`, { method: "DELETE", actorId });
  },

  // 5b. Hard delete
  hardDelete(workflowId: string, actorId?: string): Promise<void> {
    return apiFetch(`/api/workflows/${workflowId}/hard-delete`, {
      method: "DELETE",
      actorId,
    });
  },

  archive(workflowId: string, actorId?: string): Promise<WorkflowMeta> {
    return apiFetch(`/api/workflows/${workflowId}/archive`, {
      method: "POST",
      actorId,
    });
  },

  unarchive(workflowId: string, actorId?: string): Promise<WorkflowMeta> {
    return apiFetch(`/api/workflows/${workflowId}/unarchive`, {
      method: "POST",
      actorId,
    });
  },

  restore(workflowId: string, actorId?: string): Promise<WorkflowMeta> {
    return apiFetch(`/api/workflows/${workflowId}/restore`, {
      method: "POST",
      actorId,
    });
  },

  // 6. Duplicate
  duplicate(
    workflowId: string,
    input: { new_name?: string; owner_id?: string; actorId?: string } = {},
  ): Promise<WorkflowResponse> {
    const { actorId, ...body } = input;
    return apiFetch(`/api/workflows/${workflowId}/duplicate`, {
      method: "POST",
      body,
      actorId,
    });
  },

  // 7. Export latest
  exportLatest(workflowId: string): Promise<ExportResponse> {
    return apiFetch(`/api/workflows/${workflowId}/export`);
  },

  exportVersion(workflowId: string, version: number): Promise<ExportResponse> {
    return apiFetch(`/api/workflows/${workflowId}/versions/${version}/export`);
  },

  // 8. Import
  importWorkflow(
    payload: WorkflowDoc | { doc: WorkflowDoc; [k: string]: unknown },
    opts: { replaceWorkflowId?: string; actorId?: string } = {},
  ): Promise<ImportResponse> {
    const qs = opts.replaceWorkflowId
      ? `?replace_workflow_id=${encodeURIComponent(opts.replaceWorkflowId)}`
      : "";
    return apiFetch(`/api/workflows/import${qs}`, {
      method: "POST",
      body: payload,
      actorId: opts.actorId,
    });
  },

  // 9. Versions
  listVersions(
    workflowId: string,
    params: { page?: number; page_size?: number } = {},
  ): Promise<VersionListResponse> {
    const qs = new URLSearchParams();
    if (params.page) qs.set("page", String(params.page));
    if (params.page_size) qs.set("page_size", String(params.page_size));
    const suffix = qs.toString() ? `?${qs}` : "";
    return apiFetch(`/api/workflows/${workflowId}/versions${suffix}`);
  },

  getVersion(
    workflowId: string,
    version: number,
  ): Promise<WorkflowVersionResponse> {
    return apiFetch(`/api/workflows/${workflowId}/versions/${version}`);
  },

  snapshotVersion(
    workflowId: string,
    input: { change_note?: string; actorId?: string } = {},
  ): Promise<WorkflowVersionResponse> {
    const { actorId, ...body } = input;
    return apiFetch(`/api/workflows/${workflowId}/versions`, {
      method: "POST",
      body,
      actorId,
    });
  },

  // 10. Rollback
  rollback(
    workflowId: string,
    version: number,
    input: { change_note?: string; actorId?: string } = {},
  ): Promise<WorkflowResponse> {
    const { actorId, ...body } = input;
    return apiFetch(
      `/api/workflows/${workflowId}/versions/${version}/rollback`,
      { method: "POST", body, actorId },
    );
  },
};
```

---

## Endpoints

Every example below uses the same dev base URL: `http://localhost:8000`.

### 1. Save workflow

#### Create new — `POST /api/workflows`

Use when the workflow doesn't yet have a server id.

**Request**
```http
POST /api/workflows
Content-Type: application/json
X-Actor-Id: alice
```
```json
{
  "name": "Customer Support Bot",
  "description": "Tier-1 triage agent",
  "tags": ["support", "tier1"],
  "category": "support",
  "change_note": "initial draft",
  "doc": {
    "id": "ignored-by-server",
    "name": "Customer Support Bot",
    "nodes": [
      {
        "id": "n_start",
        "type": "start",
        "position": { "x": 240, "y": 200 },
        "data": { "name": "Start", "config": {} }
      },
      {
        "id": "n_llm",
        "type": "llm",
        "position": { "x": 480, "y": 200 },
        "data": {
          "name": "Triage",
          "config": {
            "model": "claude-opus-4-7",
            "temperature": 0.4,
            "messages": [
              { "role": "system", "content": "Triage tickets." },
              { "role": "user",   "content": "{{system.userQuery}}" }
            ],
            "outputVariables": [{ "name": "category", "type": "string" }]
          }
        }
      },
      {
        "id": "n_output",
        "type": "output",
        "position": { "x": 720, "y": 200 },
        "data": { "name": "Output", "config": {} }
      }
    ],
    "edges": [
      { "id": "e1", "source": "n_start", "target": "n_llm",    "sourceHandle": null, "targetHandle": null },
      { "id": "e2", "source": "n_llm",   "target": "n_output", "sourceHandle": null, "targetHandle": null }
    ]
  }
}
```

> **Note** — the server overwrites `doc.id` with the generated `workflow_id`.
> Send any value the React serializer produces; the response will carry the
> authoritative id in both `meta.workflow_id` and `doc.id`.

**Response — `201 Created`**
```json
{
  "meta": {
    "workflow_id": "wf_xQ7k2BfJ9aE",
    "name": "Customer Support Bot",
    "description": "Tier-1 triage agent",
    "status": "draft",
    "current_version": 1,
    "owner_id": "alice",
    "tags": ["support", "tier1"],
    "category": "support",
    "node_types": ["llm", "output", "start"],
    "created_at": "2026-05-10T05:55:00.123456+00:00",
    "updated_at": "2026-05-10T05:55:00.123456+00:00"
  },
  "doc": {
    "id": "wf_xQ7k2BfJ9aE",
    "name": "Customer Support Bot",
    "nodes": [ /* …same as request, with doc.id stamped */ ],
    "edges": [ /* …same as request */ ]
  }
}
```

**TS**
```ts
const created = await workflowApi.create({
  name: doc.name,
  doc: serializeWorkflow(currentRfState),
  actorId: currentUser?.id,
});
store.setServerWorkflow(created.meta, created.doc);
```

#### Update existing — `PUT /api/workflows/{workflow_id}`

Bumps `current_version` and writes a new version row. Use **`If-Match`** with
the version you loaded for optimistic concurrency control.

**Request**
```http
PUT /api/workflows/wf_xQ7k2BfJ9aE
Content-Type: application/json
If-Match: 1
X-Actor-Id: alice
```
```json
{
  "doc": { "id": "wf_xQ7k2BfJ9aE", "name": "...", "nodes": [...], "edges": [...] },
  "change_note": "tightened triage prompt"
}
```

**Response — `200 OK`** — same shape as create, with `current_version: 2`.

**Conflict — `409`** when `If-Match` is stale:
```json
{
  "detail": "version mismatch: expected current_version=1, actual=3",
  "code": "CONFLICT"
}
```

Surface a "Someone else updated this — Reload / Overwrite" UI on 409.

---

### 2. Load workflow

`GET /api/workflows/{workflow_id}`

**Response — `200 OK`** — `WorkflowResponse` (same shape as create response).

**404** when the id doesn't exist:
```json
{ "detail": "workflow 'wf_missing' not found", "code": "NOT_FOUND" }
```

**TS**
```ts
const { meta, doc } = await workflowApi.get(workflowId);
deserializeWorkflow(doc);          // existing helper
store.setServerWorkflow(meta, doc);
```

---

### 3. List workflows

`GET /api/workflows`

Supports the full filter/sort/pagination set:

| Param | Type | Default | Notes |
|---|---|---|---|
| `page` | int ≥ 1 | 1 | |
| `page_size` | int 1–200 | 20 | |
| `q` | string | — | Case-insensitive substring match on `name` |
| `owner_id` | string | — | |
| `status` | `draft` / `published` / `archived` | — | |
| `tag` | string | — | Single-tag match |
| `category` | string | — | |
| `node_type` | string | — | Workflows containing at least one node of this type |
| `validation_status` | `valid` / `warning` / `error` | — | From the denormalized summary |
| `include_deleted` | bool | false | When false, archived rows are hidden |
| `sort_by` | `updated_at` / `created_at` / `name` / `current_version` | `updated_at` | |
| `sort_order` | `asc` / `desc` | `desc` | |

**Example**
```
GET /api/workflows?q=customer&status=draft&page=1&page_size=10&sort_by=name&sort_order=asc
```

**Response — `200 OK`**
```json
{
  "items": [
    {
      "workflow_id": "wf_xQ7k2BfJ9aE",
      "name": "Customer Support Bot",
      "description": "Tier-1 triage agent",
      "status": "draft",
      "current_version": 3,
      "owner_id": "alice",
      "tags": ["support"],
      "category": "support",
      "node_types": ["llm", "output", "start"],
      "created_at": "2026-05-10T05:55:00.123456+00:00",
      "updated_at": "2026-05-10T06:14:21.987654+00:00"
    }
  ],
  "pagination": {
    "total": 1,
    "page": 1,
    "page_size": 10,
    "has_more": false
  }
}
```

**TS**
```ts
const page = await workflowApi.list({ q: search, page: 1, page_size: 20 });
setLibraryItems(page.items);
setPagination(page.pagination);
```

---

### 4. Validate workflow

Stateless — no DB write. Use this from the editor on every save attempt
(or on a debounce) to surface issues live.

`POST /api/workflows/validate`

**Request body** — a bare `WorkflowDoc`:
```json
{
  "id": "wf_local",
  "name": "Draft",
  "nodes": [
    { "id": "s", "type": "start",  "position": {"x":0,"y":0}, "data": {"name":"S","config":{}} }
  ],
  "edges": []
}
```

**Response — `200 OK`** (this doc is invalid — no Output, single root):
```json
{
  "valid": false,
  "issues": [
    {
      "path": "nodes",
      "message": "No 'output' node found",
      "severity": "error",
      "code": "OUTPUT_NODE_MISSING"
    }
  ]
}
```

A valid doc returns `{ "valid": true, "issues": [] }`.

There is also `POST /api/workflows/{workflow_id}/validate` to validate the
stored doc without re-uploading it — useful for the workflow library view
("rerun validation on this saved workflow").

---

### 5. Delete workflow

#### Soft delete — `DELETE /api/workflows/{workflow_id}`

Sets `status: "archived"`. Hidden from default list responses; pass
`include_deleted=true` to surface it in the library.

**Response — `200 OK`** — updated `WorkflowMeta`:
```json
{
  "workflow_id": "wf_xQ7k2BfJ9aE",
  "name": "Customer Support Bot",
  "status": "archived",
  "current_version": 3,
  "...": "..."
}
```

#### Hard delete — `DELETE /api/workflows/{workflow_id}/hard-delete`

Cascades across `workflows`, `workflow_versions`, and `workflow_audit_logs`.
Gated by the server's `enable_hard_delete` setting (default true in dev,
false recommended in production).

**Response — `204 No Content`** on success.
**`403`** when disabled:
```json
{
  "detail": "hard delete is disabled — initialize WorkflowService with allow_hard_delete=True",
  "code": "OPERATION_NOT_ALLOWED"
}
```

---

### 6. Duplicate workflow

`POST /api/workflows/{workflow_id}/duplicate`

**Request**
```json
{
  "new_name": "Customer Support Bot (copy)",
  "owner_id": "alice"
}
```
Both fields are optional. Default `new_name` is `"<source name> (copy)"`.

**Response — `201 Created`** — full `WorkflowResponse` with a fresh
`workflow_id`, `current_version: 1`, `status: "draft"`.

---

### 7. Export workflow

`GET /api/workflows/{workflow_id}/export` — latest version
`GET /api/workflows/{workflow_id}/versions/{version}/export` — specific version

**Response — `200 OK`**
```json
{
  "workflow_id": "wf_xQ7k2BfJ9aE",
  "version": 3,
  "exported_at": "2026-05-10T06:30:00.000000+00:00",
  "doc": { "id": "wf_xQ7k2BfJ9aE", "name": "...", "nodes": [...], "edges": [...] }
}
```

The envelope is self-contained and re-importable.

**TS — download to file**
```ts
async function exportToFile(workflowId: string, fallbackName: string) {
  const payload = await workflowApi.exportLatest(workflowId);
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slugify(payload.doc.name || fallbackName)}-v${payload.version}.json`;
  a.click();
  URL.revokeObjectURL(url);
}
```

---

### 8. Import workflow

`POST /api/workflows/import`

Three accepted payload shapes:

1. **Bare `WorkflowDoc`** — `{ id, name, nodes, edges }`
2. **Export envelope** — what `/export` returns: `{ workflow_id, version, exported_at, doc }`
3. **Create envelope** — `{ name?, description?, tags?, category?, doc, change_note? }`

**Request — bare doc**
```http
POST /api/workflows/import
Content-Type: application/json
```
```json
{
  "id": "wf_imported",
  "name": "Imported Flow",
  "nodes": [...],
  "edges": [...]
}
```

**Request — replace existing workflow** (use the query param):
```http
POST /api/workflows/import?replace_workflow_id=wf_xQ7k2BfJ9aE
```
Body is the same. The server runs the import through `update_workflow`,
bumping the version of the targeted workflow.

**Response — `200 OK`**
```json
{
  "workflow_id": "wf_AbCdEf123",
  "created": true,
  "version": 1,
  "validation": { "valid": true, "issues": [] }
}
```

When `replace_workflow_id` is supplied: `created: false`, `version: N+1`.

**TS — file picker**
```ts
async function importFromFile(file: File) {
  const text = await file.text();
  const payload = JSON.parse(text);
  return workflowApi.importWorkflow(payload);
}
```

---

### 9. Get versions

`GET /api/workflows/{workflow_id}/versions?page=1&page_size=20`

**Response — `200 OK`** (newest first)
```json
{
  "items": [
    {
      "workflow_id": "wf_xQ7k2BfJ9aE",
      "version": 3,
      "change_note": "tightened triage prompt",
      "created_at": "2026-05-10T06:14:21.987654+00:00",
      "created_by": "alice",
      "valid": true
    },
    {
      "workflow_id": "wf_xQ7k2BfJ9aE",
      "version": 2,
      "change_note": "added approval branch",
      "created_at": "2026-05-10T05:58:00.000000+00:00",
      "created_by": "alice",
      "valid": true
    },
    {
      "workflow_id": "wf_xQ7k2BfJ9aE",
      "version": 1,
      "change_note": "initial draft",
      "created_at": "2026-05-10T05:55:00.123456+00:00",
      "created_by": "alice",
      "valid": true
    }
  ],
  "pagination": { "total": 3, "page": 1, "page_size": 20, "has_more": false }
}
```

To fetch a single historical doc, hit
`GET /api/workflows/{workflow_id}/versions/{version}` — returns the full
`WorkflowVersionResponse` including the doc, the saved validation report,
and the change note.

To create a manual snapshot of the current head as a new version row:
`POST /api/workflows/{workflow_id}/versions`
```json
{ "change_note": "v1.0 launch" }
```

---

### 10. Rollback to version

`POST /api/workflows/{workflow_id}/versions/{version}/rollback`

Promotes the doc from `target_version` back to head as a **new** version
row (history is append-only; the original version row is preserved).

**Request** (optional body)
```json
{ "change_note": "Reverting to last good config" }
```

**Response — `200 OK`** — full `WorkflowResponse` with the restored doc and
`current_version` incremented:
```json
{
  "meta": {
    "workflow_id": "wf_xQ7k2BfJ9aE",
    "current_version": 4,
    "...": "..."
  },
  "doc": { "id": "wf_xQ7k2BfJ9aE", "name": "...", "...": "..." }
}
```

The audit log records `action: "rolled_back"` with
`details.target_version: <N>`.

---

## Error handling

Every error response shares the same envelope:

```json
{
  "detail": "human-readable message",
  "code": "NOT_FOUND" | "CONFLICT" | "VALIDATION_FAILED" | "OPERATION_NOT_ALLOWED",
  "issues": [ /* only on VALIDATION_FAILED */ ]
}
```

| Status | `code` | When | UI suggestion |
|---|---|---|---|
| 400 | (FastAPI default) | Bad `If-Match`, malformed body | inline form error |
| 403 | `OPERATION_NOT_ALLOWED` | Hard delete disabled in this env | hide / disable the button |
| 404 | `NOT_FOUND` | Workflow / version doesn't exist | redirect to library |
| 409 | `CONFLICT` | Stale `If-Match` on PUT, duplicate id on create | "reload or overwrite" dialog |
| 422 | `VALIDATION_FAILED` | Pre-save validation found errors | inline issue list |

The `ApiError` class above carries `status`, `code`, `message`, and `issues`.

```ts
try {
  await workflowApi.update(workflowId, { doc, ifMatch: lastVersion });
} catch (e) {
  if (e instanceof ApiError) {
    if (e.code === "CONFLICT")            return showConflictDialog();
    if (e.code === "VALIDATION_FAILED")   return showIssueList(e.issues ?? []);
    if (e.code === "NOT_FOUND")           return navigate("/library");
  }
  throw e;
}
```

---

## Migration from localStorage

The current React app's `workflow/storage.ts` exports four functions. Here
is how each maps to the new server-backed API.

| Existing helper | New behavior |
|---|---|
| `saveToLocalStorage(doc)` | Calls **`workflowApi.create()`** if the workflow has no `serverWorkflowId` yet, otherwise **`workflowApi.update(serverWorkflowId, …)`** with `ifMatch: serverVersion`. |
| `loadFromLocalStorage()` | Replaced by **`workflowApi.get(serverWorkflowId)`** when the user opens a workflow from the library. localStorage stays as the boot-time fallback for "last workflow I had open" until the library UI ships. |
| `exportWorkflowToFile(doc)` | Stays available for offline export. For server-backed workflows, prefer **`workflowApi.exportLatest(id)`** to capture the authoritative server doc + version, then write the response JSON to a file using the helper above. |
| `importWorkflowFromFile()` | Reads the file → calls **`workflowApi.importWorkflow(payload)`**. The server accepts the bare React-serialized doc, the server's own export envelope, or a create-style envelope — no transform required. |

### Suggested coexistence: feature-flag, don't replace

Keep `workflow/storage.ts` and the new `workflow/api/` side-by-side during
the rollout. Add a flag to the Zustand store:

```ts
// store/workflowStore.ts (new fields)
type WorkflowStoreState = {
  // existing…
  useServer: boolean;            // feature flag, persisted in localStorage
  serverWorkflowId: string | null;
  serverVersion: number | null;
  lastServerSyncAt: number | null;
  serverError: string | null;
};
```

`save()` becomes:

```ts
async function save() {
  const doc = serializeWorkflow(get().nodes, get().edges, get().docName);

  // Local validation as before (Zod). Optionally hit /validate for parity.
  const localIssues = validateWorkflow(doc);
  if (localIssues.length) { setSaveError(localIssues); return; }

  if (!get().useServer) {
    saveToLocalStorage(doc);
    set({ lastSavedAt: Date.now() });
    return;
  }

  try {
    const result = get().serverWorkflowId
      ? await workflowApi.update(get().serverWorkflowId!, {
          doc,
          ifMatch: get().serverVersion ?? undefined,
          actorId: get().currentUserId,
        })
      : await workflowApi.create({
          name: doc.name,
          doc,
          actorId: get().currentUserId,
        });

    set({
      serverWorkflowId: result.meta.workflow_id,
      serverVersion: result.meta.current_version,
      lastServerSyncAt: Date.now(),
      serverError: null,
    });
  } catch (e) {
    if (e instanceof ApiError && e.code === "CONFLICT") {
      // Open the "reload or overwrite" dialog.
      set({ conflictOpen: true });
      return;
    }
    if (e instanceof ApiError && e.code === "VALIDATION_FAILED") {
      setSaveError(e.issues);
      return;
    }
    set({ serverError: (e as Error).message });
  }
}
```

`load()` becomes:

```ts
async function load(serverWorkflowId?: string) {
  if (!get().useServer || !serverWorkflowId) {
    const doc = loadFromLocalStorage();
    if (doc) hydrate(doc);
    return;
  }

  const { meta, doc } = await workflowApi.get(serverWorkflowId);
  hydrate(deserializeWorkflow(doc));
  set({
    serverWorkflowId: meta.workflow_id,
    serverVersion: meta.current_version,
    lastServerSyncAt: Date.now(),
  });
}
```

Boot behavior:

```ts
// On app boot:
// 1. If useServer && lastServerWorkflowId, load from server.
// 2. Else fall back to loadFromLocalStorage() (existing behavior).
```

### UI changes worth shipping with the migration

- **Library modal** — header button → list `GET /api/workflows`
  results with search, filter, "New", "Duplicate", "Archive".
- **Version badge** — show `v{currentVersion}` next to the workflow name
  in the header when loaded from the server.
- **Versions panel** — sidebar tab listing `GET /…/versions` results;
  click to preview, click "Restore" to call `POST /…/rollback`.
- **Conflict dialog** — appears on 409. Two actions: "Reload from server"
  (calls `get` and discards local edits) or "Overwrite" (refetch latest
  `current_version`, then `update` again with the new `If-Match`).
- **Save status banner** — extend the existing draft/saved/error states
  with `synced` / `syncing` / `conflict`.

---

## Rollout checklist

- [ ] Add `VITE_API_BASE_URL=http://localhost:8000` to `.env.local`.
- [ ] Drop in `src/workflow/api/{client,types,workflowApi}.ts`.
- [ ] Add `useServer` + server-state fields to `workflowStore.ts`.
- [ ] Wrap save / load to choose server vs localStorage based on `useServer`.
- [ ] Wire the library modal + version badge.
- [ ] Add the 409 conflict dialog.
- [ ] Verify CORS by hitting `GET /health` from the browser devtools console.
- [ ] Manual smoke: create → edit → save → reload → restore prior version → import → export.
- [ ] Toggle `useServer` to false and confirm the offline path still works.
- [ ] Once stable, remove the feature flag and the localStorage save path
      (keep export/import for offline portability).
