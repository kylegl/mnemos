"""
tests/test_ui_server.py — Local UI router tests.
"""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

from mnemos.control_plane import ControlPlaneService
from mnemos.types import MemoryChunk
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
