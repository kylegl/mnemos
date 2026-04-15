from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_user_mnemos_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Prevent local developer Mnemos config from impacting test runs.

    CI won't have a developer-local `.mnemos/mnemos.toml`, but local test runs
    might. We set an explicit config path so runtime builders don't consult
    OS/user-global config locations.
    """

    monkeypatch.setenv("MNEMOS_CONFIG_PATH", str(tmp_path / "global-mnemos.toml"))
