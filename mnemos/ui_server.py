"""
mnemos/ui_server.py — Local browser UI for Mnemos onboarding and health.
"""

from __future__ import annotations

import json
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

from pydantic import BaseModel, ValidationError

from .control_plane import ControlPlaneService
from .ui_api_models import EmptyObjectRequest, SettingsSaveRequest


@dataclass(slots=True)
class UiResponse:
    status: int
    content_type: str
    body: bytes


MAX_REQUEST_BODY_BYTES = 1_048_576  # 1 MiB hard cap for local control-plane API POST bodies.


def _ui_assets_dir() -> Path:
    return Path(__file__).resolve().parent / "ui"


class MnemosUiRouter:
    POST_ROUTE_SCHEMAS: dict[str, type[BaseModel]] = {
        "/api/settings/global": SettingsSaveRequest,
        "/api/settings/project": SettingsSaveRequest,
        "/api/import": EmptyObjectRequest,
        "/api/smoke": EmptyObjectRequest,
    }

    def __init__(self, service: ControlPlaneService) -> None:
        self.service = service
        self.assets_dir = _ui_assets_dir()

    def _dispatch_validated_post(
        self,
        *,
        payload: dict[str, Any],
        schema: type[BaseModel],
        handler: Callable[[BaseModel], dict[str, Any]],
    ) -> UiResponse:
        try:
            validated = schema.model_validate(payload)
        except ValidationError as exc:
            return self._validation_error_response(exc)
        return self._json_response(handler(validated))

    def _handle_validated_post_route(self, path: str, validated: BaseModel) -> dict[str, Any]:
        if path == "/api/settings/global":
            return self.service.save_settings(
                cast(SettingsSaveRequest, validated).model_dump(mode="python", exclude_none=True),
                scope="global",
            )
        if path == "/api/settings/project":
            return self.service.save_settings(
                cast(SettingsSaveRequest, validated).model_dump(mode="python", exclude_none=True),
                scope="project",
            )
        if path == "/api/import":
            return self.service.import_existing_setup()
        if path == "/api/smoke":
            return self.service.run_smoke_tests()
        raise RuntimeError(f"POST route {path!r} was validated but has no bound handler")

    def _validation_error_response(self, exc: ValidationError) -> UiResponse:
        return self._json_response(
            {
                "error": "Request validation failed.",
                "details": exc.errors(),
            },
            status=HTTPStatus.BAD_REQUEST,
        )

    def _json_response(self, payload: Any, *, status: int = HTTPStatus.OK) -> UiResponse:
        return UiResponse(
            status=int(status),
            content_type="application/json; charset=utf-8",
            body=json.dumps(payload, indent=2).encode("utf-8"),
        )

    def _text_response(
        self,
        text: str,
        *,
        status: int = HTTPStatus.OK,
        content_type: str = "text/plain; charset=utf-8",
    ) -> UiResponse:
        return UiResponse(status=int(status), content_type=content_type, body=text.encode("utf-8"))

    def _asset_response(self, name: str, content_type: str) -> UiResponse:
        path = self.assets_dir / name
        if not path.exists():
            return self._text_response("Not found", status=HTTPStatus.NOT_FOUND)
        return UiResponse(
            status=HTTPStatus.OK,
            content_type=content_type,
            body=path.read_bytes(),
        )

    def handle(self, method: str, raw_path: str, body: bytes | None) -> UiResponse:
        parsed = urlparse(raw_path)
        path = parsed.path

        if method == "GET" and path == "/":
            return self._asset_response("index.html", "text/html; charset=utf-8")
        if method == "GET" and path == "/app.js":
            return self._asset_response("app.js", "application/javascript; charset=utf-8")
        if method == "GET" and path == "/styles.css":
            return self._asset_response("styles.css", "text/css; charset=utf-8")

        if method == "GET" and path == "/api/settings":
            return self._json_response(self.service.get_settings_view())
        if method == "GET" and path == "/api/health":
            return self._json_response(self.service.health_report())
        if method == "GET" and path == "/api/memory":
            return self._json_response(self.service.get_memory_snapshot())
        if method == "GET" and path.startswith("/api/memory/"):
            chunk_id = path.removeprefix("/api/memory/")
            try:
                return self._json_response(self.service.get_memory_detail(chunk_id))
            except KeyError:
                return self._json_response(
                    {"error": f"Memory chunk {chunk_id!r} not found."},
                    status=HTTPStatus.NOT_FOUND,
                )

        payload: dict[str, Any] = {}
        if body:
            try:
                parsed_payload = json.loads(body.decode("utf-8"))
            except JSONDecodeError:
                return self._json_response(
                    {"error": "Invalid JSON payload."},
                    status=HTTPStatus.BAD_REQUEST,
                )
            if not isinstance(parsed_payload, dict):
                return self._json_response(
                    {"error": "JSON payload must be an object."},
                    status=HTTPStatus.BAD_REQUEST,
                )
            payload = parsed_payload

        if method == "POST":
            schema = self.POST_ROUTE_SCHEMAS.get(path)
            if schema is not None:
                return self._dispatch_validated_post(
                    payload=payload,
                    schema=schema,
                    handler=lambda validated: self._handle_validated_post_route(path, validated),
                )

        integration_prefix = "/api/integrations/"
        if method == "POST" and path.startswith(integration_prefix):
            suffix = path[len(integration_prefix) :]
            parts = [part for part in suffix.split("/") if part]
            if len(parts) == 2 and parts[1] in {"preview", "apply"}:
                host_name = parts[0]
                if host_name not in {"claude-code", "cursor", "codex"}:
                    return self._text_response("Unknown host", status=HTTPStatus.NOT_FOUND)
                host = cast(Literal["claude-code", "cursor", "codex"], host_name)
                if parts[1] == "preview":
                    return self._dispatch_validated_post(
                        payload=payload,
                        schema=EmptyObjectRequest,
                        handler=lambda _validated: self.service.preview_integration(host),
                    )
                return self._dispatch_validated_post(
                    payload=payload,
                    schema=EmptyObjectRequest,
                    handler=lambda _validated: self.service.apply_integration(host),
                )

        return self._text_response("Not found", status=HTTPStatus.NOT_FOUND)


