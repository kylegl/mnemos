"""mnemos/ui_api_models.py — Request payload schemas for control-plane HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EmptyObjectRequest(BaseModel):
    """Schema for endpoints that accept an empty JSON object payload."""

    model_config = ConfigDict(extra="forbid")


class OnboardingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["dev", "pro"] | None = None
    preferred_host: Literal["claude-code", "cursor", "codex"] | None = None


class LLMPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal[
        "mock",
        "ollama",
        "openai",
        "openclaw",
        "openrouter",
        "multicodex",
    ] | None = None
    model: str | None = None


class EmbeddingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["simple", "ollama", "openai", "openclaw", "openrouter"] | None = None
    model: str | None = None
    dim: int | None = Field(default=None, ge=1)


class StoragePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["memory", "sqlite"] | None = None
    sqlite_path: str | None = None


class ProviderEntryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str | None = None
    base_url: str | None = None


class ProvidersPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openai: ProviderEntryPayload | None = None
    openclaw: ProviderEntryPayload | None = None
    openrouter: ProviderEntryPayload | None = None
    ollama: ProviderEntryPayload | None = None
    multicodex: ProviderEntryPayload | None = None


class SettingsSaveRequest(BaseModel):
    """Strict schema for /api/settings/{global|project} write payloads."""

    model_config = ConfigDict(extra="forbid")

    onboarding: OnboardingPayload | None = None
    llm: LLMPayload | None = None
    embedding: EmbeddingPayload | None = None
    storage: StoragePayload | None = None
    providers: ProvidersPayload | None = None

    @model_validator(mode="after")
    def _at_least_one_section(self) -> "SettingsSaveRequest":
        if (
            self.onboarding is None
            and self.llm is None
            and self.embedding is None
            and self.storage is None
            and self.providers is None
        ):
            raise ValueError("Request must include at least one settings section.")
        return self
