"""Pydantic v2 models shared across the LogSleuth backend.

These models are intentionally forward-compatible: ``deployment_correlations``
is present on both :class:`Detection` and :class:`AnalysisResult` now (default
empty) even though it is only populated in a later feature (deployment CSV
correlation).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class LogEntry(BaseModel):
    """A single normalized log line, produced by the parsers."""

    timestamp: Optional[datetime] = None
    level: str = "INFO"
    message: str = ""
    raw: str = ""
    source_line: int = 0


class DeploymentCorrelation(BaseModel):
    """A correlation between a detected spike and a nearby deployment event.

    Populated by the deployment-timeline correlation feature (FEAT-003). Kept
    here so the models are forward-compatible.
    """

    service: Optional[str] = None
    version: Optional[str] = None
    deployed_at: Optional[datetime] = None
    delta_seconds: Optional[float] = None
    note: Optional[str] = None


class Detection(BaseModel):
    """A single finding produced by the detection engine."""

    type: str = "ERROR SPIKE"
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    peak_time: Optional[datetime] = None
    error_count: int = 0
    possible_cause: Optional[str] = None
    related_errors: list[str] = Field(default_factory=list)
    suggested_steps: list[str] = Field(default_factory=list)
    matched_signature: Optional[str] = None
    confidence: float = 0.0
    deployment_correlations: list[DeploymentCorrelation] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """The full result of analyzing an uploaded log file."""

    analysis_id: Optional[int] = None
    filename: str = ""
    total_entries: int = 0
    error_count: int = 0
    detections: list[Detection] = Field(default_factory=list)
    deployment_correlations: list[DeploymentCorrelation] = Field(default_factory=list)

    def model_dump_jsonable(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (datetimes as ISO strings)."""
        return self.model_dump(mode="json")