class _MnemosUiHandler(BaseHTTPRequestHandler):
    router: MnemosUiRouter

    def do_GET(self) -> None:  # noqa: N802
        response = self.router.handle("GET", self.path, None)
        self._write_response(response)

    def do_POST(self) -> None:  # noqa: N802
        raw_content_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_content_length)
        except ValueError:
            self._write_response(
                UiResponse(
                    status=HTTPStatus.BAD_REQUEST,
                    content_type="application/json; charset=utf-8",
                    body=json.dumps({"error": "Invalid Content-Length header."}).encode("utf-8"),
                )
            )
            return

        if content_length < 0:
            self._write_response(
                UiResponse(
                    status=HTTPStatus.BAD_REQUEST,
                    content_type="application/json; charset=utf-8",
                    body=json.dumps({"error": "Invalid Content-Length header."}).encode("utf-8"),
                )
            )
            return

        if content_length > MAX_REQUEST_BODY_BYTES:
            self._write_response(
                UiResponse(
                    status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    content_type="application/json; charset=utf-8",
                    body=json.dumps(
                        {
                            "error": (
                                f"Request body too large ({content_length} bytes). "
                                f"Limit is {MAX_REQUEST_BODY_BYTES} bytes."
                            )
                        }
                    ).encode("utf-8"),
                )
            )
            return

        body = self.rfile.read(content_length) if content_length > 0 else None
        response = self.router.handle("POST", self.path, body)
        self._write_response(response)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        _ = format, args

    def _write_response(self, response: UiResponse) -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(response.body)


def run_ui_server(
    *,
    service: ControlPlaneService,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    router = MnemosUiRouter(service)

    class Handler(_MnemosUiHandler):
        pass

    Handler.router = router

    server = ThreadingHTTPServer((host, port), Handler)
    actual_port = server.server_address[1]
    url = f"http://{host}:{actual_port}/"
    if open_browser:
        webbrowser.open(url)
    print(f"Mnemos UI running at {url}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
