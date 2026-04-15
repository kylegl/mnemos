"""Account lifecycle orchestration for MultiCodex-backed OAuth sessions."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..utils.reliability import MnemosConfigurationError
from .multicodex_state import (
    MultiCodexAccount,
    MultiCodexState,
    MultiCodexStateStore,
    default_multicodex_state_file,
)
from .openai_codex_oauth import OpenAICodexOAuthBridge
from .selection import select_account


class MultiCodexAccountManager:
    """Select, refresh, and persist shared MultiCodex account state."""

    def __init__(
        self,
        *,
        state_file: str | Path | None = None,
        refresh_cmd: str | None = None,
        quota_cooldown_seconds: int = 1800,
        token_validity_threshold_seconds: int = 300,
    ) -> None:
        resolved_state_file = (
            state_file
            if state_file is not None
            else os.getenv("MNEMOS_MULTICODEX_STATE_FILE", default_multicodex_state_file())
        )
        self.state_store = MultiCodexStateStore(resolved_state_file)
        self.refresh_bridge = OpenAICodexOAuthBridge(refresh_cmd=refresh_cmd)
        self.quota_cooldown_seconds = quota_cooldown_seconds
        self.token_validity_threshold = timedelta(seconds=token_validity_threshold_seconds)
        self._state_lock = asyncio.Lock()

    @property
    def state_file(self) -> Path:
        return self.state_store.path

    @property
    def can_refresh(self) -> bool:
        return self.refresh_bridge.can_refresh

    def load_state(self) -> MultiCodexState:
        return self.state_store.load()

    def _save_state(self, state: MultiCodexState) -> None:
        self.state_store.save(state)

    def _is_token_valid(self, account: MultiCodexAccount, now: datetime) -> bool:
        if not account.accessToken:
            return False
        if account.expiresAt is None:
            return True
        return account.expiresAt - now >= self.token_validity_threshold

    async def resolve_account(
        self,
        *,
        exclude_emails: set[str] | None = None,
        force_refresh: bool = False,
    ) -> MultiCodexAccount:
        async with self._state_lock:
            state = self.load_state()
            current = datetime.now(timezone.utc)
            selected = select_account(
                state.accounts,
                active_email=state.activeEmail,
                now=current,
                exclude_emails=exclude_emails,
            )
            if selected is None:
                raise MnemosConfigurationError(
                    "No eligible MultiCodex accounts found in shared state file"
                )

            account = selected
            if force_refresh or not self._is_token_valid(account, current):
                refreshed = await self.refresh_bridge.refresh(account)
                if refreshed is None and force_refresh:
                    raise MnemosConfigurationError(
                        "MultiCodex refresh was requested but no refresh bridge is configured"
                    )
                if refreshed is not None:
                    account = refreshed
                    self._replace_account(state, account)
                    current = datetime.now(timezone.utc)

            if not self._is_token_valid(account, current):
                raise MnemosConfigurationError(
                    f"MultiCodex account {account.email} token expired and could not be refreshed"
                )

            account.lastUsed = current
            self._replace_account(state, account)
            self._save_state(state)
            return account

    async def force_refresh(self, email: str) -> MultiCodexAccount | None:
        async with self._state_lock:
            state = self.load_state()
            account = self._find_account(state, email)
            if account is None:
                return None
            refreshed = await self.refresh_bridge.refresh(account)
            if refreshed is None:
                return None
            refreshed.lastUsed = datetime.now(timezone.utc)
            self._replace_account(state, refreshed)
            self._save_state(state)
            return refreshed

    async def mark_quota_exhausted(
        self,
        email: str,
        *,
        cooldown_seconds: int | None = None,
    ) -> None:
        async with self._state_lock:
            state = self.load_state()
            account = self._find_account(state, email)
            if account is None:
                return
            duration_seconds = (
                self.quota_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
            )
            account.quotaExhaustedUntil = datetime.now(timezone.utc) + timedelta(
                seconds=duration_seconds
            )
            self._replace_account(state, account)
            self._save_state(state)

    async def mark_last_used(self, email: str) -> None:
        async with self._state_lock:
            state = self.load_state()
            account = self._find_account(state, email)
            if account is None:
                return
            account.lastUsed = datetime.now(timezone.utc)
            self._replace_account(state, account)
            self._save_state(state)

    @staticmethod
    def _find_account(state: MultiCodexState, email: str) -> MultiCodexAccount | None:
        for account in state.accounts:
            if account.email == email:
                return account
        return None

    @staticmethod
    def _replace_account(state: MultiCodexState, updated: MultiCodexAccount) -> None:
        for index, account in enumerate(state.accounts):
            if account.email == updated.email:
                state.accounts[index] = updated
                return
        state.accounts.append(updated)
