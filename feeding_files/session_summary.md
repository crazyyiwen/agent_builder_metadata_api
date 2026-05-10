# Build React Agent Workflow Builder Canvas — Session Summary

A chronological record of how this workflow-builder app was built, the
mid-build issues that came up, and the final state of the codebase.

---

## Goal

Ship a fully dynamic, schema/registry-driven visual orchestration editor
for AI agent workflows — comparable in feel to n8n / Zapier / LangGraph
Studio. Every node type, every form field, every output handle had to be
data-driven so adding a new node type is a JSON-only edit.

## Tech stack

- **Vite** + **React 18** + **TypeScript (strict)**
- **React Flow 11** for the canvas, custom nodes, custom edges
- **Zustand** as the workflow's single source of truth
- **Tailwind CSS 3** for styling
- **Zod** for schema validation
- **Lucide React** for iconography
- **nanoid** for ids

---

## Build phases

### Phase 1 — Project shell + static layout

- Scaffolded Vite + React + TS at the repo root.
- Installed: `reactflow`, `zustand`, `zod`, `nanoid`, `clsx`, `lucide-react`.
- Built the three-column shell: `Header` + `NodePalette` + `WorkflowCanvas` + `PropertiesPanel`.
- Authored the design tokens (`canvas`, `ink`, `brand`, `node` shadows) in `tailwind.config.js`.
- Set up the React Flow CSS import + global tweaks.

### Phase 2 — Registry, canvas, dynamic node card

- `workflow/types.ts` — strict types for `WorkflowDoc`, `FieldSchema`, `NodeRegistry`, `IconName`, `NodeColor`.
- `workflow/nodeRegistry.ts` — all 12 user-facing node types declared as JSON.
- `store/workflowStore.ts` — Zustand store owning `nodes`, `edges`, `selectedNodeId`. Forwarded `applyNodeChanges` / `applyEdgeChanges` / `connect`.
- `components/canvas/DynamicWorkflowNode.tsx` — the **only** custom RF node type. Reads from the registry; renders icon + name + sublabel + handles.
- `components/layout/NodePalette.tsx` — flat list generated from `paletteOrder × nodeRegistry`, with HTML5 drag-and-drop.
- `components/layout/PropertiesPanel.tsx` — placeholder skeleton showing every section + field label from the schema.
- Verified: drop creates a node, name uniqueness enforced (`llm`, `llm_1`, `llm_2`), delete works, pan/zoom/minimap/controls live.

### Phase 3 — DynamicFormRenderer + 16 field types

- `forms/FieldRenderer.tsx` — single switch on `FieldSchema.type`. Adding a new field type = one literal in the union + one case here.
- Inline simple fields: `text`, `textarea`, `number`, `switch`, `select`, `multi-select`, `code`, `json`, `variable-reference`, `variable-reference-list`.
- Extracted complex fields: `KeyValueListField`, `MessageListField`, `MappingListField` (with optional `withOperation`), `SchemaBuilder`, `ConditionBuilder` (`blocks` + `flat` shapes), `AccordionField` (recursive nesting).
- `forms/useField.ts` — `useFieldValue` / `useFieldSetter` hooks; per-field subscriptions so editing one field doesn't re-render the rest.
- Cleaned the registry: `name` and `description` moved out of `formSections` (they belong to the panel header alone).
- HTTP node demonstrated `accordion-section` (Auth nested inside).

### Phase 4 — Schema completeness + Script Test button

- Audited every node against the spec. Filled remaining gaps:
  - `script-runner` field type — Test button + JSON test input + sandboxed `new Function("input", code)` execution + result/ms display.
  - HTTP Request: added Response Mapping section.
  - Guardrail: added Reason expression.
  - External Agent: added Auth (accordion).
  - LLM / Agent / Sub Flow: promoted Output Variables to a top-level section under `config.outputVariables`.

### Phase 5 — Variable reference system

