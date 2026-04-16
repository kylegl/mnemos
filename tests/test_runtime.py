"""
tests/test_runtime.py — Tests for runtime env configuration helpers.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

import mnemos.runtime as runtime_module
from mnemos.runtime import (
    build_embedder_from_env,
    build_llm_from_env,
    build_store_from_env,
    resolve_env_value,
)
from mnemos.utils import (
    OllamaEmbeddingProvider,
    OllamaProvider,
    OpenAIEmbeddingProvider,
    SimpleEmbeddingProvider,
    SQLiteStore,
    MultiCodexProvider,
)


def test_resolve_env_value_supports_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MNEMOS_STORE_TYPE", raising=False)
    monkeypatch.setenv("MNEMOS_STORAGE", "sqlite")

    value = resolve_env_value(
        "MNEMOS_STORE_TYPE",
        default="memory",
        aliases=("MNEMOS_STORAGE",),
    )

    assert value == "sqlite"


def test_build_store_from_env_supports_db_path_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "mnemos_alias.db"
    monkeypatch.delenv("MNEMOS_STORE_TYPE", raising=False)
    monkeypatch.delenv("MNEMOS_SQLITE_PATH", raising=False)
    monkeypatch.setenv("MNEMOS_STORAGE", "sqlite")
    monkeypatch.setenv("MNEMOS_DB_PATH", str(db_path))

    store = build_store_from_env(default_store_type="memory")

    assert isinstance(store, SQLiteStore)
    assert store.db_path == str(db_path)
    store.close()


def test_build_store_from_env_defaults_to_sqlite_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "mnemos_default.db"
    monkeypatch.delenv("MNEMOS_STORE_TYPE", raising=False)
    monkeypatch.setenv("MNEMOS_SQLITE_PATH", str(db_path))

    store = build_store_from_env(default_store_type="sqlite")

    assert isinstance(store, SQLiteStore)
    assert store.db_path == str(db_path)
    store.close()


def test_build_embedder_from_env_defaults_to_simple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MNEMOS_EMBEDDING_PROVIDER", raising=False)
    embedder = build_embedder_from_env(default_provider="simple")
    assert isinstance(embedder, SimpleEmbeddingProvider)


def test_build_embedder_from_env_rejects_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "does-not-exist")
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        build_embedder_from_env(default_provider="simple")


def test_build_embedder_from_env_requires_api_key_for_openai(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("MNEMOS_OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MNEMOS_OPENAI_API_KEY"):
        build_embedder_from_env(default_provider="simple")


def test_build_embedder_from_env_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("MNEMOS_OPENAI_API_KEY", "dummy-key")
    embedder = build_embedder_from_env(default_provider="simple")
    assert isinstance(embedder, OpenAIEmbeddingProvider)


def test_build_embedder_from_env_openclaw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "openclaw")
    monkeypatch.setenv("MNEMOS_OPENCLAW_API_KEY", "claw-key")
    monkeypatch.setenv("MNEMOS_OPENCLAW_URL", "https://api.openclaw.example/v1")

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OpenAIEmbeddingProvider)
    assert embedder.api_key == "claw-key"
    assert embedder.base_url == "https://api.openclaw.example/v1"


def test_build_embedder_from_env_infers_openai_from_llm_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MNEMOS_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("MNEMOS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("MNEMOS_OPENAI_API_KEY", "dummy-key")

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OpenAIEmbeddingProvider)


def test_build_embedder_from_env_infers_ollama_from_llm_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MNEMOS_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.setenv("MNEMOS_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http://localhost:11434")

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OllamaEmbeddingProvider)


def test_build_llm_from_env_multicodex(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MNEMOS_LLM_PROVIDER", "multicodex")
    monkeypatch.setenv("MNEMOS_MULTICODEX_STATE_FILE", str(tmp_path / "multicodex.json"))
    monkeypatch.setenv("MNEMOS_MULTICODEX_URL", "https://chatgpt.com/backend-api")

    provider = build_llm_from_env()

    assert isinstance(provider, MultiCodexProvider)
    assert provider.base_url == "https://chatgpt.com/backend-api"


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://localhost:11434", True),
        ("http://127.0.0.1:11434", True),
        ("http://[::1]:11434", True),
        ("http://0.0.0.0:11434", True),
        ("http://10.0.0.5:11434", False),
        ("https://ollama.internal:11434", False),
        ("http:///no-host", False),
    ],
)
def test_is_local_ollama_url_matrix(base_url: str, expected: bool) -> None:
    assert runtime_module._is_local_ollama_url(base_url) is expected


def test_build_embedder_from_env_attempts_local_ollama_autostart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http://127.0.0.1:11434")

    ready_checks: list[tuple[str, float]] = []
    popen_calls: list[tuple[list[str], dict[str, str] | None]] = []

    def fake_tcp_ready(base_url: str, *, timeout: float = 0.2) -> bool:
        ready_checks.append((base_url, timeout))
        return False

    class _DummyPopen:
        def __init__(self, args: list[str], env: dict[str, str] | None) -> None:
            popen_calls.append((args, env))

    def fake_popen(args: list[str], **kwargs: object) -> _DummyPopen:
        env = kwargs.get("env")
        assert env is None or isinstance(env, dict)
        return _DummyPopen(args, env)

    monkeypatch.setattr(runtime_module, "_ollama_tcp_ready", fake_tcp_ready)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    monotonic_state = {"now": 0.0}

    def fake_monotonic() -> float:
        monotonic_state["now"] += 0.5
        return monotonic_state["now"]

    monkeypatch.setattr(runtime_module.time, "monotonic", fake_monotonic)

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OllamaEmbeddingProvider)
    assert ready_checks, "expected local readiness checks"
    assert popen_calls, "expected autostart attempt"
    args, env = popen_calls[0]
    assert args == ["ollama", "serve"]
    assert env is not None
    assert env.get("OLLAMA_HOST") == "http://127.0.0.1:11434"


def test_build_embedder_from_env_does_not_autostart_remote_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http://10.0.0.5:11434")

    popen_called = {"value": False}

    def fail_popen(*_args: object, **_kwargs: object) -> None:
        popen_called["value"] = True
        raise AssertionError("autostart should not run for non-local ollama URLs")

    monkeypatch.setattr(runtime_module.subprocess, "Popen", fail_popen)

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OllamaEmbeddingProvider)
    assert not popen_called["value"]


def test_build_embedder_from_env_localhost_ollama_uses_local_autostart_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http://localhost:11434")

    checks: list[str] = []

    def fake_tcp_ready(base_url: str, *, timeout: float = 0.2) -> bool:
        host = urlparse(base_url).hostname or ""
        checks.append(host)
        return True

    monkeypatch.setattr(runtime_module, "_ollama_tcp_ready", fake_tcp_ready)

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OllamaEmbeddingProvider)
    assert checks == ["localhost"]


def test_build_embedder_from_env_does_not_spawn_when_local_ollama_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http://127.0.0.1:11434")

    popen_called = {"value": False}

    def fake_ready(_base_url: str, *, timeout: float = 0.2) -> bool:
        return True

    def fail_popen(*_args: object, **_kwargs: object) -> None:
        popen_called["value"] = True
        raise AssertionError("autostart should not run when local ollama is already ready")

    monkeypatch.setattr(runtime_module, "_ollama_tcp_ready", fake_ready)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", fail_popen)

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OllamaEmbeddingProvider)
    assert not popen_called["value"]


def test_build_embedder_from_env_tolerates_popen_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http://127.0.0.1:11434")

    def fake_ready(_base_url: str, *, timeout: float = 0.2) -> bool:
        return False

    def raise_oserror(*_args: object, **_kwargs: object) -> None:
        raise OSError("ollama binary not found")

    monkeypatch.setattr(runtime_module, "_ollama_tcp_ready", fake_ready)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", raise_oserror)

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OllamaEmbeddingProvider)


def test_build_embedder_from_env_malformed_ollama_url_does_not_autostart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http:///no-host")

    popen_called = {"value": False}

    def fail_popen(*_args: object, **_kwargs: object) -> None:
        popen_called["value"] = True
        raise AssertionError("autostart should not run for malformed ollama URLs")

    monkeypatch.setattr(runtime_module.subprocess, "Popen", fail_popen)

    embedder = build_embedder_from_env(default_provider="simple")

    assert isinstance(embedder, OllamaEmbeddingProvider)
    assert not popen_called["value"]


def test_build_llm_from_env_attempts_local_ollama_autostart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http://127.0.0.1:11434")

    popen_calls: list[list[str]] = []

    def fake_tcp_ready(_base_url: str, *, timeout: float = 0.2) -> bool:
        return False

    class _DummyPopen:
        def __init__(self, args: list[str]) -> None:
            popen_calls.append(args)

    def fake_popen(args: list[str], **_kwargs: object) -> _DummyPopen:
        return _DummyPopen(args)

    monkeypatch.setattr(runtime_module, "_ollama_tcp_ready", fake_tcp_ready)
    monkeypatch.setattr(runtime_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    monotonic_state = {"now": 0.0}

    def fake_monotonic() -> float:
        monotonic_state["now"] += 0.5
        return monotonic_state["now"]

    monkeypatch.setattr(runtime_module.time, "monotonic", fake_monotonic)

    llm = build_llm_from_env()

    assert isinstance(llm, OllamaProvider)
    assert popen_calls == [["ollama", "serve"]]


def test_build_llm_from_env_does_not_autostart_remote_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("MNEMOS_OLLAMA_URL", "http://10.0.0.5:11434")

    popen_called = {"value": False}

    def fail_popen(*_args: object, **_kwargs: object) -> None:
        popen_called["value"] = True
        raise AssertionError("autostart should not run for remote ollama URLs")

    monkeypatch.setattr(runtime_module.subprocess, "Popen", fail_popen)

    llm = build_llm_from_env()

    assert isinstance(llm, OllamaProvider)
    assert not popen_called["value"]


def test_build_store_from_env_rejects_legacy_store_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MNEMOS_STORE_TYPE", "qdrant")
    with pytest.raises(Exception, match="storage.type"):
        build_store_from_env(default_store_type="memory")

    monkeypatch.setenv("MNEMOS_STORE_TYPE", "neo4j")
    with pytest.raises(Exception, match="storage.type"):
        build_store_from_env(default_store_type="memory")
