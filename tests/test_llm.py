"""
tests/test_llm.py -- Tests for production LLM providers.
"""

from __future__ import annotations

import httpx
import pytest

from mnemos.oauth.multicodex_state import MultiCodexAccount
from mnemos.utils import llm as llm_module


@pytest.mark.asyncio
async def test_openai_provider_retries_openrouter_with_fallback_key_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self._status_code = status_code
            self._payload = payload

        def raise_for_status(self) -> None:
            if self._status_code < 400:
                return None
            request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
            response = httpx.Response(self._status_code, request=request)
            raise httpx.HTTPStatusError(
                f"status {self._status_code}",
                request=request,
                response=response,
            )

        def json(self) -> dict[str, object]:
            return self._payload

    class _DummyAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_DummyAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = exc_type, exc, tb
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, object],
            headers: dict[str, str],
        ) -> _DummyResponse:
            _ = url, json, self.timeout
            calls.append(headers["Authorization"])
            if headers["Authorization"] == "Bearer stale-key":
                return _DummyResponse(401, {})
            return _DummyResponse(
                200,
                {"choices": [{"message": {"content": "ok"}}]},
            )

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", _DummyAsyncClient)

    provider = llm_module.OpenAIProvider(
        api_key="stale-key",
        api_key_fallback="fresh-key",
        model="openrouter/auto",
        base_url="https://openrouter.ai/api/v1",
    )

    result = await provider.predict("memory test")

    assert result == "ok"
    assert calls == ["Bearer stale-key", "Bearer fresh-key"]
    assert provider.api_key == "fresh-key"


@pytest.mark.asyncio
async def test_multicodex_provider_rotates_account_on_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAccountManager:
        def __init__(self) -> None:
            self._accounts = [
                MultiCodexAccount(email="a@example.com", accessToken="token-a", accountId="acct-a"),
                MultiCodexAccount(email="b@example.com", accessToken="token-b", accountId="acct-b"),
            ]
            self.marked: list[str] = []

        async def resolve_account(self, *, exclude_emails: set[str] | None = None) -> MultiCodexAccount:
            excluded = exclude_emails or set()
            for account in self._accounts:
                if account.email not in excluded:
                    return account
            raise RuntimeError("no account")

        async def mark_quota_exhausted(self, email: str, *, cooldown_seconds: int | None = None) -> None:
            _ = cooldown_seconds
            self.marked.append(email)

        async def force_refresh(self, email: str) -> MultiCodexAccount | None:
            _ = email
            return None

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses")
                response = httpx.Response(self.status_code, request=request)
                raise httpx.HTTPStatusError("error", request=request, response=response)

        def json(self) -> dict[str, object]:
            return self._payload

        @property
        def text(self) -> str:
            value = self._payload.get("sse")
            return value if isinstance(value, str) else ""

    calls: list[str] = []

    class _DummyAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_DummyAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = exc_type, exc, tb
            return None

        async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]) -> _DummyResponse:
            _ = url, json, self.timeout
            calls.append(headers["Authorization"])
            if headers["Authorization"] == "Bearer token-a":
                return _DummyResponse(429, {})
            return _DummyResponse(
                200,
                {
                    "sse": (
                        'event: response.output_text.delta\n'
                        'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                    )
                },
            )

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", _DummyAsyncClient)

    manager = _FakeAccountManager()
    provider = llm_module.MultiCodexProvider(account_manager=manager)

    result = await provider.predict("rotate")

    assert result == "ok"
    assert calls == ["Bearer token-a", "Bearer token-b"]
    assert manager.marked == ["a@example.com"]


@pytest.mark.asyncio
async def test_multicodex_provider_refreshes_same_account_on_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeAccountManager:
        def __init__(self) -> None:
            self.account = MultiCodexAccount(
                email="a@example.com",
                accessToken="stale-token",
                accountId="acct-a",
            )

        async def resolve_account(self, *, exclude_emails: set[str] | None = None) -> MultiCodexAccount:
            _ = exclude_emails
            return self.account

        async def mark_quota_exhausted(self, email: str, *, cooldown_seconds: int | None = None) -> None:
            _ = email, cooldown_seconds

        async def force_refresh(self, email: str) -> MultiCodexAccount | None:
            _ = email
            self.account = MultiCodexAccount(
                email="a@example.com",
                accessToken="fresh-token",
                accountId="acct-a",
            )
            return self.account

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict[str, object]) -> None:
            self.status_code = status_code
            self._payload = payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses")
                response = httpx.Response(self.status_code, request=request)
                raise httpx.HTTPStatusError("error", request=request, response=response)

        def json(self) -> dict[str, object]:
            return self._payload

        @property
        def text(self) -> str:
            value = self._payload.get("sse")
            return value if isinstance(value, str) else ""

    calls: list[str] = []

    class _DummyAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "_DummyAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            _ = exc_type, exc, tb
            return None

        async def post(self, url: str, *, json: dict[str, object], headers: dict[str, str]) -> _DummyResponse:
            _ = url, json, self.timeout
            calls.append(headers["Authorization"])
            if headers["Authorization"] == "Bearer stale-token":
                return _DummyResponse(401, {})
            return _DummyResponse(
                200,
                {
                    "sse": (
                        'event: response.output_text.delta\n'
                        'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
                    )
                },
            )

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", _DummyAsyncClient)

    provider = llm_module.MultiCodexProvider(account_manager=_FakeAccountManager())

    result = await provider.predict("refresh")

    assert result == "ok"
    assert calls == ["Bearer stale-token", "Bearer fresh-token"]
