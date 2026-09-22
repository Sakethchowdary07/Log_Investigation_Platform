"""Spike detection + cause inference engine.

ERROR/CRITICAL/FATAL entries are bucketed into fixed time windows. A window is
flagged as an ERROR SPIKE when its error count meets a threshold. For each
spike the knowledge base is run over the window's messages to infer a possible
cause, related errors, and ordered suggested steps.

If the entries carry no usable timestamps we fall back to a single count-based
window so detection still works.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from .knowledge_base import Analyzer, KnowledgeBase, RuleBasedAnalyzer
from .models import AnalysisResult, Detection

ERROR_LEVELS = {"ERROR", "CRITICAL", "FATAL"}

DEFAULT_WINDOW_SECONDS = 60
DEFAULT_THRESHOLD = 5


def _is_error(level: str) -> bool:
    return level.upper() in ERROR_LEVELS


class SpikeDetector:
    def __init__(
        self,
        analyzer: Optional[Analyzer] = None,
        kb: Optional[KnowledgeBase] = None,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        threshold: int = DEFAULT_THRESHOLD,
    ):
        if analyzer is None:
            if kb is None:
                kb = KnowledgeBase.load()
            analyzer = RuleBasedAnalyzer(kb)
        self.analyzer = analyzer
        self.window_seconds = window_seconds
        self.threshold = threshold

    def _spike_windows(self, errors_with_ts):
        """Find spike windows using a sliding window over error timestamps.

        Fixed wall-clock buckets can split a single burst across a boundary so
        that neither half reaches the threshold and the spike is missed. A
        sliding window instead considers every ``window_seconds`` interval that
        begins at an actual error: any such interval containing at least
        ``threshold`` errors is a candidate spike. Overlapping/adjacent
        candidate intervals are then merged into a single detection so one
        burst is reported once (with all its errors), regardless of where it
        falls relative to a minute boundary.

        Yields ``(window_start, window_end, bucket)`` tuples in chronological
        order, where ``bucket`` is the list of error entries in the window.
        """
        ordered = sorted(errors_with_ts, key=lambda e: e.timestamp)
        span = timedelta(seconds=self.window_seconds)

        # Collect candidate intervals: each starts at an error timestamp and
        # spans window_seconds; keep those meeting the threshold.
        candidates: list[tuple[datetime, datetime]] = []
        n = len(ordered)
        for i in range(n):
            start = ordered[i].timestamp
            end = start + span
            count = 0
            for j in range(i, n):
                if ordered[j].timestamp < end:
                    count += 1
                else:
                    break
            if count >= self.threshold:
                candidates.append((start, end))

        if not candidates:
            return

        # Merge overlapping/touching candidate intervals into spikes.
        merged: list[tuple[datetime, datetime]] = []
        cur_start, cur_end = candidates[0]
        for start, end in candidates[1:]:
            if start <= cur_end:
                cur_end = max(cur_end, end)
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = start, end
        merged.append((cur_start, cur_end))

        for start, end in merged:
            bucket = [e for e in ordered if start <= e.timestamp < end]
            yield start, end, bucket

    def detect(self, entries, filename: str = "") -> AnalysisResult:
        total = len(entries)
        errors = [e for e in entries if _is_error(e.level)]
        error_count = len(errors)

        result = AnalysisResult(
            filename=filename,
            total_entries=total,
            error_count=error_count,
        )

        errors_with_ts = [e for e in errors if e.timestamp is not None]

        if errors_with_ts:
            for window_start, window_end, bucket in self._spike_windows(errors_with_ts):
                detection = self._build_detection(
                    bucket,
                    window_start=window_start,
                    window_end=window_end,
                )
                result.detections.append(detection)
        else:
            # No timestamps available: fall back to a single count-based window.
            if error_count >= self.threshold:
                detection = self._build_detection(errors, window_start=None, window_end=None)
                result.detections.append(detection)

        return result

    def _build_detection(self, bucket, window_start, window_end) -> Detection:
        messages = [e.message for e in bucket]
        peak_time = _peak_time(bucket)

        detection = Detection(
            type="ERROR SPIKE",
            window_start=window_start,
            window_end=window_end,
            peak_time=peak_time,
            error_count=len(bucket),
        )

        match = self.analyzer.analyze(messages)
        if match is not None:
            rule = match.rule
            detection.possible_cause = rule.possible_cause
            detection.related_errors = list(rule.related_errors)
            detection.suggested_steps = list(rule.suggested_steps)
            detection.matched_signature = rule.id
            # Confidence: share of window messages that matched, capped at 1.0.
            detection.confidence = round(min(1.0, match.match_count / max(1, len(bucket))), 3)
        return detection


def _peak_time(bucket) -> Optional[datetime]:
    """Pick the timestamp with the most errors (the busiest second)."""
    timed = [e for e in bucket if e.timestamp is not None]
    if not timed:
        return None
    counts: dict[datetime, int] = defaultdict(int)
    for e in timed:
        counts[e.timestamp] += 1
    # Highest count wins; on ties prefer the earliest second (the spike's
    # onset) rather than its tail, which is the more useful "peak" to show an
    # operator. Negating the epoch makes the earliest timestamp sort largest.
    return max(counts, key=lambda t: (counts[t], -t.timestamp()))


def analyze_text(
    entries,
    filename: str = "",
    kb: Optional[KnowledgeBase] = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
    threshold: int = DEFAULT_THRESHOLD,
) -> AnalysisResult:
    """Convenience wrapper: build a detector and run it over entries."""
    detector = SpikeDetector(
        kb=kb,
        window_seconds=window_seconds,
        threshold=threshold,
    )
    return detector.detect(entries, filename=filename)
