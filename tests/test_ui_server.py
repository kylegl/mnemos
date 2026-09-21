"""
tests/test_ui_server.py — Local UI router tests.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

from mnemos.config import MnemosConfig, SpreadingConfig, SurprisalConfig
from mnemos.control_plane import ControlPlaneService
from mnemos.engine import MnemosEngine
from mnemos.types import MemoryChunk
from mnemos.utils.embeddings import SimpleEmbeddingProvider
from mnemos.utils.llm import MockLLMProvider
from pydantic import BaseModel

from mnemos.ui_server import MAX_REQUEST_BODY_BYTES, MnemosUiRouter
from mnemos.utils import SQLiteStore


def test_ui_router_serves_index_and_settings(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    index_response = router.handle("GET", "/", None)
    assert index_response.status == 200
    assert index_response.content_type == "text/html; charset=utf-8"
    assert b"Mnemos Control Plane" in index_response.body

    settings_response = router.handle("GET", "/api/settings", None)
    assert settings_response.status == 200
    payload = json.loads(settings_response.body.decode("utf-8"))
    assert "settings" in payload
    assert "paths" in payload


def test_ui_router_previews_cursor_integration(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    response = router.handle("POST", "/api/integrations/cursor/preview", b"{}")

    assert response.status == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["host"] == "cursor"
    assert "MNEMOS_CONFIG_PATH" in payload["preview"]


def test_ui_router_rejects_invalid_json_payload(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    response = router.handle("POST", "/api/settings/global", b"{not-json")

    assert response.status == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert "error" in payload


def test_ui_router_rejects_non_object_json_payload(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    response = router.handle("POST", "/api/settings/global", b"[]")

    assert response.status == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert "must be an object" in payload["error"]


def test_ui_router_rejects_unknown_settings_fields(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    response = router.handle(
        "POST",
        "/api/settings/global",
        json.dumps({"storage": {"type": "sqlite"}, "unknown": True}).encode("utf-8"),
    )

    assert response.status == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"] == "Request validation failed."


def test_ui_router_rejects_legacy_storage_fields(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    response = router.handle(
        "POST",
        "/api/settings/global",
        json.dumps({"storage": {"type": "sqlite", "qdrant_url": "http://localhost:6333"}}).encode(
            "utf-8"
        ),
    )

    assert response.status == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"] == "Request validation failed."


def test_ui_router_rejects_non_empty_import_payload(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    response = router.handle("POST", "/api/import", json.dumps({"force": True}).encode("utf-8"))

    assert response.status == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"] == "Request validation failed."


def test_ui_router_exposes_request_size_limit() -> None:
    assert MAX_REQUEST_BODY_BYTES == 1_048_576


def test_ui_router_dispatch_validated_post_helper_rejects_invalid_payload(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    class _Schema(BaseModel):
        value: int

    response = router._dispatch_validated_post(
        payload={"value": "nope"},
        schema=_Schema,
        handler=lambda _validated: {"ok": True},
    )

    assert response.status == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["error"] == "Request validation failed."


def test_ui_router_prevents_post_route_bypass_patterns() -> None:
    source = inspect.getsource(MnemosUiRouter.handle)

    # Guardrail: POST /api routes must be routed through centralized
    # schema mapping + `_dispatch_validated_post`, not ad-hoc literal
    # `if method == "POST" and path == "/api/..."` blocks.
    disallowed_literal_post_routes = re.findall(
        r'if\s+method\s*==\s*"POST"\s+and\s+path\s*==\s*"/api/[^"]+"',
        source,
    )
    assert disallowed_literal_post_routes == []


def test_ui_router_post_route_schemas_are_explicit_and_non_empty() -> None:
    schemas = MnemosUiRouter.POST_ROUTE_SCHEMAS

    assert schemas
    for path, schema in schemas.items():
        assert path.startswith("/api/")
        assert issubclass(schema, BaseModel)


def test_ui_router_post_route_schema_coverage_has_matching_handler_branches() -> None:
    source = inspect.getsource(MnemosUiRouter._handle_validated_post_route)
    for path in MnemosUiRouter.POST_ROUTE_SCHEMAS:
        assert f'if path == "{path}"' in source


def test_ui_router_serves_memory_detail_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    global_config = tmp_path / "Mnemos" / "mnemos.toml"
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=global_config,
    )
    service.save_settings(
        {
            "llm": {"provider": "mock"},
            "embedding": {"provider": "simple", "dim": 64},
            "storage": {"type": "sqlite", "sqlite_path": str(db_path)},
        },
        scope="global",
    )

    store = SQLiteStore(str(db_path))
    store.store(
        MemoryChunk(
            id="chunk-123",
            content="Use uv for Python package management.",
            metadata={
                "scope": "project",
                "scope_id": "repo-alpha",
                "source": "surprisal_gate",
                "ingest_channel": "manual",
                "encoding_reason": "High surprisal.",
            },
        )
    )
    store.close()

    router = MnemosUiRouter(service)
    response = router.handle("GET", "/api/memory/chunk-123", None)

    assert response.status == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["id"] == "chunk-123"
    assert payload["scope"] == "project"
    assert payload["graph"]["present"] is True
    assert payload["graph"]["neighbor_count"] == 0


def test_ui_router_serves_memory_graph_hooks_in_static_assets(tmp_path: Path) -> None:
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=tmp_path / "Mnemos" / "mnemos.toml",
    )
    router = MnemosUiRouter(service)

    index_response = router.handle("GET", "/", None)
    app_js_response = router.handle("GET", "/app.js", None)

    assert index_response.status == 200
    assert b"vue.global.prod.js" in index_response.body
    assert b"vue-flow-core.iife.js" in index_response.body
    assert b"open-graph-page" in index_response.body
    assert b"graph-page" in index_response.body
    assert b"global-graph-root" in index_response.body
    assert app_js_response.status == 200
    assert b"memory-graph-root" in app_js_response.body
    assert b"memoryGraphRenderer" in app_js_response.body
    assert b"globalMemoryGraphRenderer" in app_js_response.body
    assert b"refreshGlobalGraphPage" in app_js_response.body
    assert b"renderMemoryGraphSection" in app_js_response.body
    assert b"VueFlowCore" in app_js_response.body
    assert b"Graph context unavailable for this memory." in app_js_response.body
    assert b"Graph context is present, but no neighbors surfaced yet." in app_js_response.body


def test_ui_router_serves_global_graph_endpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    global_config = tmp_path / "Mnemos" / "mnemos.toml"
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=global_config,
    )
    service.save_settings(
        {
            "llm": {"provider": "mock"},
            "embedding": {"provider": "simple", "dim": 64},
            "storage": {"type": "sqlite", "sqlite_path": str(db_path)},
        },
        scope="global",
    )

    store = SQLiteStore(str(db_path))
    store.store(MemoryChunk(id="chunk-a", content="alpha node"))
    store.store(MemoryChunk(id="chunk-b", content="beta node"))
    store.replace_graph_neighbors("chunk-a", {"chunk-b": 0.72})
    store.replace_graph_neighbors("chunk-b", {"chunk-a": 0.72})
    store.close()

    router = MnemosUiRouter(service)
    response = router.handle("GET", "/api/graph", None)

    assert response.status == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["node_count"] == 2
    assert payload["edge_count"] == 1
    assert {node["id"] for node in payload["nodes"]} == {"chunk-a", "chunk-b"}
    assert payload["edges"][0]["source"] in {"chunk-a", "chunk-b"}
    assert payload["edges"][0]["target"] in {"chunk-a", "chunk-b"}


def test_ui_router_reports_unavailable_graph_when_memory_is_not_hydrated(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    global_config = tmp_path / "Mnemos" / "mnemos.toml"
    service = ControlPlaneService(
        cwd=tmp_path / "repo",
        home=tmp_path / "home",
        env={},
        global_config_path=global_config,
    )
    service.save_settings(
        {
            "llm": {"provider": "mock"},
            "embedding": {"provider": "simple", "dim": 64},
            "storage": {"type": "sqlite", "sqlite_path": str(db_path)},
        },
        scope="global",
    )

    embedder = SimpleEmbeddingProvider(dim=64)
    store = SQLiteStore(str(db_path))
    store.store(
        MemoryChunk(
            id="chunk-loaded",
            content="Hydrated chunks stay in the startup snapshot.",
            embedding=embedder.embed("Hydrated chunks stay in the startup snapshot."),
        )
    )
    store.store(
        MemoryChunk(
            id="chunk-hidden",
            content="This chunk exists in storage but falls outside the hydration limit.",
            embedding=embedder.embed(
                "This chunk exists in storage but falls outside the hydration limit."
            ),
        )
    )
    store.close()

    def _build_engine(_settings: object) -> MnemosEngine:
        return MnemosEngine(
            config=MnemosConfig(
                surprisal=SurprisalConfig(threshold=0.0, min_content_length=0),
                spreading=SpreadingConfig(startup_hydration_limit=1, startup_auto_connect=False),
            ),
            llm=MockLLMProvider(),
            embedder=embedder,
            store=SQLiteStore(str(db_path)),
        )

    service._build_engine = _build_engine  # type: ignore[method-assign]
    router = MnemosUiRouter(service)
    response = router.handle("GET", "/api/memory/chunk-hidden", None)

    assert response.status == 200
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["id"] == "chunk-hidden"
    assert payload["graph"]["present"] is False
    assert payload["graph"]["neighbor_count"] == 0