- `workflow/variableContext.ts` — `VariableContext` shape (`system.*`, `runtime.workflowMetaData.*`, `nodes.<name>.result.<field>`) + `resolveString` (interpolated) + `resolveValue` (single-ref preserves type) + `getAvailableVariables(doc, excludeNodeId)`.
- `forms/VariableChip.tsx` — chip + `VariableChipsPreview` that splits a string on `{{...}}` and renders refs as inline blue chips.
- `forms/VariablePicker.tsx` — portal-rendered popover (escapes the right panel's `overflow-y-auto`), viewport-aware (auto-flips above when no room below), search, three collapsible groups (System / Runtime / Node Results).
- `forms/ExpressionInput.tsx` — input/textarea + `{ x }` button that opens the picker; insertion preserves the caret position.
- Wired into: `text`, `textarea`, `variable-reference`, `variable-reference-list`, `mapping-list` value column, `message-list` content, `condition-builder` field + value.

### Phase 6 — Persistence

- `workflow/validation.ts` — Zod schema for the entire doc + semantic checks (unique node names, edge endpoint existence, exactly one Start) + `dedupeNodeNames`.
- `workflow/serialize.ts` — `serializeWorkflow` strips RF runtime fields; `deserializeWorkflow` retrofits a Start node if missing and dedupes names.
- `workflow/storage.ts` — `saveToLocalStorage` / `loadFromLocalStorage` / `exportWorkflowToFile` / `importWorkflowFromFile`.
- Store: auto-loads from localStorage on boot. New actions: `save`, `exportToFile`, `importFromFile`, `setDocName`, `resetToEmpty`. Status fields: `lastSavedAt`, `saveError`.
- Header: editable workflow name, Import/Export icons, Save validates and surfaces issues. Status banner shows draft/saved/error states.
- PropertiesPanel: live duplicate-name warning under the name input.

### Phase 7 — Runtime simulation

- `workflow/execution/resolveVariables.ts` — re-exports the resolver and adds `resolveDeep` for arrays/objects.
- `workflow/execution/nodeExecutors.ts` — one mock executor per node type. All synchronous. Returns either `{ kind: "done", result, nextHandle?, stateUpdates? }` or `{ kind: "pause", awaiting }`.
- `workflow/execution/executor.ts` — `startExecution(doc, seed)` and `resumeExecution(doc, state, response)`. Traversal picks the next edge by matching `(edge.sourceHandle ?? "out")` against the executor's `nextHandle`. `MAX_STEPS = 100` loop guard.
- `workflow/execution/setByPath.ts` — shared mutating dot-path setter.
- `components/run/RunPanel.tsx` — portal-rendered modal: userQuery seed → live execution log → Human Input / Approval pause prompts → final Output JSON. Errors surfaced in a red panel.
- Header **Run** button toggles `runOpen` in the store.

### Phase 8 — Visual polish

- Edges: `MarkerType.ArrowClosed` arrowheads, brand-blue stroke + thicker on selection, brand-blue smoothstep connection-line preview during drag.
- MiniMap: each node renders in its registry color (added `MINIMAP_COLORS` map in `colorTokens.ts`).
- Controls: rounded, single shadow, ink-100 hover, 28 px hit-targets.
- Handles: 1.5 px gray ring → brand-blue on hover with 1.15× scale; green on valid drop.
- Node card: bumped to 224 px wide, 40 px icon block, 1 px hover lift.
- Palette: subtle category separators, hover-scale on icon block, focus-glow on search.
- Section headers: uppercase with `tracking-[0.06em]`, smoother `duration-150` chevron.
- Soft scrollbar (`.scrollbar-soft`) on palette + right panel.
- OpenType `font-feature-settings: "ss01", "cv11"` for crisper letterforms.

---

## Mid-build issues + fixes

### Node not appearing on canvas after drop

**Symptom**: header showed "1 node", right panel populated correctly, but the canvas stayed empty and the MiniMap was blank.

**Cause**: the store's `fromRFNodes` was stripping the `width` / `height` / `positionAbsolute` / `dragging` fields that React Flow attaches when it measures a new node. On the next render RF saw the node as "unmeasured" and refused to render it — the dimensions update bounced through the store and was discarded every frame.

**Fix**: extended `WorkflowNode` with optional runtime fields and round-tripped them in both `toRFNodes` and `fromRFNodes`. (`serializeWorkflow` strips them on save, so the on-disk JSON stays clean.)

### Drag from palette didn't reliably register

**Cause**: `onDragOver` only called `e.preventDefault()` when the custom MIME type was visible in `dataTransfer.types`. Safari and some Firefox versions hide custom MIME types during `dragover`, so `preventDefault` never fired and the browser refused the drop.

**Fix**: always `preventDefault` in `onDragOver`, defer the type check to `onDrop`. Also moved the DnD handlers from `<ReactFlow>` to the wrapper `<div>` because RF's pointer-event capture was interfering.

### Right panel showed an empty white pane when nothing was selected

**Fix**: `PropertiesPanel` now returns `null` when `selectedNodeId` is null. The canvas reclaims the panel's width.

### Rule node didn't grow with branches

**Cause**: handles were absolutely positioned with hand-calculated `top` offsets between 22 px and 50 px. Five branches → all squeezed into 28 px → overlapping dots.

**Fix**: rewrote `DynamicWorkflowNode` so multi-output nodes render a `<ul>` below the header with one `<li>` per branch. Each row is `position: relative`, so the handle inherits the row as its reference and pins to the right edge cleanly. Card grows naturally with the branch list.

### Handoff feature for Agent node

- Added `handoffs: []` to `agent.defaultConfig` and a "Handoffs" section using the new `handoff-list` field type.
- Each handoff exposes a labeled output handle on the canvas card (orange-tinted with a `↳` icon, distinguished from regular branches via `HandleSpec.kind`).
- `HandoffListField`'s name input is an autocomplete combobox: lists workflow nodes that aren't already targets of another handoff on this agent. Picking one sets the handoff name and auto-creates the canvas edge.
- Removing a handoff cleans up the orphan edge.
- Maps onto LangGraph's `add_node` + conditional-edge pattern.

### Edge endpoint dragging (reconnect)

- Added `updateEdgeConnection` action using RF's `updateEdge` helper with `shouldReplaceId: false` so the edge identity stays stable.
- Wired `onEdgeUpdate` / `onEdgeUpdateStart` / `onEdgeUpdateEnd` on the canvas.
- Drop on empty canvas → edge deleted (standard RF pattern).

### Wire-path adjustment (drag the bend)

**Iteration 1**: used `getSmoothStepPath` with `centerX`/`centerY` overrides. The `centerY` parameter doesn't actually move horizontal smoothstep paths up/down, so vertical drag moved the handle but not the wire — handle drifted off-path.

**Iteration 2 (current)**: replaced the path generator with two cubic Béziers joined at the user-controlled bend point:

```
M source.x, source.y
C ctrlA.x, source.y   ctrlA.x, bend.y   bend.x, bend.y
C ctrlB.x, bend.y     ctrlB.x, target.y target.x, target.y
```

The join point is exactly `(bend.x, bend.y)`, so the drag handle is always on the wire. Each Bézier exits/enters its endpoint horizontally, so the arrow marker stays oriented along the horizontal axis.

- Drag uses `setPointerCapture` + `screenToFlowPosition` so the gesture works at any zoom and survives fast cursor moves outside the small handle.
- Double-click resets the offset to zero.
- `nopan nodrag` on the handle wrapper prevents React Flow's pan handler from hijacking the drag.

### Delete-on-hover for edges

- Added a rose-tinted `✕` button next to the bend handle.
- Hover state shared between the SVG `<g>` (wire) and the HTML overlay (controls) via React state with a 120 ms grace timer — moving from wire to button doesn't flicker.
- Wider invisible interaction path (`interactionWidth: 24`) makes the wire easy to grab.

---

## Architecture invariants (verified at every checkpoint)

- **Adding a 14th node type** = append a JSON entry to `nodeRegistry.ts` + map a new lucide icon in `Icon.tsx`. Zero new React components, zero canvas logic.
- **Adding a new field type** = add a literal to `FieldType` in `types.ts` + a case in `FieldRenderer.tsx`.
- **No `LlmForm.tsx` / `AgentForm.tsx`** — they don't exist. The right panel never branches on `node.data.type`.
- **Workflow JSON is pure-serializable** — runtime fields are stripped on serialize, and `validateWorkflow` (Zod) gates every save and import.
- `tsc --noEmit` clean. `vite build` clean (1793 modules, 437 KB JS / 130 KB gzipped).

---

## Final file structure

```
package.json, vite.config.ts, tsconfig.json, tailwind.config.js,
postcss.config.js, index.html, .gitignore

src/
├── main.tsx                          # React 18 entry
├── app/
│   └── App.tsx                       # 3-column shell + RunPanel mount
│
├── workflow/
│   ├── types.ts                      # WorkflowDoc, FieldSchema, NodeRegistry…
│   ├── nodeRegistry.ts               # 13 node types (Start + 12 user-facing)
│   ├── defaultWorkflow.ts            # createEmptyWorkflow, createStartNode
│   ├── optionSources.ts              # Named option sources for select fields
│   ├── variableContext.ts            # `{{path}}` resolver + getAvailableVariables
│   ├── validation.ts                 # Zod schema + semantic checks + dedupe
│   ├── serialize.ts                  # Strip RF runtime fields / retrofit Start
│   ├── storage.ts                    # localStorage + file IO
│   └── execution/
│       ├── resolveVariables.ts       # resolveDeep + facade
│       ├── nodeExecutors.ts          # One mock executor per node type
│       ├── executor.ts               # startExecution / resumeExecution / tick
│       └── setByPath.ts              # Mutating dot-path setter
│
├── store/
│   └── workflowStore.ts              # Zustand: doc, selection, RF integration,
│                                     #   save/load/run/import/export, edge ops
│
├── utils/
│   └── path.ts                       # getByPath
│
├── components/
│   ├── layout/
│   │   ├── Header.tsx                # Title input + status banner + actions
│   │   ├── NodePalette.tsx           # Registry-driven, draggable, grouped
│   │   └── PropertiesPanel.tsx       # Schema-driven; null when nothing selected
│   ├── canvas/
│   │   ├── WorkflowCanvas.tsx        # ReactFlow + DnD drop + minimap + controls
│   │   ├── DynamicWorkflowNode.tsx   # The single RF node type
│   │   └── AdjustableEdge.tsx        # Bezier-through-bend custom edge
│   ├── forms/
│   │   ├── DynamicFormRenderer.tsx
│   │   ├── FieldRenderer.tsx         # Switch on FieldType (18 cases)
│   │   ├── Section.tsx               # Collapsible (panel + nested variants)
│   │   ├── ExpressionInput.tsx       # Text + caret-aware variable insertion
│   │   ├── VariableChip.tsx
│   │   ├── VariablePicker.tsx        # Portal popover, viewport-aware
│   │   ├── inputs.ts                 # Shared Tailwind class strings
│   │   ├── useField.ts
│   │   └── fields/
│   │       ├── KeyValueListField.tsx
│   │       ├── MessageListField.tsx
│   │       ├── MappingListField.tsx
│   │       ├── ConditionBuilder.tsx  # blocks + flat shapes
│   │       ├── SchemaBuilder.tsx
│   │       ├── AccordionField.tsx    # nested fields (recursive)
│   │       ├── ScriptRunner.tsx      # Test button + sandboxed run
│   │       └── HandoffListField.tsx  # Autosuggest target node + auto-edge
│   ├── run/
│   │   └── RunPanel.tsx              # Portal modal: log → pause → output
│   └── ui/
│       ├── Button.tsx
│       ├── Icon.tsx                  # IconName → lucide component map
│       └── colorTokens.ts            # ICON_BG, ICON_BG_LARGE, MINIMAP_COLORS
│
└── styles/
    └── globals.css                   # Tailwind + RF tweaks + scrollbar polish

feeding_files/                        # Original reference material (untouched)
├── plan_prompt/WORKFLOW_BUILDER_PROMPT.md
├── json_files/*.json
└── ui_images/*.png
```

---

## How to run

From the repo root:

```bash
npm install        # one-time
npm run dev        # http://localhost:5173
npm run typecheck  # tsc -b --noEmit
npm run build      # production build (writes dist/)
npm run preview    # preview the production bundle
```

Quick tour after opening:

1. **Build** — drag any node from the left palette onto the canvas. The Start node is always present.
2. **Edit** — click a node to open the right properties panel. Every section/field renders from `nodeRegistry.ts`.
3. **Variables** — click the `{ x }` button on any expression field. The picker lists `system.*`, `runtime.*`, and `nodes.<name>.result.<field>` derived from the workflow's other nodes.
4. **Save / Export / Import** — top-right toolbar. Save validates and writes to localStorage. Export downloads `<slug>.json`. Import replaces the doc.
5. **Run** — Run button opens the simulation modal. Human Input / Approval pause for input; Output terminates with the final JSON.
6. **Edges** — connect handles by dragging. Click an edge to select. Hover for the delete `✕`. Drag the small bend dot to bend the wire freely. Drag an endpoint to reconnect; drop on empty canvas to delete.

---

## Final feature coverage

- **13 node types** — Start (auto, protected) + LLM, Agent, External Agent, Script, HTTP Request, Guardrail, Rule, Sub Flow, Variable Update, Output, Approval, Human Input.
- **18 field types** — text/textarea/select/multi-select/switch/number/code/json + 5 list builders + condition-builder (blocks/flat) + schema-builder + accordion-section + script-runner + variable-reference (+ list) + handoff-list.
- **Variable system** — resolver (string + value + deep), portal picker, chip preview, ExpressionInput wired into 7 surfaces.
- **Persistence** — auto-load + save to localStorage, JSON export/import, schema-validated everywhere.
- **Validation** — Zod + unique names + edge endpoint existence + exactly-one-Start.
- **Runtime simulation** — synchronous traversal from Start, mock executors for all 13 types, Human Input/Approval pause modals, Output termination, error surfacing.
- **Agent handoffs** — autosuggest combobox, auto-edge creation, orange-tinted handle rows, edge cleanup on remove.
- **Edges** — endpoint reconnect (drag endpoint), wire path adjustment (drag the bend through any direction), delete-on-hover, drag-to-empty deletes.
- **Visual polish** — arrowed edges, color-coded MiniMap, hover/focus states, soft scrollbars, OpenType font features, brand-blue selection ring + glow.

Built collaboratively over Phases 1 → 8 plus the post-Phase iterations
covering Start-node integration, handoff-tool semantics, autosuggest UX,
edge reconnect, free-form wire bending, and edge-delete affordance.
