"""Pluggable OpenAI Codex OAuth refresh bridge for MultiCodex accounts."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from collections import defaultdict
from json import JSONDecodeError
from typing import Any

from .multicodex_state import MultiCodexAccount


class OpenAICodexOAuthBridge:
    """Refresh bridge that optionally delegates to a command handler."""

    _DEFAULT_TIMEOUT_SECONDS = 30.0
    _MAX_STDOUT_BYTES = 1_048_576
    _MAX_STDERR_BYTES = 131_072

    def __init__(self, refresh_cmd: str | None = None) -> None:
        env_cmd = os.getenv("MNEMOS_MULTICODEX_REFRESH_CMD")
        configured = (refresh_cmd if refresh_cmd is not None else env_cmd) or None
        if configured is not None:
            stripped = configured.strip()
            if "\x00" in stripped or "\n" in stripped or "\r" in stripped:
                raise ValueError("MNEMOS_MULTICODEX_REFRESH_CMD contains invalid control characters")
            configured = stripped or None
        self.refresh_cmd = configured
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @property
    def can_refresh(self) -> bool:
        return self.refresh_cmd not in (None, "")

    async def refresh(self, account: MultiCodexAccount) -> MultiCodexAccount | None:
        if not self.can_refresh:
            return None

        lock = self._locks[account.email]
        async with lock:
            refreshed = await self._run_refresh_command(account)
            if refreshed is None:
                return None
            merged = account.model_dump(mode="python")
            merged.update(refreshed)
            merged["email"] = account.email
            return MultiCodexAccount.model_validate(merged)

    async def _run_refresh_command(self, account: MultiCodexAccount) -> dict[str, Any] | None:
        if self.refresh_cmd in (None, ""):
            return None

        command = shlex.split(self.refresh_cmd)
        if not command:
            return None

        payload = json.dumps({"account": account.model_dump(mode="json")}).encode("utf-8")
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout_seconds_raw = os.getenv("MNEMOS_MULTICODEX_REFRESH_TIMEOUT_SECONDS", "")
        try:
            timeout_seconds = (
                float(timeout_seconds_raw)
                if timeout_seconds_raw not in ("", None)
                else self._DEFAULT_TIMEOUT_SECONDS
            )
        except ValueError:
            timeout_seconds = self._DEFAULT_TIMEOUT_SECONDS

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(payload), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate(b"")
            raise RuntimeError(
                "MultiCodex refresh command timed out "
                f"after {timeout_seconds:.2f} seconds"
            ) from exc

        if len(stdout) > self._MAX_STDOUT_BYTES:
            raise RuntimeError(
                "MultiCodex refresh command stdout exceeded safety limit "
                f"({len(stdout)} bytes > {self._MAX_STDOUT_BYTES})"
            )
        if len(stderr) > self._MAX_STDERR_BYTES:
            raise RuntimeError(
                "MultiCodex refresh command stderr exceeded safety limit "
                f"({len(stderr)} bytes > {self._MAX_STDERR_BYTES})"
            )

        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"MultiCodex refresh command failed with exit code {process.returncode}: {stderr_text}"
            )

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        if not stdout_text:
            raise RuntimeError("MultiCodex refresh command returned empty output")

        try:
            parsed = json.loads(stdout_text)
        except JSONDecodeError as exc:
            raise RuntimeError("MultiCodex refresh command returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("MultiCodex refresh command must return a JSON object")

        account_payload = parsed.get("account") if "account" in parsed else parsed
        if not isinstance(account_payload, dict):
            raise RuntimeError("MultiCodex refresh output account payload must be a JSON object")
        return dict(account_payload)
