"""Deployment-timeline correlation.

Parses an optional deployment-timeline CSV and correlates detected error
spikes against nearby deployment events. When a spike's peak time falls within
a configurable window after (or shortly around) a deployment, that deployment
is attached to the :class:`~app.models.Detection` as a
:class:`~app.models.DeploymentCorrelation` and an ordered suggested step like
``"Review deployment at 10:39 AM (checkout-service v1.4.2)"`` is appended.

The CSV is intentionally forgiving about column names: a timestamp column is
required (``timestamp`` / ``time`` / ``deployed_at`` / ``date``) and the
service/version/description columns are matched from a set of common aliases.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from typing import Optional

from .models import Detection, DeploymentCorrelation
from .parsers import _coerce_ts

# Column name aliases (lower-cased) mapped to the logical field.
_TS_KEYS = ("timestamp", "time", "deployed_at", "deploy_time", "date", "datetime", "when")
_SERVICE_KEYS = ("service", "name", "app", "application", "component")
_VERSION_KEYS = ("version", "tag", "release", "sha", "commit", "build")
_NOTE_KEYS = ("description", "note", "notes", "message", "detail", "details", "summary")

DEFAULT_WINDOW_MINUTES = 15


class DeployEvent:
    """A single parsed deployment event from the timeline CSV."""

    def __init__(
        self,
        deployed_at: Optional[datetime],
        service: Optional[str] = None,
        version: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        self.deployed_at = deployed_at
        self.service = service
        self.version = version
        self.note = note

    def label(self) -> str:
        """A short human-readable label for the deployment."""
        parts = [p for p in (self.service, self.version) if p]
        if parts:
            return " ".join(parts)
        if self.note:
            return self.note
        return "deployment"


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        if key in row and row[key] is not None and str(row[key]).strip():
            return str(row[key]).strip()
    return None


def parse_deployments(text: str) -> list[DeployEvent]:
    """Parse a deployment-timeline CSV into :class:`DeployEvent` objects.

    Rows whose timestamp cannot be parsed are skipped. Column names are
    matched case-insensitively against known aliases.
    """
    events: list[DeployEvent] = []
    if not text or not text.strip():
        return events

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        return events

    for raw_row in reader:
        # Normalize keys to lower-case, stripped strings.
        row = {
            (k or "").strip().lower(): (v if v is not None else "")
            for k, v in raw_row.items()
        }
        ts_value = _pick(row, _TS_KEYS)
        deployed_at = _coerce_ts(ts_value) if ts_value else None
        if deployed_at is None:
            # Skip rows without a usable timestamp.
            continue
        events.append(
            DeployEvent(
                deployed_at=deployed_at,
                service=_pick(row, _SERVICE_KEYS),
                version=_pick(row, _VERSION_KEYS),
                note=_pick(row, _NOTE_KEYS),
            )
        )
    return events


def _format_time(dt: datetime) -> str:
    """Format a datetime as ``H:MM AM/PM`` (no leading zero on the hour)."""
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d} {suffix}"


# Time-only log formats (``HH:MM:SS`` / ``HH:MM AM/PM``) parse via strptime to
# the sentinel date 1900-01-01. Deployment CSVs usually carry a full date and
# so land in the real year. Comparing the two directly yields a ~124-year delta
# and every correlation silently fails. When either side is date-less we must
# reconcile them onto a common basis by comparing time-of-day instead.
_SENTINEL_YEAR = 1900


def _is_dateless(dt: datetime) -> bool:
    """True when ``dt`` came from a time-only format (strptime sentinel date)."""
    return dt.year == _SENTINEL_YEAR


def _reconciled_delta(peak: datetime, deployed_at: datetime) -> float:
    """Signed seconds between ``peak`` and ``deployed_at`` on a common basis.

    If both timestamps carry a real date, this is just their difference. If
    either side is date-less (time-only log or deploy), we project both onto a
    shared day and compare time-of-day, so a time-only log still correlates to
    a full-date deploy CSV. The comparison wraps around midnight to the nearest
    direction so spikes just after midnight still match a late-evening deploy.
    """
    if _is_dateless(peak) or _is_dateless(deployed_at):
        peak_seconds = (
            peak.hour * 3600 + peak.minute * 60 + peak.second + peak.microsecond / 1e6
        )
        dep_seconds = (
            deployed_at.hour * 3600
            + deployed_at.minute * 60
            + deployed_at.second
            + deployed_at.microsecond / 1e6
        )
        delta = peak_seconds - dep_seconds
        # Normalize into (-12h, +12h] so midnight wrap picks the closest match.
        day = 86400.0
        if delta > day / 2:
            delta -= day
        elif delta < -day / 2:
            delta += day
        return delta
    return (peak - deployed_at).total_seconds()


def correlate(
    detections: list[Detection],
    deploy_events: list[DeployEvent],
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> list[DeploymentCorrelation]:
    """Attach nearby deployment events to each detection.

    For every detection with a ``peak_time``, deployment events whose time is
    within ``window_minutes`` before the peak (or up to a minute after, to
    tolerate clock skew) are attached as :class:`DeploymentCorrelation` items.
    An ordered ``"Review deployment at <time> (<label>)"`` step is appended to
    the detection's ``suggested_steps``.

    Returns the flat list of all correlations found across detections, so the
    caller can also surface them at the analysis level.
    """
    all_correlations: list[DeploymentCorrelation] = []
    if not deploy_events:
        return all_correlations

    window = timedelta(minutes=window_minutes)
    # Small tolerance for deploys logged slightly after the first errors.
    after_tolerance = timedelta(minutes=1)

    for detection in detections:
        peak = detection.peak_time
        if peak is None:
            continue

        matches: list[tuple[float, DeployEvent]] = []
        for event in deploy_events:
            if event.deployed_at is None:
                continue
            delta = _reconciled_delta(peak, event.deployed_at)
            # Deployment happened before the peak (delta >= 0) within window,
            # or a touch after the peak (within tolerance).
            if -after_tolerance.total_seconds() <= delta <= window.total_seconds():
                matches.append((delta, event))

        # Closest (smallest absolute delta) first, then by time.
        matches.sort(key=lambda m: (abs(m[0]), m[1].deployed_at or datetime.min))

        for delta, event in matches:
            label = event.label()
            note = event.note
            if note is None and event.deployed_at is not None:
                note = f"Deployed {_format_time(event.deployed_at)}"
            correlation = DeploymentCorrelation(
                service=event.service,
                version=event.version,
                deployed_at=event.deployed_at,
                delta_seconds=delta,
                note=note,
            )
            detection.deployment_correlations.append(correlation)
            all_correlations.append(correlation)

            if event.deployed_at is not None:
                step = f"Review deployment at {_format_time(event.deployed_at)} ({label})"
                if step not in detection.suggested_steps:
                    detection.suggested_steps.append(step)

    return all_correlations
