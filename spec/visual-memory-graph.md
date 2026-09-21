# Visual Memory Graph Spec

## Objective
Add a visual graph representation to the Mnemos control-plane Memory panel so the currently selected memory can be inspected as a node with its strongest related memories shown as connected neighbors.

This feature should improve at-a-glance understanding of graph context without changing the underlying memory model or inspectability API.

## Current codebase context
- The control plane already exposes a Memory panel in `mnemos/ui/index.html`, `mnemos/ui/app.js`, and `mnemos/ui/styles.css`.
- `GET /api/memory/{chunk_id}` already returns the full inspection payload from `build_chunk_inspection(...)`.
- That payload already includes a `graph` object with:
  - `present`
  - `neighbor_count`
  - `neighbors[]` entries containing `id`, `weight`, and `content_preview`
- The UI stack is static HTML + vanilla JS + CSS. There is no frontend build pipeline or component framework in the repo.

## Scope
### In scope
- Render a visual graph inside the Memory detail panel for the selected memory.
- Show the selected memory as the center node.
- Show each returned neighbor as an outer node.
- Use existing graph data only; no backend schema changes are required.
- Keep the current textual detail sections intact so the graph augments, not replaces, inspectability.
- Make the graph responsive and readable on narrow viewports.

### Out of scope
- No new persistence layer or graph database changes.
- No multi-hop traversal or full-network visualization.
- No editing, dragging, or node creation in the UI.
- No third-party visualization dependency unless the implementation proves the vanilla approach is insufficient.
- No changes to the inspectability payload contract unless a later implementation discovers a hard limit.

## User experience
### Primary flow
1. The user opens the Mnemos UI via `mnemos ui`.
2. The Memory panel loads recent chunks.
3. The user selects a memory.
4. The detail view renders:
   - memory metadata
   - revision history
   - a visual graph for the selected memory
5. The graph shows the center memory and its top neighbors.

### Graph behavior
- The selected chunk should appear visually emphasized at the center.
- Neighbor nodes should be arranged around the center in a stable layout.
- Edge styling should reflect relationship strength, with higher weights appearing more prominent.
- Hovering a node should expose the node id and a short preview.
- Clicking a neighbor should select that memory if it is available through the existing detail endpoint; otherwise the click should at minimum not break the current view.

### Empty and fallback states
- If `graph.present` is false or `neighbor_count` is zero, the panel should show a clear empty-state message such as “No graph neighbors yet.”
- The existing textual graph summary should remain visible even when the visual graph is empty.
- If the graph cannot be rendered for any reason, the rest of the memory detail panel must still load.

## Technical design
### Data source
Use the existing `/api/memory/{chunk_id}` response as the only source of truth for the detail view.

Expected graph shape:
```json
{
  "graph": {
    "present": true,
    "neighbor_count": 1,
    "neighbors": [
      {
        "id": "chunk-456",
        "weight": 0.91,
        "content_preview": "FastAPI services deploy with Docker..."
      }
    ]
  }
}
```

### Rendering approach
Implement the graph with native DOM/SVG primitives in `mnemos/ui/app.js`.

Recommended layout rules:
- center node rendered as a large circle or pill in the middle of a fixed-aspect graph container
- neighbors distributed in a radial layout around the center
- edge stroke width and opacity scaled by `weight`
- labels truncated to fit, with full details available via tooltip or accessible text

Why this approach:
- keeps the control plane dependency-free
- fits the repo’s current static UI model
- avoids introducing a bundler or charting library for a small inspectability surface

### Accessibility and responsiveness
- Every node should have an accessible label.
- The graph should have a textual summary or fallback for screen readers and small screens.
- On narrow widths, the graph should collapse into a vertical stack while preserving the same data.
- Color must not be the only indicator of relationship strength; use stroke width, label placement, and/or node emphasis too.

## File impact
### `mnemos/ui/index.html`
- Add a dedicated graph container inside the Memory detail section.
- Preserve the existing memory list and detail layout.

### `mnemos/ui/app.js`
- Add a dedicated rendering function for the memory graph.
- Reuse the existing detail fetch from `/api/memory/{chunk_id}`.
- Keep the current detail sections and append the visual graph section.
- If a neighbor is clicked, reuse the same detail-loading path used for primary memory selection.

### `mnemos/ui/styles.css`
- Add styles for the graph container, node chips/circles, edge lines, hover/focus states, and empty state.
- Add responsive rules so the graph remains legible on mobile widths.

### `tests/test_ui_server.py`
- Add or extend tests to confirm the UI still serves the detail endpoint and that graph data is present in the memory inspection payload used by the UI.
- Add at least one static-asset assertion that the graph section markup or renderer hook exists in the served UI bundle.

## Success criteria
1. Selecting a memory with graph neighbors shows a visible graph in the detail pane.
2. The graph uses the existing `graph.neighbors` payload and does not require backend changes.
3. The center node is clearly the selected memory; neighbors are clearly associated to it.
4. Empty graphs render a graceful fallback instead of a broken layout.
5. Existing memory detail sections still render exactly as before, with the graph added as an enhancement.
6. The UI remains usable on narrow screens.
7. The feature works with the current static UI stack and does not introduce a build step.

## Validation expectations
### Automated
- Run the relevant Python tests:
  - `pytest tests/test_inspectability.py`
  - `pytest tests/test_ui_server.py`
- Ensure any added test assertions cover:
  - graph payload presence
  - detail endpoint behavior
  - explicit graph-rendering hooks or markup in the UI assets
- Keep the existing route validation guardrails intact: no ad-hoc POST route bypasses and no schema-coverage regressions.

### Manual browser smoke check
- Start the UI with `mnemos ui`.
- Store or select at least one memory with a known neighbor relationship.
- Confirm the graph renders in the Memory detail panel.
- Verify hover/focus reveals readable labels or previews.
- Verify a memory with no neighbors shows the fallback state.

### Regression check
- Confirm the Memory panel still shows scope, provenance, revision history, and raw metadata after the graph is added.
- Confirm the detail view still loads if graph rendering fails or if the memory has no neighbors.
