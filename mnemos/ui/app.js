const state = {
  settings: null,
  view: null,
  selectedMemoryId: null,
  currentPage: "main",
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => {
    switch (character) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      case "'":
        return "&#39;";
      default:
        return character;
    }
  });
}

function truncateText(value, maxLength = 96) {
  const text = String(value ?? "").trim();
  if (!text) {
    return "No preview available.";
  }
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, Math.max(maxLength - 1, 1)).trimEnd()}…`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const contentType = response.headers.get("Content-Type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    throw new Error(typeof payload === "string" ? payload : JSON.stringify(payload));
  }
  return payload;
}

function statusPill(label, tone = "pass") {
  return `<span class="pill ${tone}">${label}</span>`;
}

function activeProvider() {
  return document.getElementById("llm-provider").value;
}

function providerSettings(settings) {
  const provider = activeProvider();
  return settings.providers?.[provider] || {};
}

function refreshProviderFields() {
  if (!state.settings) {
    return;
  }
  const provider = providerSettings(state.settings);
  document.getElementById("provider-url").value = provider.base_url || "";
  document.getElementById("provider-key").value = "";
  document.getElementById("provider-key-status").textContent = provider.configured
    ? "A secret is already configured here. Leave blank to keep it."
    : "No secret saved yet for this provider.";
}

function fillForm(view) {
  state.view = view;
  state.settings = view.settings;
  const { settings, paths, warnings } = view;
  document.getElementById("onboarding-mode").value = settings.onboarding?.mode || "dev";
  document.getElementById("preferred-host").value =
    settings.onboarding?.preferred_host || "claude-code";
  document.getElementById("llm-provider").value = settings.llm?.provider || "mock";
  document.getElementById("llm-model").value = settings.llm?.model || "";
  document.getElementById("embedding-provider").value = settings.embedding?.provider || "simple";
  document.getElementById("embedding-model").value = settings.embedding?.model || "";
  document.getElementById("store-type").value = settings.storage?.type || "sqlite";
  document.getElementById("sqlite-path").value = settings.storage?.sqlite_path || "";
  refreshProviderFields();

  const heroStatus = document.getElementById("hero-status");
  heroStatus.innerHTML = [
    statusPill(`Global: ${paths.global_config}`),
    paths.project_config ? statusPill(`Project: ${paths.project_config}`, "warn") : "",
    ...warnings.map((warning) => statusPill(warning, "warn")),
  ].join("");
}

function collectPayload() {
  const provider = activeProvider();
  return {
    onboarding: {
      mode: document.getElementById("onboarding-mode").value,
      preferred_host: document.getElementById("preferred-host").value,
    },
    llm: {
      provider,
      model: document.getElementById("llm-model").value.trim(),
    },
    embedding: {
      provider: document.getElementById("embedding-provider").value,
      model: document.getElementById("embedding-model").value.trim(),
    },
    storage: {
      type: document.getElementById("store-type").value,
      sqlite_path: document.getElementById("sqlite-path").value.trim(),
    },
    providers: {
      [provider]: {
        base_url: document.getElementById("provider-url").value.trim(),
        api_key: document.getElementById("provider-key").value,
      },
    },
  };
}

const memoryGraphRenderer = (() => {
  const FLOW_CANVAS = { width: 1000, height: 640 };
  const NODE_DIMENSIONS = {
    center: { width: 280, height: 126 },
    neighbor: { width: 224, height: 108 },
  };
  const EDGE_STYLE = { type: "straight" };

  let mountedApp = null;

  function destroy() {
    if (mountedApp) {
      mountedApp.unmount();
      mountedApp = null;
    }
  }

  function graphState(payload) {
    const graph = payload?.graph || {};
    const graphPresent = Boolean(graph.present);
    const neighbors = Array.isArray(graph.neighbors)
      ? graph.neighbors.filter((neighbor) => neighbor && neighbor.id)
      : [];

    if (!graphPresent) {
      return {
        kind: "unavailable",
        present: false,
        neighbors,
        neighborCount: Number(graph.neighbor_count || 0),
        status: "Graph context unavailable for this memory.",
        note: "This usually means the chunk was not hydrated into the current in-memory snapshot.",
      };
    }

    if (neighbors.length === 0) {
      return {
        kind: "empty",
        present: true,
        neighbors,
        neighborCount: Number(graph.neighbor_count || 0),
        status: "Graph context is present, but no neighbors surfaced yet.",
        note: "The selected memory is hydrated, but nothing was strong enough to render as a neighbor.",
      };
    }

    return {
      kind: "populated",
      present: true,
      neighbors,
      neighborCount: neighbors.length,
      status: `Showing ${neighbors.length} neighbor${neighbors.length === 1 ? "" : "s"}.`,
      note: "Hover or focus a node to inspect its id and preview. Click a neighbor to load that memory.",
    };
  }

  function layoutNodes(payload) {
    const model = graphState(payload);
    const centerId = payload?.id || "selected-memory";
    const centerPreview = truncateText(payload?.content, 96);
    const centerNode = {
      id: centerId,
      type: "memoryNode",
      position: {
        x: (FLOW_CANVAS.width - NODE_DIMENSIONS.center.width) / 2,
        y: (FLOW_CANVAS.height - NODE_DIMENSIONS.center.height) / 2,
      },
      draggable: false,
      connectable: false,
      selectable: true,
      data: {
        kind: "center",
        kicker: "Center",
        label: payload?.id || "selected-memory",
        preview: truncateText(payload?.content, 88),
        title: `Selected memory ${payload?.id || "selected-memory"}. ${centerPreview}`,
        ariaLabel: `Selected memory ${payload?.id || "selected-memory"}. ${centerPreview}`,
        memoryId: centerId,
      },
    };

    const nodes = [centerNode];
    const edges = [];

    if (model.present && model.neighbors.length > 0) {
      const radius = model.neighbors.length === 1 ? 250 : Math.min(330, 190 + model.neighbors.length * 18);
      model.neighbors.forEach((neighbor, index) => {
        const angle = (Math.PI * 2 * index) / model.neighbors.length - Math.PI / 2;
        const weight = Number(neighbor.weight);
        const normalizedWeight = Number.isFinite(weight) ? Math.min(Math.max(weight, 0.12), 1) : 0.5;
        const nodeX = FLOW_CANVAS.width / 2 + radius * Math.cos(angle) - NODE_DIMENSIONS.neighbor.width / 2;
        const nodeY = FLOW_CANVAS.height / 2 + radius * Math.sin(angle) - NODE_DIMENSIONS.neighbor.height / 2;
        const preview = truncateText(neighbor.content_preview || neighbor.content || "", 92);
        const nodeId = neighbor.id;

        nodes.push({
          id: nodeId,
          type: "memoryNode",
          position: { x: nodeX, y: nodeY },
          draggable: false,
          connectable: false,
          selectable: true,
          data: {
            kind: "neighbor",
            kicker: "Neighbor",
            label: nodeId,
            preview,
            title: `Neighbor memory ${nodeId}. ${preview}`,
            ariaLabel: `Neighbor memory ${nodeId}. ${preview}`,
            memoryId: nodeId,
          },
        });

        edges.push({
          id: `e-${centerId}-${nodeId}`,
          source: centerId,
          target: nodeId,
          type: EDGE_STYLE.type,
          selectable: false,
          focusable: false,
          style: {
            strokeWidth: 1.4 + normalizedWeight * 4,
            opacity: 0.26 + normalizedWeight * 0.58,
          },
        });
      });
    }

    return { ...model, centerId, nodes, edges };
  }

  function renderFallback(model, payload) {
    if (model.kind === "unavailable") {
      return `
        <div class="memory-graph memory-graph-empty" aria-live="polite">
          <p class="graph-empty-state">${escapeHtml(model.status)}</p>
          <p class="graph-empty-note">${escapeHtml(model.note)}</p>
        </div>
      `;
    }

    const centerPreview = truncateText(payload?.content, 88);
    return `
      <div class="memory-flow-shell memory-flow-shell-fallback">
        <div class="graph-summary" aria-live="polite">
          <p class="graph-status">${escapeHtml(model.status)}</p>
          <p class="graph-hint">${escapeHtml(model.note)}</p>
        </div>
        <div class="memory-flow-stage memory-flow-stage-fallback">
          <div class="memory-flow-fallback-card memory-flow-fallback-center">
            <span class="graph-node-kicker">Center</span>
            <strong class="graph-node-id">${escapeHtml(payload?.id || "selected-memory")}</strong>
            <span class="graph-node-preview">${escapeHtml(centerPreview)}</span>
          </div>
        </div>
      </div>
    `;
  }

  function mount(root, payload, onNodeSelect) {
    destroy();
    if (!root) {
      return;
    }

    const model = layoutNodes(payload);
    const vueApi = window.Vue;
    const vueFlowApi = window.VueFlowCore;

    if (!vueApi || !vueFlowApi || !vueFlowApi.VueFlow) {
      root.innerHTML = renderFallback(model, payload);
      return;
    }

    if (model.kind === "unavailable") {
      root.innerHTML = renderFallback(model, payload);
      return;
    }

    const { createApp, markRaw } = vueApi;
    const { VueFlow, Handle } = vueFlowApi;

    const Position = vueFlowApi.Position;

    const MemoryNode = {
      components: { Handle },
      props: {
        data: {
          type: Object,
          required: true,
        },
        selected: {
          type: Boolean,
          default: false,
        },
      },
      setup() {
        return { Position };
      },
      template: `
        <div class="memory-flow-node" :class="['memory-flow-node--' + data.kind, { 'is-selected': selected }]">
          <Handle type="target" :position="Position.Left" class="graph-handle graph-handle-target" />
          <button
            class="memory-flow-node-card"
            type="button"
            :title="data.title"
            :aria-label="data.ariaLabel"
          >
            <span class="graph-node-kicker">{{ data.kicker }}</span>
            <strong class="graph-node-id">{{ data.label }}</strong>
            <span class="graph-node-preview">{{ data.preview }}</span>
          </button>
          <Handle type="source" :position="Position.Right" class="graph-handle graph-handle-source" />
        </div>
      `,
    };

    const nodeTypes = {
      memoryNode: markRaw(MemoryNode),
    };

    mountedApp = createApp({
      components: { VueFlow },
      data() {
        return {
          nodes: model.nodes,
          edges: model.edges,
          nodeTypes,
          stateMessage: model.status,
          stateHint: model.note,
          graphKind: model.kind,
          centerId: model.centerId,
        };
      },
      methods: {
        handleNodeClick(_event, node) {
          const candidate = node?.id ? node : _event?.node || null;
          const memoryId = candidate?.id || candidate?.data?.memoryId;
          if (!memoryId || typeof onNodeSelect !== "function") {
            return;
          }
          onNodeSelect(memoryId);
        },
      },
      template: `
        <div class="memory-flow-shell" :class="'memory-flow-shell-' + graphKind">
          <div v-if="graphKind === 'empty' || graphKind === 'populated'" class="graph-summary" aria-live="polite">
            <p class="graph-status">{{ stateMessage }}</p>
            <p class="graph-hint">{{ stateHint }}</p>
          </div>
          <div class="memory-flow-stage">
            <VueFlow
              class="memory-flow"
              :nodes="nodes"
              :edges="edges"
              :node-types="nodeTypes"
              :nodes-draggable="false"
              :nodes-connectable="false"
              :elements-selectable="false"
              :pan-on-drag="false"
              :zoom-on-scroll="false"
              :zoom-on-double-click="false"
              :prevent-scrolling="true"
              :fit-view="true"
              :min-zoom="0.4"
              :max-zoom="1.4"
              @node-click="handleNodeClick"
            />
          </div>
          <p v-if="graphKind === 'empty'" class="graph-empty-note graph-empty-note-inline">
            This graph is hydrated, but no connected neighbors were returned yet.
          </p>
        </div>
      `,
    });

    mountedApp.mount(root);
  }

  return { destroy, mount };
})();

const globalMemoryGraphRenderer = (() => {
  let mountedApp = null;

  function destroy() {
    if (mountedApp) {
      mountedApp.unmount();
      mountedApp = null;
    }
  }

  function compactId(value, maxLength = 18) {
    const raw = String(value || "");
    if (raw.length <= maxLength) {
      return raw;
    }
    return `${raw.slice(0, 8)}…${raw.slice(-6)}`;
  }

  function seededUnit(seedText) {
    let hash = 2166136261;
    const text = String(seedText || "seed");
    for (let i = 0; i < text.length; i += 1) {
      hash ^= text.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    return ((hash >>> 0) % 1_000_000) / 1_000_000;
  }

  function layoutGlobalGraph(payload) {
    const rawNodes = Array.isArray(payload?.nodes) ? payload.nodes.filter((node) => node && node.id) : [];
    if (rawNodes.length === 0) {
      return { nodes: [], edges: [] };
    }

    const nodeMetaById = new Map();
    rawNodes.forEach((node) => {
      const scopeLabel = `${node.scope || "global"}${node.scope_id ? `:${node.scope_id}` : ""}`;
      nodeMetaById.set(node.id, {
        scopeLabel,
        title: `Memory ${node.id} (${scopeLabel}). ${truncateText(node.content, 120)}`,
        ariaLabel: `Memory ${node.id} (${scopeLabel}). ${truncateText(node.content, 120)}`,
      });
    });

    const knownIds = new Set(rawNodes.map((node) => node.id));
    const rawEdges = (Array.isArray(payload?.edges) ? payload.edges : []).filter(
      (edge) => edge && edge.source && edge.target && knownIds.has(edge.source) && knownIds.has(edge.target),
    );

    const nodeCount = rawNodes.length;
    const canvasWidth = Math.max(2200, Math.ceil(Math.sqrt(nodeCount)) * 280);
    const canvasHeight = Math.max(1400, Math.ceil(Math.sqrt(nodeCount)) * 220);

    const simulationNodes = rawNodes.map((node) => ({
      id: node.id,
      x: 80 + seededUnit(`${node.id}:x`) * (canvasWidth - 160),
      y: 80 + seededUnit(`${node.id}:y`) * (canvasHeight - 160),
    }));

    const indexById = new Map(simulationNodes.map((node, index) => [node.id, index]));

    const simulationEdges = rawEdges.map((edge) => {
      const weight = Number(edge.weight);
      const normalizedWeight = Number.isFinite(weight) ? Math.min(Math.max(weight, 0.12), 1) : 0.5;
      return {
        sourceIndex: indexById.get(edge.source),
        targetIndex: indexById.get(edge.target),
        weight: normalizedWeight,
        source: edge.source,
        target: edge.target,
        id: edge.id || `e-${edge.source}-${edge.target}`,
      };
    });

    const area = canvasWidth * canvasHeight;
    const k = Math.sqrt(area / Math.max(nodeCount, 1));
    const iterations = Math.min(220, Math.max(90, Math.floor(44 + nodeCount * 0.5)));
    let temperature = Math.min(canvasWidth, canvasHeight) * 0.11;

    for (let iteration = 0; iteration < iterations; iteration += 1) {
      const displacement = simulationNodes.map(() => ({ x: 0, y: 0 }));

      for (let i = 0; i < simulationNodes.length; i += 1) {
        for (let j = i + 1; j < simulationNodes.length; j += 1) {
          const a = simulationNodes[i];
          const b = simulationNodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          const distance = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01);
          const force = (k * k) / distance;

          dx /= distance;
          dy /= distance;

          displacement[i].x += dx * force;
          displacement[i].y += dy * force;
          displacement[j].x -= dx * force;
          displacement[j].y -= dy * force;
        }
      }

      simulationEdges.forEach((edge) => {
        const i = edge.sourceIndex;
        const j = edge.targetIndex;
        if (typeof i !== "number" || typeof j !== "number") {
          return;
        }

        const a = simulationNodes[i];
        const b = simulationNodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        const distance = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01);
        const force = ((distance * distance) / k) * (0.34 + edge.weight * 0.96);

        dx /= distance;
        dy /= distance;

        displacement[i].x -= dx * force;
        displacement[i].y -= dy * force;
        displacement[j].x += dx * force;
        displacement[j].y += dy * force;
      });

      simulationNodes.forEach((node, index) => {
        const dx = displacement[index].x;
        const dy = displacement[index].y;
        const dispLength = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01);
        const limited = Math.min(temperature, dispLength);

        node.x += (dx / dispLength) * limited;
        node.y += (dy / dispLength) * limited;

        node.x = Math.min(canvasWidth - 64, Math.max(64, node.x));
        node.y = Math.min(canvasHeight - 64, Math.max(64, node.y));
      });

      temperature *= 0.956;
    }

    const flowNodes = simulationNodes.map((node) => {
      const meta = nodeMetaById.get(node.id);
      const isSelected = state.selectedMemoryId === node.id;
      return {
        id: node.id,
        type: "globalMemoryNode",
        position: {
          x: node.x - 78,
          y: node.y - 24,
        },
        draggable: true,
        connectable: false,
        selectable: true,
        data: {
          kind: isSelected ? "center" : "neighbor",
          scope: meta?.scopeLabel || "global",
          label: compactId(node.id),
          title: meta?.title || `Memory ${node.id}`,
          ariaLabel: meta?.ariaLabel || `Memory ${node.id}`,
          memoryId: node.id,
        },
      };
    });

    const flowEdges = simulationEdges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "smoothstep",
      selectable: false,
      focusable: false,
      style: {
        strokeWidth: 0.8 + edge.weight * 2.4,
        opacity: 0.14 + edge.weight * 0.5,
      },
    }));

    return { nodes: flowNodes, edges: flowEdges };
  }

  function renderFallback(root, message) {
    root.innerHTML = `
      <div class="memory-graph memory-graph-empty" aria-live="polite">
        <p class="graph-empty-state">${escapeHtml(message)}</p>
      </div>
    `;
  }

  function mount(root, payload, onNodeSelect) {
    destroy();
    if (!root) {
      return;
    }

    const vueApi = window.Vue;
    const vueFlowApi = window.VueFlowCore;
    if (!vueApi || !vueFlowApi || !vueFlowApi.VueFlow) {
      renderFallback(root, "Vue Flow runtime unavailable.");
      return;
    }

    const { nodes, edges } = layoutGlobalGraph(payload);
    if (!nodes.length) {
      renderFallback(root, "No memory nodes found yet.");
      return;
    }

    const { createApp, markRaw } = vueApi;
    const { VueFlow, Handle } = vueFlowApi;
    const Position = vueFlowApi.Position;

    const backgroundApi = window.vueFlowBackground;
    const controlsApi = window.vueFlowControls;
    const minimapApi = window.vueFlowMinimap;

    const Background = backgroundApi?.Background || null;
    const Controls = controlsApi?.Controls || null;
    const MiniMap = minimapApi?.MiniMap || null;

    const MemoryNode = {
      components: { Handle },
      props: {
        data: {
          type: Object,
          required: true,
        },
        selected: {
          type: Boolean,
          default: false,
        },
      },
      setup() {
        return { Position };
      },
      template: `
        <div class="memory-flow-node" :class="['memory-flow-node--' + data.kind, { 'is-selected': selected }]">
          <Handle type="target" :position="Position.Left" class="graph-handle graph-handle-target" />
          <button
            class="memory-flow-node-card memory-flow-node-card-compact"
            type="button"
            :title="data.title"
            :aria-label="data.ariaLabel"
          >
            <strong class="graph-node-id">{{ data.label }}</strong>
            <span class="graph-node-kicker">{{ data.scope }}</span>
          </button>
          <Handle type="source" :position="Position.Right" class="graph-handle graph-handle-source" />
        </div>
      `,
    };

    const nodeTypes = {
      globalMemoryNode: markRaw(MemoryNode),
    };

    const components = { VueFlow };
    if (Background) {
      components.Background = Background;
    }
    if (Controls) {
      components.Controls = Controls;
    }
    if (MiniMap) {
      components.MiniMap = MiniMap;
    }

    mountedApp = createApp({
      components,
      data() {
        return {
          nodes,
          edges,
          nodeTypes,
          hasBackground: Boolean(Background),
          hasControls: Boolean(Controls),
          hasMiniMap: Boolean(MiniMap),
        };
      },
      methods: {
        handleNodeClick(_event, node) {
          const candidate = node?.id ? node : _event?.node || null;
          const memoryId = candidate?.id || candidate?.data?.memoryId;
          if (!memoryId || typeof onNodeSelect !== "function") {
            return;
          }
          onNodeSelect(memoryId);
        },
      },
      template: `
        <div class="memory-flow-shell memory-flow-shell-populated">
          <div class="memory-flow-stage">
            <VueFlow
              class="memory-flow memory-flow-global"
              :nodes="nodes"
              :edges="edges"
              :node-types="nodeTypes"
              :nodes-draggable="true"
              :nodes-connectable="false"
              :elements-selectable="true"
              :pan-on-drag="true"
              :zoom-on-scroll="true"
              :zoom-on-double-click="true"
              :prevent-scrolling="false"
              :fit-view="true"
              :fit-view-on-init="true"
              :min-zoom="0.12"
              :max-zoom="1.8"
              @node-click="handleNodeClick"
            >
              <Background v-if="hasBackground" :gap="20" pattern-color="rgba(17, 94, 89, 0.18)" />
              <MiniMap v-if="hasMiniMap" />
              <Controls v-if="hasControls" position="top-right" />
            </VueFlow>
          </div>
        </div>
      `,
    });

    mountedApp.mount(root);
  }

  return { destroy, mount };
})();

async function showGraphPage() {
  state.currentPage = "graph";
  memoryGraphRenderer.destroy();
  document.getElementById("main-page")?.classList.add("is-hidden");
  document.getElementById("graph-page")?.classList.remove("is-hidden");

  if (window.location.hash !== "#graph") {
    window.location.hash = "graph";
  }

  await refreshGlobalGraphPage();
}

function showMainPage() {
  state.currentPage = "main";
  globalMemoryGraphRenderer.destroy();
  document.getElementById("graph-page")?.classList.add("is-hidden");
  document.getElementById("main-page")?.classList.remove("is-hidden");

  if (window.location.hash === "#graph") {
    history.replaceState(null, "", window.location.pathname);
  }
}

async function refreshGlobalGraphPage() {
  const summary = document.getElementById("graph-page-summary");
  const graphRoot = document.getElementById("global-graph-root");
  if (!summary || !graphRoot) {
    return;
  }

  summary.textContent = "Loading graph…";
  globalMemoryGraphRenderer.destroy();

  try {
    const payload = await api("/api/graph");
    const nodeCount = Number(payload.node_count || 0);
    const edgeCount = Number(payload.edge_count || 0);
    summary.textContent = `${nodeCount} nodes · ${edgeCount} edges · force-directed layout · drag nodes, scroll to zoom, click a node to open details`;

    globalMemoryGraphRenderer.mount(graphRoot, payload, async (memoryId) => {
      if (!memoryId) {
        return;
      }
      await selectMemory(memoryId);
      showMainPage();
    });
  } catch (error) {
    summary.textContent = "Failed to load graph.";
    graphRoot.innerHTML = `
      <div class="memory-graph memory-graph-empty" aria-live="polite">
        <p class="graph-empty-state">${escapeHtml(error.message)}</p>
      </div>
    `;
  }
}

async function syncPageFromHash() {
  if (window.location.hash === "#graph") {
    await showGraphPage();
    return;
  }
  showMainPage();
}

function renderMemoryGraphSection(payload) {
  const graph = payload.graph || {};
  const graphPresent = Boolean(graph.present);
  const neighborCount = Number(graph.neighbor_count || 0);
  const graphNeighborList = !graphPresent
    ? "<li>Graph context unavailable for this memory.</li>"
    : neighborCount === 0
      ? "<li>No graph neighbors yet.</li>"
      : (graph.neighbors || [])
          .filter((neighbor) => neighbor && neighbor.id)
          .map(
            (neighbor) => `
              <li>
                <span>${escapeHtml(neighbor.id)}</span>
                <span>${escapeHtml(String(neighbor.weight))}</span>
              </li>
            `,
          )
          .join("");

  return `
    <div class="detail-section">
      <p class="section-kicker">Graph</p>
      <ul class="detail-list graph-summary-list">
        <li><strong>Present:</strong> ${graphPresent ? "yes" : "no"}</li>
        <li><strong>Neighbors:</strong> ${neighborCount}</li>
      </ul>
      <div class="graph-stage graph-stage-inline">
        <div id="memory-graph-root" class="memory-graph-root"></div>
      </div>
      <ul class="detail-list graph-neighbor-list">${graphNeighborList}</ul>
    </div>
  `;
}

async function refreshSettings() {
  const view = await api("/api/settings");
  fillForm(view);
}

async function refreshHealth() {
  const report = await api("/api/health");
  const summary = document.getElementById("health-summary");
  const checks = document.getElementById("health-checks");
  summary.innerHTML = [
    statusPill(
      `Status: ${report.status}`,
      report.status === "ready" ? "pass" : report.status === "degraded" ? "warn" : "fail",
    ),
    statusPill(`Profile: ${report.profile}`),
    statusPill(`Store: ${report.store_type}`),
    statusPill(`LLM: ${report.llm_provider}`),
    statusPill(`Embedding: ${report.embedding_provider}`),
  ].join("");
  checks.innerHTML = report.checks
    .map(
      (check) => `
      <li>
        <span class="check-name">${check.name}</span>
        <span>${check.message}</span>
      </li>
    `,
    )
    .join("");
}

async function refreshMemory() {
  const snapshot = await api("/api/memory");
  document.getElementById("memory-summary").textContent = `${snapshot.count} chunks currently visible from the configured store.`;
  if (!state.selectedMemoryId && snapshot.recent.length > 0) {
    state.selectedMemoryId = snapshot.recent[0].id;
  }
  document.getElementById("memory-list").innerHTML = snapshot.recent
    .map(
      (item) => `
      <button class="memory-item ${state.selectedMemoryId === item.id ? "selected" : ""}" data-memory-id="${item.id}">
        <div class="memory-meta">
          <span>${item.scope}${item.scope_id ? `:${item.scope_id}` : ""}</span>
          <span>accessed ${item.access_count}x</span>
          <span>${item.updated_at}</span>
        </div>
        <p>${item.content}</p>
      </button>
    `,
    )
    .join("");
  document.querySelectorAll(".memory-item").forEach((element) => {
    element.addEventListener("click", async () => {
      const memoryId = element.dataset.memoryId;
      if (!memoryId) {
        return;
      }
      await selectMemory(memoryId);
    });
  });
  if (state.selectedMemoryId) {
    await refreshMemoryDetail(state.selectedMemoryId);
  } else {
    document.getElementById("memory-detail").textContent = "No stored memories yet.";
  }
}

async function selectMemory(memoryId) {
  state.selectedMemoryId = memoryId;
  await refreshMemory();
}

async function refreshMemoryDetail(chunkId) {
  memoryGraphRenderer.destroy();
  try {
    const payload = await api(`/api/memory/${encodeURIComponent(chunkId)}`);
    const history = (payload.history || [])
      .map(
        (entry) => `
        <li>
          <span>v${entry.from_version} -> v${entry.to_version}</span>
          <span>${entry.changed_at}</span>
          <p>${entry.previous_content}</p>
          <p>${entry.new_content}</p>
        </li>
      `,
      )
      .join("");

    const detailContainer = document.getElementById("memory-detail");
    detailContainer.innerHTML = `
      <div class="detail-section">
        <p class="section-kicker">Scope</p>
        <h3>${payload.scope}${payload.scope_id ? `:${payload.scope_id}` : ""}</h3>
        <p>${payload.content}</p>
      </div>
      <div class="detail-section">
        <p class="section-kicker">Provenance</p>
        <ul class="detail-list">
          <li><strong>Stored by:</strong> ${payload.provenance?.stored_by || "unknown"}</li>
          <li><strong>Channel:</strong> ${payload.provenance?.ingest_channel || "n/a"}</li>
          <li><strong>Reason:</strong> ${payload.provenance?.encoding_reason || "n/a"}</li>
          <li><strong>Version:</strong> ${payload.version}</li>
          <li><strong>Access count:</strong> ${payload.access_count}</li>
        </ul>
      </div>
      <div class="detail-section">
        <p class="section-kicker">Revision History</p>
        <ul class="detail-list">${history || "<li>No rewrites yet.</li>"}</ul>
      </div>
      ${renderMemoryGraphSection(payload)}
      <div class="detail-section">
        <p class="section-kicker">Metadata</p>
        <pre class="console">${JSON.stringify(payload.metadata || {}, null, 2)}</pre>
      </div>
    `;

    const graphRoot = document.getElementById("memory-graph-root");
    memoryGraphRenderer.mount(graphRoot, payload, async (memoryId) => {
      if (!memoryId) {
        return;
      }
      if (memoryId === state.selectedMemoryId) {
        return;
      }
      await selectMemory(memoryId);
    });
  } catch (error) {
    memoryGraphRenderer.destroy();
    document.getElementById("memory-detail").textContent = error.message;
  }
}

async function save(scope) {
  const result = await api(`/api/settings/${scope}`, {
    method: "POST",
    body: JSON.stringify(collectPayload()),
  });
  document.getElementById("integration-preview").textContent = `Saved ${scope} settings to ${result.path}`;
  await refreshSettings();
  await refreshHealth();
}

async function importSetup() {
  const result = await api("/api/import", { method: "POST", body: "{}" });
  fillForm(result);
  document.getElementById("integration-preview").textContent =
    `Imported existing setup from: ${result.sources.join(", ") || "no external host config found"}`;
}

async function previewHost(host, action) {
  const result = await api(`/api/integrations/${host}/${action}`, {
    method: "POST",
    body: "{}",
  });
  document.getElementById("integration-preview").textContent = result.preview;
}

async function smokeTest() {
  const result = await api("/api/smoke", { method: "POST", body: "{}" });
  document.getElementById("integration-preview").textContent = JSON.stringify(result, null, 2);
}

function bindEvents() {
  document.getElementById("save-global").addEventListener("click", () => save("global"));
  document.getElementById("save-project").addEventListener("click", () => save("project"));
  document.getElementById("import-button").addEventListener("click", importSetup);
  document.getElementById("smoke-button").addEventListener("click", smokeTest);
  document.getElementById("refresh-health").addEventListener("click", refreshHealth);
  document.getElementById("refresh-memory").addEventListener("click", refreshMemory);
  document.getElementById("llm-provider").addEventListener("change", refreshProviderFields);

  const openGraphButton = document.getElementById("open-graph-page");
  if (openGraphButton) {
    openGraphButton.addEventListener("click", () => {
      showGraphPage();
    });
  }

  const closeGraphButton = document.getElementById("close-graph-page");
  if (closeGraphButton) {
    closeGraphButton.addEventListener("click", () => {
      showMainPage();
    });
  }

  const refreshGraphButton = document.getElementById("refresh-graph-page");
  if (refreshGraphButton) {
    refreshGraphButton.addEventListener("click", () => {
      refreshGlobalGraphPage();
    });
  }

  window.addEventListener("hashchange", () => {
    syncPageFromHash();
  });

  document.querySelectorAll(".preview-host").forEach((button) => {
    button.addEventListener("click", (event) => {
      const host = event.target.closest(".host-card").dataset.host;
      previewHost(host, "preview");
    });
  });
  document.querySelectorAll(".apply-host").forEach((button) => {
    button.addEventListener("click", (event) => {
      const host = event.target.closest(".host-card").dataset.host;
      previewHost(host, "apply");
    });
  });
}

async function init() {
  bindEvents();
  await refreshSettings();
  await refreshHealth();
  await refreshMemory();
  await syncPageFromHash();
}

init().catch((error) => {
  document.getElementById("integration-preview").textContent = error.message;
});
