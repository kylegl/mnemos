"""tests/test_openai_codex_oauth.py — Security and robustness tests for refresh bridge."""

from __future__ import annotations

import asyncio

import pytest

from mnemos.oauth.multicodex_state import MultiCodexAccount
from mnemos.oauth.openai_codex_oauth import OpenAICodexOAuthBridge


@pytest.mark.asyncio
async def test_refresh_raises_on_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyProcess:
        returncode = 0

        async def communicate(self, _payload: bytes) -> tuple[bytes, bytes]:
            return b"not-json", b""

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    bridge = OpenAICodexOAuthBridge(refresh_cmd="refresh-helper")
    account = MultiCodexAccount(email="a@example.com", accessToken="token")

    with pytest.raises(RuntimeError, match="invalid JSON"):
        await bridge.refresh(account)


@pytest.mark.asyncio
async def test_refresh_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _DummyProcess:
        returncode = 0

        async def communicate(self, _payload: bytes) -> tuple[bytes, bytes]:
            await asyncio.sleep(0.05)
            return b"{}", b""

        def kill(self) -> None:
            return None

    async def _fake_create_subprocess_exec(*_args, **_kwargs):
        return _DummyProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    monkeypatch.setenv("MNEMOS_MULTICODEX_REFRESH_TIMEOUT_SECONDS", "0.001")

    bridge = OpenAICodexOAuthBridge(refresh_cmd="refresh-helper")
    account = MultiCodexAccount(email="a@example.com", accessToken="token")

    with pytest.raises(RuntimeError, match="timed out"):
        await bridge.refresh(account)


def test_refresh_cmd_rejects_control_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MNEMOS_MULTICODEX_REFRESH_CMD", raising=False)
    with pytest.raises(ValueError, match="control characters"):
        OpenAICodexOAuthBridge(refresh_cmd="refresh-helper\n--json")
