"""Execution context for project-scoped test runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class ContextPathError(ValueError):
    """Raised when a project-scoped path leaves its project boundary."""


@dataclass(frozen=True)
class RunContext:
    """Filesystem and configuration boundary for one project."""

    root: Path
    config_path: Path

    def path(self, *parts: str | Path) -> Path:
        return self.root.joinpath(*parts)

    def resolve(
        self,
        value: str | Path,
        base: str | Path | None = None,
        *,
        label: str = "路径",
    ) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = Path(base or self.root) / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError as exc:
            raise ContextPathError(f"{label}必须位于当前项目内") from exc
        return candidate

    def ensure_inside(self, value: str | Path, *, label: str = "路径") -> Path:
        return self.resolve(value, label=label)

