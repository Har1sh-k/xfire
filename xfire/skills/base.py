"""Base skill interface for xfire."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class SkillResult(BaseModel):
    """Result from a skill execution."""

    skill_name: str
    summary: str
    details: dict[str, Any] = {}
    raw_output: str = ""


class BaseSkill(ABC):
    """Abstract base class for all xfire skills.

    Skills are capabilities that provide deeper code understanding.
    They are pre-computed by the orchestrator and included in agent
    review context — agents don't call skills directly.
    """

    name: str = "base"

    @abstractmethod
    def execute(self, repo_dir: str, changed_files: list[str], **kwargs: Any) -> SkillResult:
        """Execute this skill and return results."""
        ...
