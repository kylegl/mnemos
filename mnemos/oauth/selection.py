"""Account selection logic for MultiCodex-backed provider routing."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from .multicodex_state import MultiCodexAccount


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def eligible_accounts(
    accounts: list[MultiCodexAccount],
    *,
    now: datetime | None = None,
    exclude_emails: set[str] | None = None,
) -> list[MultiCodexAccount]:
    current = _utc_now() if now is None else now
    excluded = exclude_emails or set()
    eligible: list[MultiCodexAccount] = []
    for account in accounts:
        if account.email in excluded:
            continue
        exhausted_until = account.quotaExhaustedUntil
        if exhausted_until is not None and exhausted_until > current:
            continue
        eligible.append(account)
    return eligible


def select_account(
    accounts: list[MultiCodexAccount],
    *,
    active_email: str | None,
    now: datetime | None = None,
    exclude_emails: set[str] | None = None,
    rng: random.Random | None = None,
) -> MultiCodexAccount | None:
    current = _utc_now() if now is None else now
    randomizer = rng or random
    eligible = eligible_accounts(accounts, now=current, exclude_emails=exclude_emails)
    if not eligible:
        return None

    if active_email:
        for account in eligible:
            if account.email == active_email:
                return account

    untouched = [account for account in eligible if account.lastUsed is None]
    candidates = untouched if untouched else eligible

    with_resets = [account for account in candidates if account.quotaExhaustedUntil is not None]
    if with_resets:
        earliest_reset = min(
            account.quotaExhaustedUntil for account in with_resets if account.quotaExhaustedUntil
        )
        assert earliest_reset is not None
        earliest = [
            account for account in with_resets if account.quotaExhaustedUntil == earliest_reset
        ]
        return randomizer.choice(earliest)

    return randomizer.choice(candidates)
