"""Shared monotonic runtime budget for the competition process."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic


class BudgetExceededError(TimeoutError):
    """Raised before work that cannot start inside the remaining budget."""


@dataclass(frozen=True, slots=True)
class RuntimeBudget:
    started_at: float
    deadline: float

    @classmethod
    def start(cls, limit_seconds: float = 1080.0) -> "RuntimeBudget":
        started = monotonic()
        return cls(started_at=started, deadline=started + limit_seconds)

    def elapsed(self) -> float:
        return max(0.0, monotonic() - self.started_at)

    def remaining(self) -> float:
        return max(0.0, self.deadline - monotonic())

    def exhausted(self, reserve_seconds: float = 0.0) -> bool:
        return self.remaining() <= reserve_seconds

    def require(self, stage: str, reserve_seconds: float = 0.0) -> None:
        if self.exhausted(reserve_seconds):
            raise BudgetExceededError(f"runtime budget exhausted before {stage}")

    def network_timeout(self, configured_timeout: float) -> float:
        remaining = self.remaining()
        if remaining <= 0:
            raise BudgetExceededError("runtime budget exhausted before API request")
        return max(0.05, min(configured_timeout, remaining))
