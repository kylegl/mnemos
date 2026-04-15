"""Shared MultiCodex account state file parsing and persistence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator


_DATETIME_FIELDS = ("expiresAt", "lastUsed", "quotaExhaustedUntil")


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        numeric = float(value)
        # MultiCodex commonly stores epoch milliseconds.
        if numeric >= 1_000_000_000_000:
            numeric = numeric / 1000.0
        return datetime.fromtimestamp(numeric, tz=timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    raise ValueError(f"Unsupported datetime value: {value!r}")


def _serialize_datetime(value: datetime | None) -> int | None:
    if value is None:
        return None
    normalized = value.astimezone(timezone.utc)
    return int(normalized.timestamp() * 1000)


class MultiCodexAccount(BaseModel):
    """OAuth/session account entry from the shared MultiCodex state file."""

    model_config = ConfigDict(extra="ignore")

    email: str
    accessToken: str
    refreshToken: str | None = None
    expiresAt: datetime | None = None
    accountId: str | None = None
    lastUsed: datetime | None = None
    quotaExhaustedUntil: datetime | None = None

    @field_validator(*_DATETIME_FIELDS, mode="before")
    @classmethod
    def _validate_datetime(cls, value: Any) -> datetime | None:
        return _parse_datetime(value)


class MultiCodexState(BaseModel):
    """Top-level shared state payload used by MultiCodex-compatible clients."""

    model_config = ConfigDict(extra="ignore")

    schemaVersion: int = 1
    accounts: list[MultiCodexAccount] = Field(default_factory=list)
    activeEmail: str | None = None


class MultiCodexStateStore:
    """Read/write helper for the canonical shared state file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> MultiCodexState:
        if not self.path.exists():
            return MultiCodexState(schemaVersion=1, accounts=[], activeEmail=None)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("MultiCodex state file must contain a JSON object")
        return MultiCodexState.model_validate(dict(data))

    def save(self, state: MultiCodexState) -> None:
        payload = state.model_dump(mode="python")
        for account in payload.get("accounts", []):
            if not isinstance(account, dict):
                continue
            for field_name in _DATETIME_FIELDS:
                account[field_name] = _serialize_datetime(account.get(field_name))
        temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self.path)


def default_multicodex_state_file() -> str:
    return "~/.pi/agent/multicodex.json"
